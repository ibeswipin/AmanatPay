import base64
import re
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Iterable, Optional

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import transaction
from django.db.models import Count, DecimalField, F, Max, Min, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from apps.collector.models import DebtSMSlog
from apps.company.models import Branch, Company
from apps.delivery.choices import ExternalSystemChoices
from apps.delivery.models import DeliveryAddress, Region
from apps.installment.models import Application
from apps.merchandise.choices import ProductMarkingChoices
from apps.merchandise.models import Brand, Category, Duration, Product
from apps.order.choices import (
    AdditionalServiceTypeChoices,
    MerchantPaymentStatusChoices,
    MerchantPaymentTypeChoices,
    OrderCreationTypeChoices,
    OrderSourceChoices,
    OrderStatusChoices,
    PartnerPayment,
    PaymentFactStatusChoices,
    ReceiptImageTypeChoices,
)
from apps.order.models import (
    AdditionalServicePrice,
    Contract,
    DraftOrder,
    DraftOrderProduct,
    MerchantPayment,
    Order,
    OrderAdditionalService,
    OrderProduct,
    OrderReceiptImage,
    OrderStatusLog,
    PaymentSchedule,
    PaymentTypesForApp,
)
from apps.personal.models import Cards
from apps.user.choices import LimitHistoryTypeChoices
from apps.user.models import Client, LimitHistory, Role
from apps.utils.exceptions import BadRequestException, NotFoundException
from apps.utils.functions import (
    get_delivery_external_service_type,
    get_radius_company,
    merchant_payload,
)
from apps.utils.messages import SMSMessages
from apps.utils.permissions import amanat_staff_by_role
from apps.wallet.choices import Status
from apps.wallet.choices import Status as WalletTransactionStatus
from apps.wallet.choices import TransactionSourceChoices, TransactionType
from apps.wallet.models import Transactions
from celery_tasks.tasks import (
    generate_contract_pdf_task,
    send_order_status_notification_task,
    sync_order_to_radius_task,
    upload_devices_task,
)
from services.card import CardService
from services.collector import CollectorService
from services.otp import OTPService
from services.pricing import PricingContext, PricingService
from services.radius import RadiusCRMService
from services.wallet import ClientWalletService


OrderItemPayload = dict[str, Any]
PreparedOrderProduct = tuple[OrderItemPayload, Product | int]


class OrderService:
    pricing_service = PricingService()
    """Order service - buyurtmalar bilan ishlash"""

    HIDE_PAYMENT_SCHEDULE_STATUSES = {
        OrderStatusChoices.RETURNED,
        OrderStatusChoices.CANCELLED_MERCHANT,
        OrderStatusChoices.CANCELLED_AMANAT,
    }

    @staticmethod
    def validate_order_editable_by_status(order: Order) -> None:
        """Buyurtma tahrirlashga ruxsat etilgan statusdaligini tekshiradi."""
        if order.status not in [
            OrderStatusChoices.DRAFT,
            OrderStatusChoices.PENDING_PARTNER,
        ]:
            raise BadRequestException(_("Buyurtmani tahrirlash mumkin emas."))

    @staticmethod
    def _as_local_schedule_datetime(value: datetime) -> datetime:
        """Payment schedule sanasini lokal timezone bilan normalize qiladi."""
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return timezone.localtime(value)

    @staticmethod
    def _order_payment_transaction_id(order: Order) -> str:
        """Order uchun private balancedan to'lov qilish transaction_id si"""
        return f"payment-private-balance:{order.id}"  # noqa

    @staticmethod
    def _validate_private_balance(client: Client, prepayment: Decimal) -> None:
        """Oldindan to'lov (prepayment) uchun mijozning shaxsiy balansi yetarliligini tekshiradi."""
        prepayment = Decimal(prepayment or 0)
        if prepayment <= 0:
            return
        if prepayment > client.private_balance:
            raise BadRequestException(_("Shaxsiy balans yetarli emas"))

    @staticmethod
    def _sync_limit_history(
        order: Order,
        *,
        application: Optional[Application] = None,
        limit_history: Optional[LimitHistory] = None,
    ) -> LimitHistory | None:
        """Buyurtma uchun limit band qilish tarixini yaratadi yoki yangilaydi."""
        application = (
            application if application is not None else order.client.get_active_limit
        )
        months = getattr(order.duration, "months", 0) or 0

        if not application or months <= 0:
            return None

        financed_total = Decimal(order.price) - Decimal(order.prepayment or 0)
        monthly_amount = (financed_total / Decimal(months)).quantize(
            Decimal("0.01")
        )
        limit_history = (
            limit_history or LimitHistory.objects.filter(order=order).first()
        )

        if limit_history:
            limit_history.amount = monthly_amount
            limit_history.comment = "Buyurtma narxi o'zgardi"
            limit_history.save(update_fields=["amount", "comment"])
            order.client.clear_limit_cache()
            return limit_history

        created = LimitHistory.objects.create(
            order=order,
            client=order.client,
            application=application,
            type=LimitHistoryTypeChoices.SUBTRACT,
            amount=monthly_amount,
            comment="Buyurtma yaratildi",
        )
        order.client.clear_limit_cache()
        return created

    @staticmethod
    def _validate_client_limit(
        client: Client,
        duration: Duration,
        total_price: Decimal,
        prepayment: Decimal = Decimal("0"),
        current_order: Optional[Order] = None,
        current_reserved: Optional[LimitHistory] = None,
    ) -> None:
        """Buyurtma summasi mijozning muddat bo'yicha limitiga yetishini tekshiradi."""
        months = getattr(duration, "months", 0) or 0
        if months <= 0:
            raise BadRequestException(_("Bo'lib to'lash muddati noto'g'ri"))

        duration_limit_attr_map = {
            4: "get_limit_4",
            6: "get_limit_6",
            12: "get_limit_12",
            18: "get_limit_18",
        }
        duration_limit_attr = duration_limit_attr_map.get(months)
        if not duration_limit_attr:
            raise BadRequestException(_("Ushbu muddat uchun limit sozlanmagan"))

        available_total_limit = Decimal(getattr(client, duration_limit_attr) or 0)

        if current_order:
            current_reserved = (
                current_reserved
                or LimitHistory.objects.filter(order=current_order).first()
            )
            if current_reserved:
                current_order_months = (
                    getattr(getattr(current_order, "duration", None), "months", 0) or 0
                )
                if current_order_months > 0:
                    restored_total = abs(Decimal(current_reserved.amount or 0)) * Decimal(
                        current_order_months
                    )
                    available_total_limit += restored_total

        required_total_limit = Decimal(total_price) - Decimal(prepayment or 0)

        if available_total_limit <= 0:
            raise BadRequestException(_("Limit mavjud emas"))

        if required_total_limit > available_total_limit:
            raise BadRequestException(_("Mijoz limiti yetarli emas"))

    @staticmethod
    def _get_default_first_payment(first_payment: Optional[date] = None) -> date:
        """Birinchi to'lov sanasini qaytaradi yoki standart keyingi oy sanasini beradi."""
        return first_payment or (timezone.localdate() + relativedelta(months=1))

    @staticmethod
    def _get_validation_maps(
        items_data: list[OrderItemPayload],
    ) -> tuple[dict[int, Product], dict[int, Category]]:
        """Validatsiya uchun kerakli mahsulot va kategoriyalarni oldindan yuklaydi."""
        product_ids = {
            item.get("product_id") for item in items_data if item.get("product_id")
        }
        category_ids = {
            item.get("category_id")
            for item in items_data
            if item.get("category_id") and not item.get("_category")
        }

        products_map = {
            product.id: product
            for product in Product.objects.select_related("category").filter(
                id__in=product_ids
            )
        }
        categories_map = {
            item["_category"].id: item["_category"]
            for item in items_data
            if item.get("_category")
        }
        categories_map.update(
            {
                category.id: category
                for category in Category.objects.filter(id__in=category_ids)
            }
        )
        return products_map, categories_map

    @staticmethod
    def _resolve_validation_item(
        item: OrderItemPayload,
        products_map: dict[int, Product],
        categories_map: dict[int, Category],
    ) -> tuple[Optional[Category], str, int]:
        """Validatsiya uchun itemdan kategoriya, nom va sonni ajratib oladi."""
        quantity = item.get("quantity", 1) or 1
        product_id = item.get("product_id")

        if product_id:
            product = products_map.get(product_id)
            category = product.category if product else None
            product_name = product.name if product else f"ID={product_id}"
            return category, product_name, quantity

        category = item.get("_category") or categories_map.get(item.get("category_id"))
        product_name = (
            item.get("full_name") or item.get("name") or str(_("Noma'lum mahsulot"))
        )
        return category, product_name, quantity

    @staticmethod
    def _collect_imei_category_violations(
        items_data: list[OrderItemPayload],
        products_map: dict[int, Product],
        categories_map: dict[int, Category],
    ) -> list[str]:
        """IMEI kategoriyalarda ruxsat etilgan miqdordan oshgan itemlarni topadi."""
        imei_category_quantities: dict[int, int] = defaultdict(int)
        imei_category_products: dict[int, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )

        for item in items_data:
            category, product_name, quantity = OrderService._resolve_validation_item(
                item, products_map, categories_map
            )
            if not category or category.marking_type != ProductMarkingChoices.IMEI:
                continue

            imei_category_quantities[category.id] += quantity
            imei_category_products[category.id][product_name] += quantity

        violations = []
        for category_id, quantity in imei_category_quantities.items():
            if quantity <= 2:
                continue

            products_text = ", ".join(
                f"{name} x{product_quantity}"
                for name, product_quantity in imei_category_products[
                    category_id
                ].items()
            )
            violations.append(products_text)

        return violations

    def _validate_order_for_verification(
        self,
        client: Client,
        duration: Duration,
        items_data: list[OrderItemPayload],
        total_price: Decimal,
        **kwargs,
    ) -> tuple[bool, str]:
        """Buyurtmani keyingi bosqichga o'tkazishdan oldingi biznes tekshiruvlarini bajaradi."""
        products_map, categories_map = self._get_validation_maps(items_data)
        violations = self._collect_imei_category_violations(
            items_data,
            products_map,
            categories_map,
        )
        if violations:
            return (
                False,
                str(
                    _(
                        "GSM kategoriya bo‘yicha 2 tadan ortiq tovar qo‘shish mumkin emas: %(products)s"
                    )
                    % {"products": "; ".join(violations)}
                ),
            )

        return True, ""

    @staticmethod
    def generate_order_number(company: Company) -> tuple[str, int]:
        """Kompaniya uchun navbatdagi buyurtma raqami va ichki counterni generatsiya qiladi."""
        max_counter = (
            Order.objects.filter(company=company).aggregate(Max("order_counter"))[
                "order_counter__max"
            ]
            or 0
        )

        new_counter = max_counter + 1
        order_number = f"{company.unique_code}{new_counter:06d}"  # noqa

        return order_number, new_counter

    def change_order_status(
        self,
        order: Order,
        new_status: str,
        created_by: Optional[Role] = None,
        comment: Optional[str] = None,
    ) -> OrderStatusLog:
        """Buyurtma statusini o'zgartiradi va shu o'zgarish uchun log yozadi."""
        order.status = new_status
        order.save(update_fields=["status"])

        prev_log = order.status_logs.order_by("-created_at").first()

        return OrderStatusLog.objects.create(
            order=order,
            prev=prev_log,
            status=new_status,
            created_by=created_by,
            comment=comment,
        )

    def _get_client(self, client_id: int) -> Optional[Client]:
        """ID bo'yicha mijozni topadi."""
        return Client.objects.filter(id=client_id).first()

    def _get_duration(self, duration_id: int) -> Optional[Duration]:
        """ID bo'yicha muddat obyektini topadi."""
        return Duration.objects.filter(id=duration_id).first()

    def _get_company_and_branch(
        self, company_id: Optional[int], branch_id: Optional[int]
    ) -> tuple[Optional[Company], Optional[Branch]]:
        """Berilgan company yoki branch asosida tegishli kompaniya va filialni topadi."""
        if branch_id:
            branch = (
                Branch.objects.select_related("company").filter(id=branch_id).first()
            )
            if branch:
                return branch.company, branch
            return None, None

        if company_id:
            company = Company.objects.filter(id=company_id).first()
            return company, None

        return None, None

    @staticmethod
    def _get_order_source(branch: Optional[Branch]) -> str:
        """Filial online bo'lsa online, aks holda offline manbasini qaytaradi."""
        if branch and branch.is_online:
            return OrderSourceChoices.ONLINE
        return OrderSourceChoices.OFFLINE

    def _resolve_product(
        self, item: OrderItemPayload, company: Company
    ) -> Optional[Product]:
        """Item ichidan mavjud mahsulotni `product_id` yoki `external_id` bilan topadi."""
        product_id = item.get("product_id")
        external_id = item.get("external_id")

        if product_id:
            return Product.objects.filter(id=product_id).first()

        if external_id:
            return Product.objects.filter(
                external_id=external_id, company=company
            ).first()

        return None

    def _resolve_products(
        self, items: list[OrderItemPayload], company: Company
    ) -> tuple[list[dict[str, int]], list[str]]:
        """Itemlar ro'yxatidagi mahsulotlarni topib, xatolarni alohida yig'adi."""
        products_data = []
        errors = []

        for idx, item in enumerate(items):
            product = self._resolve_product(item, company)

            if not product:
                product_id = item.get("product_id")
                external_id = item.get("external_id")
                if product_id:
                    errors.append(f"Mahsulot topilmadi (product_id={product_id})")
                elif external_id:
                    errors.append(f"Mahsulot topilmadi (external_id={external_id})")
                else:
                    errors.append(f"Item {idx + 1}: product_id yoki external_id kerak")
                continue

            products_data.append(
                {
                    "product_id": product.id,
                    "quantity": item.get("quantity", 1),
                }
            )

        return products_data, errors

    def _get_or_create_product(
        self, item: OrderItemPayload, company: Company
    ) -> Product:
        """Mavjud mahsulotni qaytaradi yoki item ma'lumotidan yangisini yaratadi."""
        product_id = item.get("product_id")

        if product_id:
            product = Product.objects.filter(id=product_id).first()
            if not product:
                raise NotFoundException(_(f"Mahsulot topilmadi (id={product_id})"))
            return product

        # Serializer tomonidan cache qilingan kategoriyani ishlatish (qo'shimcha query yo'q)
        category = item.get("_category")
        if not category:
            category_id = item.get("category_id")
            category = Category.objects.filter(id=category_id).first()
            if not category:
                raise NotFoundException(_(f"Kategoriya topilmadi (id={category_id})"))

        product = Product.objects.create(
            name=item.get("name"),
            category=category,
            company=company,
            ikpu=item.get("ikpu"),
            brand_id=item.get("brand_id"),
        )

        return product

    def calculate_price_v2(
        self,
        company_id: int,
        category_id: int,
        duration_id: int,
        price: Decimal | str,
        prepayment: Decimal | str = Decimal("0"),
        *,
        pricing_context: PricingContext,
    ) -> dict[str, Decimal]:
        """Mahsulotning ustama, oldindan to'lov va yakuniy narxini hisoblaydi.

        Foiz `PricingContext` dan olinadi: kategoriya (yoki ota kategoriya,
        yoki `Duration.percent`), kompaniya va mijoz stavkalaridan eng kichigi.
        Ustama to'liq narxga emas, moliyalashtirilayotgan qismga qo'llanadi.
        """
        percent = pricing_context.resolve_percent(
            duration_id=duration_id,
            category_id=category_id,
            company_id=company_id,
        )
        return self.pricing_service.calculate(
            base_price=price,
            percent=percent,
            prepayment=prepayment,
        )

    @staticmethod
    def _validate_prepayment_amount(
        prepayment: Optional[Decimal | str],
        total_base_price: Decimal,
    ) -> Decimal:
        """Oldindan to'lov summasi ruxsat etilgan diapazonda ekanini tekshiradi."""
        prepayment_amount = Decimal(str(prepayment or "0"))
        if prepayment_amount < 0:
            raise BadRequestException(_("Oldindan to'lov manfiy bo'lishi mumkin emas"))
        if prepayment_amount > total_base_price:
            raise BadRequestException(
                _(
                    "Oldindan to'lov mahsulotlarning jami asl narxidan yuqori bo'lishi mumkin emas"
                )
            )
        return prepayment_amount

    def _allocate_prepayment(
        self, items: list[OrderProduct], total_prepayment: Decimal
    ) -> None:
        """Umumiy oldindan to'lovni order itemlar orasida taqsimlaydi."""
        remaining = Decimal(str(total_prepayment or "0"))
        for item in items:
            item.prepayment = Decimal("0")

        if remaining <= 0:
            return

        for item in sorted(
            items, key=lambda row: (row.base_price, row.product_id or 0), reverse=True
        ):
            if remaining <= 0:
                break
            allocated = min(item.base_price, remaining)
            item.prepayment = allocated
            remaining -= allocated

    def _apply_pricing_to_items(
        self,
        items: list[OrderProduct],
        *,
        total_prepayment: Decimal,
        company_id: int,
        duration_id: int,
        pricing_context: PricingContext,
    ) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """Itemlarga narxlarni qo'llab, order bo'yicha jami summalarni hisoblaydi."""
        self._allocate_prepayment(items, total_prepayment)

        total_base_price = Decimal("0")
        total_prepayment_amount = Decimal("0")
        total_markup_amount = Decimal("0")
        total_price = Decimal("0")

        for item in items:
            price_data = self.calculate_price_v2(
                company_id=company_id,
                category_id=item.line_category_id,
                duration_id=duration_id,
                price=item.base_price,
                prepayment=item.prepayment,
                pricing_context=pricing_context,
            )
            item.markup_amount = price_data["markup_amount"]
            item.price = price_data["price"]

            total_base_price += item.base_price
            total_prepayment_amount += item.prepayment
            total_markup_amount += item.markup_amount
            total_price += item.price

        return (
            total_base_price,
            total_prepayment_amount,
            total_markup_amount,
            total_price,
        )

    def _spend_order_prepayment_private_balance(self, order: Order) -> None:
        """Buyurtma oldindan to'lovini mijozning shaxsiy balansidan yechadi."""
        if order.prepayment <= 0:
            return

        ClientWalletService().pay_order_from_private_balance(
            order=order,
            amount=order.prepayment,
        )
        self._create_order_payment_transaction(order)

    def _refund_order_prepayment_private_balance(self, order: Order) -> None:
        """Bekor qilingan buyurtma oldindan to'lovini shaxsiy balansga qaytaradi."""
        if order.prepayment <= 0:
            return

        ClientWalletService().refund_order_private_balance(
            order=order,
            amount=order.prepayment,
        )

    def _create_order_payment_transaction(self, order: Order) -> None:
        """Private balancedan Orderga to'lov qilindi degan musbat PAYMENT tranzaksiyasini yaratadi."""
        if order.prepayment <= 0:
            return

        tr_id = self._order_payment_transaction_id(order)

        Transactions.objects.update_or_create(
            order=order,
            transaction_id=tr_id,
            defaults={
                "client": order.client,
                "amount": order.prepayment,
                "source": TransactionSourceChoices.PRIVATE_BALANCE,
                "type": TransactionType.PAYMENT,
                "status": WalletTransactionStatus.SUCCESS,
                "description": f"Buyurtma ({order.number}) uchun oldindan to'lov qilindi",
            },
        )

    def _mark_order_private_balance_transaction_returned(self, order: Order) -> None:
        """Private balance dan Order uchun qilingan to'lovni qaytarish"""
        if order.prepayment <= 0:
            return

        Transactions.objects.filter(
            order=order,
            transaction_id=self._order_payment_transaction_id(order),
            type=TransactionType.PAYMENT,
            status=WalletTransactionStatus.SUCCESS,
        ).update(status=WalletTransactionStatus.RETURN)

    def _refund_order_paid_amount_to_private_balance(self, order: Order) -> None:
        """Order bo'yicha to'langan summani private balance'ga qaytaradi."""
        paid_amount = (
            Transactions.objects.filter(
                order=order,
                type=TransactionType.PAYMENT,
                status=WalletTransactionStatus.SUCCESS,
            )
            .exclude(source=TransactionSourceChoices.PRIVATE_BALANCE)
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0")
        )

        if paid_amount <= 0:
            return

        ClientWalletService().refund_order_paid_amount_to_private_balance(
            order=order,
            amount=paid_amount,
        )

        # order uchun to'lov qilingan transactionlarni return qilib qo'yish
        # Transactions.objects.filter(
        #     order=order,
        #     type=TransactionType.PAYMENT,
        #     status=WalletTransactionStatus.SUCCESS,
        # ).exclude(source=TransactionSourceChoices.PRIVATE_BALANCE).update(
        #     status=WalletTransactionStatus.RETURN,
        #     cancelled_at=timezone.now(),
        # )

    @staticmethod
    def _get_order_cashback_amount(order: Order) -> Decimal:
        """Order uchun hisoblanadigan cashback summasi."""
        return (order.price * Decimal("0.01")).quantize(Decimal("0.01"))

    def _award_order_cashback(self, order: Order) -> None:
        """Order aktiv bo'lganda cashback beradi."""
        cashback = self._get_order_cashback_amount(order)
        if cashback <= 0:
            return

        ClientWalletService().topup_cashback_balance(
            client=order.client,
            amount=cashback,
            cashback_source="order",
            expires_at=timezone.now() + relativedelta(years=1),
            order=order,
            description=f"Buyurtma ({order.number}) uchun cashback",
        )

    def _rollback_order_cashback(self, order: Order) -> None:
        """Order bekor qilinganda yoki qaytarilganda cashbackni qaytarib oladi."""
        cashback = self._get_order_cashback_amount(order)
        if cashback <= 0:
            return
        cashback_service = ClientWalletService()
        earned_cashback_exists = cashback_service.cashback_transaction_model.objects.filter(
            client=order.client,
            order=order,
            type="deposit",
            amount=cashback,
        ).exists()
        if not earned_cashback_exists:
            return

        cashback_service.spend_cashback_balance(
            client=order.client,
            amount=cashback,
            order=order,
            description=f"Buyurtma ({order.number}) bekor qilindi / qaytarildi",
        )

    def _process_terminal_order_effects(
        self,
        *,
        order: Order,
        new_status: str,
        created_by: Optional[Role],
        comment: str,
        create_merchant_refund: bool = False,
    ) -> None:
        """Terminal statuslar uchun order statusi va side effectlarni bir joyda bajaradi."""
        with transaction.atomic():
            self.change_order_status(
                order=order,
                new_status=new_status,
                created_by=created_by,
                comment=comment,
            )

            if order.prepayment > 0:
                self._refund_order_prepayment_private_balance(order)
                self._mark_order_private_balance_transaction_returned(order)

            if new_status == OrderStatusChoices.RETURNED:
                self._refund_order_paid_amount_to_private_balance(order)

            self._rollback_order_cashback(order)

            if create_merchant_refund:
                self.create_merchant_refund_payments(order)

            if (
                new_status in self.HIDE_PAYMENT_SCHEDULE_STATUSES
                and Contract.objects.filter(order=order).exists()
            ):
                transaction.on_commit(lambda: generate_contract_pdf_task.delay(order.id))

            if order.client_id:
                transaction.on_commit(
                    lambda: send_order_status_notification_task.delay(order.id)
                )

    def activate_order(
        self,
        order: Order,
        created_by: Optional[Role] = None,
        comment: str = "",
    ) -> Order:
        """Orderni ACTIVE holatiga o'tkazib, cashback beradi."""
        if order.status == OrderStatusChoices.ACTIVE:
            return order

        with transaction.atomic():
            self.change_order_status(
                order=order,
                new_status=OrderStatusChoices.ACTIVE,
                created_by=created_by,
                comment=comment or "Buyurtma faollashtirildi",
            )
            self._award_order_cashback(order)

        return order

    def close_order(
        self,
        order: Order,
        created_by: Optional[Role] = None,
        comment: str = "",
    ):
        """
        Agar order puli to'lab bo'lingan bo'lsa yopib yuborish kerak
        """
        if (
            order.total_paid >= order.price
            and order.status != OrderStatusChoices.CLOSED
        ):
            self.change_order_status(
                order=order,
                new_status=OrderStatusChoices.CLOSED,
                created_by=created_by,
                comment=comment or "Buyurtma to'liq to'landi",
            )

    def create_order(
        self,
        *,
        client_id: int,
        duration_id: int,
        items: list[dict],
        company_id: Optional[int] = None,
        branch_id: Optional[int] = None,
        created_by: Optional[Role] = None,
        delivery_data: Optional[dict[str, Any]] = None,
        prepayment: Optional[Decimal] = None,
        first_payment: Optional[date] = None,
        additional_services: Optional[list[AdditionalServicePrice]] = None,
        pick_up_address: Optional[str] = None,
    ) -> Order:
        """Admin yoki partner oqimi uchun yangi qoralama buyurtma yaratadi."""
        if not items:
            raise BadRequestException("Kamida bitta mahsulot kerak")

        company, branch = self._get_company_and_branch(company_id, branch_id)
        if not company:
            raise NotFoundException("Kompaniya yoki filial topilmadi")

        client = Client.objects.filter(id=client_id).first()
        if not client:
            raise NotFoundException(f"Mijoz topilmadi (id={client_id})")

        duration = self._get_duration(duration_id)
        if not duration:
            raise NotFoundException("Bo'lib to'lash muddati topilmadi")

        order_number, order_counter = self.generate_order_number(company)

        is_valid = True
        error_msg = ""

        with transaction.atomic():
            order_products_data, created_products = self._resolve_items_to_products(
                items, company
            )
            pricing_context = self._build_pricing_context(
                company_id=company.id,
                duration_id=duration.id,
                category_ids=self._prepared_category_ids(
                    order_products_data, created_products
                ),
                client_id=client.id,
            )

            order = Order(
                number=order_number,
                order_counter=order_counter,
                client=client,
                company=company,
                duration=duration,
                branch=branch,
                created_by=created_by,
                base_price=Decimal("0"),
                markup_amount=Decimal("0"),
                price=Decimal("0"),
                prepayment=Decimal("0"),
                status=OrderStatusChoices.DRAFT,
                first_payment=self._get_default_first_payment(first_payment),
                pick_up_address=pick_up_address,
                source=self._get_order_source(branch),
                creation_type=OrderCreationTypeChoices.MANUALLY,
            )

            order_products = self._build_order_products(
                order,
                order_products_data,
                created_products,
                company_id=company.id,
                duration_id=duration.id,
                pricing_context=pricing_context,
            )

            requested_prepayment = self._validate_prepayment_amount(
                prepayment, sum(op.base_price for op in order_products)
            )
            (
                total_base_price,
                total_prepayment_amount,
                total_markup_amount,
                total_price,
            ) = self._apply_pricing_to_items(
                order_products,
                total_prepayment=requested_prepayment,
                company_id=company.id,
                duration_id=duration.id,
                pricing_context=pricing_context,
            )
            self._validate_private_balance(client, total_prepayment_amount)

            services_to_create = []
            if additional_services:
                for sp in additional_services:
                    if sp:
                        services_to_create.append({"service": sp, "price": sp.price})
                        total_price += sp.price

            #  Delivery narxi limitga aralashmasligi kerak !!!
            # if delivery_data:
            #     delivery_region = delivery_data.get("region")
            #     if delivery_region:
            #         service_type, delivery_price_obj = self._get_delivery_service_price(delivery_region)
            #         if delivery_price_obj:
            #             total_price += delivery_price_obj.price

            is_valid, error_msg = self._validate_order_for_verification(
                client=client,
                duration=duration,
                items_data=items,
                total_price=Decimal(total_price),
            )

            if not is_valid:
                draft_order = DraftOrder.objects.create(
                    client=client,
                    company=company,
                    duration=duration,
                    base_price=total_base_price,
                    markup_amount=total_markup_amount,
                    price=total_price,
                )
                draft_items = [
                    DraftOrderProduct(
                        draft_order=draft_order,
                        product=op.product,
                        quantity=1,
                        base_price=op.base_price,
                        markup_amount=op.markup_amount,
                        price=op.price,
                    )
                    for op in order_products
                ]
                DraftOrderProduct.objects.bulk_create(draft_items)
            else:
                self._validate_client_limit(
                    client=client,
                    duration=duration,
                    total_price=Decimal(total_price),
                    prepayment=total_prepayment_amount,
                )

                order.base_price = total_base_price
                order.prepayment = total_prepayment_amount
                order.markup_amount = total_markup_amount
                order.price = total_price
                order.save()

                OrderProduct.objects.bulk_create(order_products)

                if services_to_create:
                    OrderAdditionalService.objects.bulk_create(
                        [
                            OrderAdditionalService(
                                order=order, service=s["service"], price=s["price"]
                            )
                            for s in services_to_create
                        ]
                    )

                if delivery_data:
                    self._create_delivery_address(order, delivery_data)

                self._sync_limit_history(order)

                self.change_order_status(
                    order=order,
                    new_status=OrderStatusChoices.DRAFT,
                    created_by=created_by,
                    comment="Buyurtma yaratildi",
                )

        if not is_valid:
            raise BadRequestException(error_msg)

        return order

    def create_mobile_order(
        self,
        *,
        client: Client,
        duration: Duration,
        products_data: list[dict[str, Any]],
        additional_services: Optional[list[AdditionalServicePrice]] = None,
        pick_up_address: Optional[str] = None,
    ) -> dict:
        """Mobil ilova uchun scoring, draft yoki success natijasi bilan buyurtma yaratadi."""
        _limit = client.get_limit
        scoring_enable = client.scoring_enable
        # 1) Agar limit mavjud bo'lmasa -> step="scoring"
        if (_limit or 0) <= 0 and scoring_enable:
            return {"step": "scoring", "message": "Limit mavjud emas"}

        company = get_radius_company()

        services_total = Decimal("0")
        services_to_create = []
        if additional_services:
            for sp in additional_services:
                if sp:
                    services_to_create.append({"service": sp, "price": sp.price})
                    services_total += sp.price

        total_base_price = Decimal("0")
        total_markup_amount = Decimal("0")
        total_price = services_total

        prepared_products = []

        pricing_context = self._build_pricing_context(
            company_id=company.id,
            duration_id=duration.id,
            category_ids=[item["product"].category_id for item in products_data],
            client_id=client.id,
        )

        for item in products_data:
            product = item["product"]
            quantity = item["quantity"]
            base_price = item["price"]

            price_data = self.calculate_price_v2(
                company_id=company.id,
                category_id=product.category_id,
                duration_id=duration.id,
                price=base_price,
                pricing_context=pricing_context,
            )

            markup_amount = price_data["markup_amount"]
            final_price = price_data["price"]

            total_base_price += base_price * quantity
            total_markup_amount += markup_amount * quantity
            total_price += final_price * quantity

            prepared_products.append(
                {
                    "product": product,
                    "quantity": quantity,
                    "base_price": base_price,
                    "markup_amount": markup_amount,
                    "price": final_price,
                }
            )

        validation_items = [
            {
                "product_id": item["product"].id,
                "quantity": item["quantity"],
            }
            for item in prepared_products
        ]

        is_valid, error_msg = self._validate_order_for_verification(
            client=client,
            duration=duration,
            items_data=validation_items,
            total_price=Decimal(total_price),
        )
        if not is_valid:
            raise BadRequestException(error_msg)

        # 2) Agar limit yetmasa -> DraftOrder objecti yaratilsin
        try:
            self._validate_client_limit(
                client=client,
                duration=duration,
                total_price=total_price,
                prepayment=Decimal("0"),
            )
        except BadRequestException:
            with transaction.atomic():
                draft_order = DraftOrder.objects.create(
                    client=client,
                    company=company,
                    duration=duration,
                    base_price=total_base_price,
                    markup_amount=total_markup_amount,
                    price=total_price,
                )

                draft_items = []
                for p in prepared_products:
                    draft_items.append(
                        DraftOrderProduct(
                            draft_order=draft_order,
                            product=p["product"],
                            quantity=p["quantity"],
                            base_price=p["base_price"],
                            markup_amount=p["markup_amount"],
                            price=p["price"],
                        )
                    )
                DraftOrderProduct.objects.bulk_create(draft_items)

            return {"step": "application", "draft_order": draft_order}

        # 3) Agar limiti yetsa -> draft statusida order yaratiladi
        order_number, order_counter = self.generate_order_number(company)

        with transaction.atomic():
            order = Order.objects.create(
                number=order_number,
                order_counter=order_counter,
                client=client,
                company=company,
                duration=duration,
                base_price=total_base_price,
                markup_amount=total_markup_amount,
                price=total_price,
                status=OrderStatusChoices.DRAFT,
                pick_up_address=pick_up_address,
                source=OrderSourceChoices.MARKETPLACE,
                creation_type=OrderCreationTypeChoices.MOBILE,
            )

            if services_to_create:
                OrderAdditionalService.objects.bulk_create(
                    [
                        OrderAdditionalService(
                            order=order, service=s["service"], price=s["price"]
                        )
                        for s in services_to_create
                    ]
                )

            order_items = []
            for p in prepared_products:
                # OrderProduct is per-item (for IMEI/Marking tracking support)
                for idx in range(p["quantity"]):
                    order_items.append(
                        OrderProduct(
                            order=order,
                            product=p["product"],
                            base_price=p["base_price"],
                            prepayment=Decimal("0"),
                            markup_amount=p["markup_amount"],
                            price=p["price"],
                        )
                    )

            OrderProduct.objects.bulk_create(order_items)

            if settings.IS_PRODUCTION:
                sync_order_to_radius_task.delay(order.id)

            self._sync_limit_history(order)

            self.change_order_status(
                order=order,
                new_status=OrderStatusChoices.DRAFT,
                comment="Mobil ilovadan buyurtma yaratildi",
            )

            return {"step": "success", "order": order}

    def _create_order_product(
        self,
        order: Order,
        product: Product,
        base_price: Decimal,
    ) -> OrderProduct:
        """Bitta mahsulot uchun saqlangan `OrderProduct` yozuvini yaratadi."""
        markup_percent = Decimal("1")  # 1% markup
        unit_markup = base_price * markup_percent / 100
        unit_final = base_price + unit_markup

        return OrderProduct.objects.create(
            order=order,
            product=product,
            base_price=base_price,
            prepayment=Decimal("0"),
            markup_amount=unit_markup,
            price=unit_final,
        )

    def _recalculate_order_totals(
        self,
        order: Order,
        *,
        application=None,
        limit_history: Optional[LimitHistory] = None,
    ) -> LimitHistory | None:
        """Order itemlari va xizmatlaridan kelib chiqib jami summalarni qayta hisoblaydi."""
        totals = order.items.aggregate(
            base=Sum("base_price"),
            prepayment=Sum("prepayment"),
            markup=Sum("markup_amount"),
            total=Sum("price"),
        )

        service_total = order.additional_services.aggregate(Sum("price"))[
            "price__sum"
        ] or Decimal("0")

        order.base_price = totals["base"] or Decimal("0")
        order.prepayment = totals["prepayment"] or Decimal("0")
        order.markup_amount = totals["markup"] or Decimal("0")
        order.price = (totals["total"] or Decimal("0")) + service_total
        order.save(update_fields=["base_price", "prepayment", "markup_amount", "price"])
        return self._sync_limit_history(
            order, application=application, limit_history=limit_history
        )

    def add_product_to_order(
        self,
        order: Order,
        product: Product,
        base_price: Decimal = Decimal("0"),
    ) -> OrderProduct:
        """Buyurtmaga yangi mahsulot qo'shadi va jami summalarni yangilaydi."""
        order_product = self._create_order_product(order, product, base_price)
        self._recalculate_order_totals(order)
        return order_product

    def remove_product_from_order(self, order_product: OrderProduct) -> None:
        """Buyurtmadan mahsulotni o'chiradi va jami summalarni yangilaydi."""
        order = order_product.order
        order_product.delete()
        self._recalculate_order_totals(order)

    def create_payment_schedule(
        self, order: Order, first_payment: Optional["date"] = None
    ) -> list[PaymentSchedule]:
        """Buyurtma uchun to'lov jadvalini yaratadi va kerak bo'lsa prepaymentni yechadi."""

        # Clean existing schedules
        order.payment_schedules.all().delete()

        months = order.duration.months
        financed_total = order.price - order.prepayment

        base_monthly = (financed_total // months // 1000) * 1000

        total_scheduled = base_monthly * months
        remainder = financed_total - total_scheduled

        schedules_to_create = []

        if order.prepayment > 0:
            schedules_to_create.append(
                PaymentSchedule(
                    order=order,
                    period_number=0,
                    due_date=order.created_at,
                    planned_amount=order.prepayment,
                )
            )

        if first_payment:
            base_date = self._as_local_schedule_datetime(
                datetime.combine(first_payment, time(hour=0))
            )
        else:
            base_date = self._as_local_schedule_datetime(order.created_at)
            base_date = base_date + relativedelta(months=1)
            base_date = base_date.replace(hour=0, minute=0, second=0, microsecond=0)

        min_last_due_date = None
        if months == 12:
            min_last_due_date = self._as_local_schedule_datetime(order.created_at) + relativedelta(days=366)
            min_last_due_date = min_last_due_date.replace(hour=0, minute=0, second=0, microsecond=0)

        for i in range(months):
            amount = base_monthly + remainder if i == 0 else base_monthly

            due_date = base_date + relativedelta(months=i)
            if min_last_due_date and i == months - 1 and due_date < min_last_due_date:
                due_date = min_last_due_date

            schedules_to_create.append(
                PaymentSchedule(
                    order=order,
                    period_number=i + 1,
                    due_date=due_date,
                    planned_amount=amount,
                )
            )

        schedules = PaymentSchedule.objects.bulk_create(schedules_to_create)

        if order.prepayment > 0:
            self._validate_private_balance(order.client, order.prepayment)
            self._spend_order_prepayment_private_balance(order)

        return schedules

    def get_mobile_order_detail(self, order: Order, lang="uz", request=None) -> dict:
        """Mobil ilova uchun buyurtma tafsilotlarini hisoblab qaytaradi."""
        status_map = {
            "paid": {"uz": "To'langan", "ru": "Оплачено"},
            "expected": {"uz": "Kutilmoqda", "ru": "В ожидании"},
            "overdue": {"uz": "Muddati o'tgan", "ru": "Просрочено"},
            "partially_paid": {"uz": "Qisman to'langan", "ru": "Частично оплачено"},
        }

        products_map = {}
        for item in order.items.select_related("product").all():
            p_id = str(item.product_id)
            if p_id not in products_map:
                products_map[p_id] = {
                    "product_id": p_id,
                    "name": item.display_name,
                    "count": 0,
                    "price": str(int(item.price)),
                }
            products_map[p_id]["count"] += 1

        products = list(products_map.values())

        delivery = None
        delivery_service = None
        try:
            delivery = order.delivery
        except DeliveryAddress.DoesNotExist:
            delivery = None

        if delivery:
            delivery_service = delivery.service
            if not delivery_service and delivery.region:
                delivery_service = get_delivery_external_service_type(delivery.region)

        # Terminal statuslarda payment schedule mobil detailda ko'rsatilmaydi.
        hide_payment_schedule = order.status in self.HIDE_PAYMENT_SCHEDULE_STATUSES
        schedules = (
            []
            if hide_payment_schedule
            else list(order.payment_schedules.all().order_by("due_date"))
        )
        transactions = list(
            order.transactions.filter(
                type=TransactionType.PAYMENT,
                status=WalletTransactionStatus.SUCCESS,
            )
        )

        # Calculate Total Paid
        total_paid = sum(t.amount for t in transactions)
        total_price = order.price

        # Calculate Debt (Cumulative Overdue) & Schedule Status
        # Logic: Distribute total_paid across schedules sequentially

        cumulative_planned = Decimal("0")
        overdue_amount = Decimal("0")
        now = timezone.now()

        # 1. Calculate Debt (same logic as Order.overdue property)
        for schedule in schedules:
            cumulative_planned += schedule.planned_amount
            if total_paid < cumulative_planned:
                if schedule.due_date < now:
                    overdue_amount = cumulative_planned - total_paid
                break

        debt = overdue_amount

        # 2. Build Schedule with Status
        monthly_schedule = []
        remaining_paid_tracker = total_paid
        remaining_months = 0
        month = order.duration.months

        for schedule in schedules:
            plan_price = schedule.planned_amount
            plan_date = self._as_local_schedule_datetime(schedule.due_date)

            if remaining_paid_tracker >= plan_price:
                status_key = "paid"
                remaining_paid_tracker -= plan_price
            elif remaining_paid_tracker > 0:
                status_key = "partially_paid"
                remaining_paid_tracker = Decimal("0")
                remaining_months += 1
            else:
                if plan_date < now:
                    status_key = "overdue"
                else:
                    status_key = "expected"
                remaining_months += 1

            display_status = status_map.get(status_key, {}).get(lang, "")

            monthly_schedule.append(
                {
                    "period_number": schedule.period_number,
                    "plan_date": plan_date.strftime("%d.%m.%Y"),
                    "plan_price": int(plan_price),
                    "status": status_key,
                    "status_desc": display_status,
                }
            )

        last_payment_day = ""
        if monthly_schedule:
            last_payment_day = monthly_schedule[-1]["plan_date"]

        monthly_payment = 0
        for db_schedule, schedule in zip(schedules, monthly_schedule):
            if db_schedule.period_number > 0:
                monthly_payment = schedule["plan_price"]
                break

        remaining_price = total_price - total_paid

        return {
            "order_name": order.number,
            "status": order.status,
            "status_desc": order.get_status_display(),
            "source": "omonat",
            "merchant": merchant_payload(order.company, request),
            "delivery_service": delivery_service,
            "products": products,
            "total": int(total_price),
            "tarif": month,
            "type": "installment",
            "last_payment_day": last_payment_day,
            "monthly_payment": monthly_payment,
            "monthly": monthly_schedule,
            "remaining_months": remaining_months,
            "remaining_price": int(remaining_price) if remaining_price > 0 else 0,
            "paid_price": int(total_paid),
            "debt": int(debt),
            "created_at": timezone.localtime(order.created_at),
        }

    def get_payment_facts(self, order: Order) -> list[dict]:
        """To'lov jadvali va tranzaksiyalarni bitta vaqt ketma-ketligiga birlashtiradi."""
        schedules = list(order.payment_schedules.all().order_by("due_date"))
        transactions = list(
            order.transactions.filter(
                type=TransactionType.PAYMENT,
                status=WalletTransactionStatus.SUCCESS,
            ).order_by("created_at", "id")
        )

        def local_date(value):
            return timezone.localtime(value).date()

        txn_by_date = defaultdict(list)
        for txn in transactions:
            txn_by_date[local_date(txn.created_at)].append(txn)

        actual_by_schedule = defaultdict(lambda: Decimal("0"))
        paid_at_by_schedule = {}
        schedule_index = 0
        for txn in transactions:
            remaining_amount = txn.amount

            while remaining_amount > 0 and schedule_index < len(schedules):
                schedule = schedules[schedule_index]
                schedule_remaining = (
                    schedule.planned_amount - actual_by_schedule[schedule.id]
                )

                if schedule_remaining <= 0:
                    schedule_index += 1
                    continue

                applied_amount = min(remaining_amount, schedule_remaining)
                actual_by_schedule[schedule.id] += applied_amount
                remaining_amount -= applied_amount

                if actual_by_schedule[schedule.id] >= schedule.planned_amount:
                    paid_at_by_schedule[schedule.id] = txn.created_at
                    schedule_index += 1

        result = []
        now = timezone.now()
        schedule_dates = {local_date(schedule.due_date) for schedule in schedules}

        for schedule in schedules:
            schedule_date = local_date(schedule.due_date)
            planned = schedule.planned_amount
            actual = sum(
                t.amount for t in txn_by_date.get(schedule_date, [])
            )
            allocated_actual = actual_by_schedule[schedule.id]
            overdue = Decimal("0")
            overdue_days = None
            paid_at = paid_at_by_schedule.get(schedule.id)
            if paid_at and schedule.due_date < paid_at:
                overdue_days = (paid_at - schedule.due_date).days
            elif schedule.due_date < now and allocated_actual < planned:
                overdue = planned - allocated_actual
                overdue_days = (now - schedule.due_date).days

            if allocated_actual >= planned:
                status = PaymentFactStatusChoices.PAID
            elif allocated_actual > 0:
                status = PaymentFactStatusChoices.PARTIALLY_PAID
            elif overdue > 0:
                status = PaymentFactStatusChoices.OVERDUE
            else:
                status = PaymentFactStatusChoices.PENDING

            result.append(
                {
                    "date": schedule_date,
                    "planned": planned,
                    "actual": actual,
                    "overdue": overdue,
                    "overdue_days": overdue_days,
                    "status": status,
                    "transactions": [
                        {
                            "id": t.id,
                            "uuid": str(t.uuid),
                            "source": t.source,
                            "source_display": t.get_source_display(),
                            "amount": t.amount,
                            "date": t.created_at,
                        }
                        for t in txn_by_date.get(schedule_date, [])
                    ],
                }
            )

        for dt, txns in txn_by_date.items():
            if dt in schedule_dates:
                continue

            actual = sum(t.amount for t in txns)
            result.append(
                {
                    "date": dt,
                    "planned": Decimal("0"),
                    "actual": actual,
                    "overdue": Decimal("0"),
                    "overdue_days": None,
                    "status": PaymentFactStatusChoices.EXTRA,
                    "transactions": [
                        {
                            "id": t.id,
                            "uuid": str(t.uuid),
                            "source": t.source,
                            "source_display": t.get_source_display(),
                            "amount": t.amount,
                            "date": t.created_at,
                        }
                        for t in txns
                    ],
                }
            )

        return sorted(result, key=lambda row: row["date"])

    def get_order_totals(self, order: Order) -> dict:
        """Buyurtma bo'yicha reja, to'langan, kechikkan va qolgan summalarni hisoblaydi."""
        schedules = order.payment_schedules.all()
        transactions = order.transactions.filter(
            type=TransactionType.PAYMENT,
            status=WalletTransactionStatus.SUCCESS,
        )

        total_planned = schedules.aggregate(Sum("planned_amount"))[
            "planned_amount__sum"
        ] or Decimal("0")

        total_actual = transactions.aggregate(Sum("amount"))["amount__sum"] or Decimal(
            "0"
        )

        # Calculate overdue
        now = timezone.now()
        total_overdue = Decimal("0")
        for schedule in schedules.filter(due_date__lt=now):
            paid_on_date = transactions.filter(
                created_at__date=schedule.due_date.date()
            ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0")

            if paid_on_date < schedule.planned_amount:
                total_overdue += schedule.planned_amount - paid_on_date

        return {
            "planned": total_planned,
            "actual": total_actual,
            "overdue": total_overdue,
            "remaining": total_planned - total_actual,
        }

    # ---------------------------------------------------------------------------
    # Private helpers for create_order / update_order
    # ---------------------------------------------------------------------------

    def _resolve_items_to_products(
        self,
        items: list[OrderItemPayload],
        company: Company,
    ) -> tuple[list[PreparedOrderProduct], list[Product]]:
        """Itemlarni mavjud yoki yangi yaratiladigan mahsulot obyektlariga bog'laydi."""
        product_ids = {
            item["product_id"]
            for item in items
            if item.get("product_id") and item["product_id"] > 0
        }

        existing_products: dict[int, Product] = {}
        if product_ids:
            existing_products = {
                p.id: p for p in Product.objects.filter(id__in=product_ids)
            }
            missing = product_ids - set(existing_products.keys())
            if missing:
                raise NotFoundException(_(f"Mahsulotlar topilmadi: {missing}"))

        products_to_create: list[Product] = []
        order_products_data: list[tuple] = []

        for item in items:
            product_id = item.get("product_id")
            if product_id and product_id > 0:
                order_products_data.append((item, existing_products[product_id]))
            else:
                category = item.get("_category")
                if not category:
                    raise NotFoundException(
                        _(f"Kategoriya topilmadi (id={item.get('category_id')})")
                    )
                new_product = Product(
                    name=item.get("name"),
                    category=category,
                    company=company,
                    ikpu=item.get("ikpu"),
                    brand_id=item.get("brand_id"),
                )
                for field_name in ("name_uz", "name_ru", "name_en"):
                    if hasattr(new_product, field_name) and not getattr(
                        new_product, field_name, None
                    ):
                        setattr(new_product, field_name, new_product.name)
                products_to_create.append(new_product)
                order_products_data.append((item, len(products_to_create) - 1))

        created_products = (
            Product.objects.bulk_create(products_to_create)
            if products_to_create
            else []
        )
        Product.update_search_vectors([product.pk for product in created_products])
        return order_products_data, created_products

    @staticmethod
    def get_order_attributes() -> dict:
        """Order formalariga kerak bo'ladigan status, duration va brand variantlarini qaytaradi."""
        return {
            "status": [
                {
                    "value": value,
                    "label": label,
                }
                for value, label in OrderStatusChoices.choices
            ],
            "duration": [
                {
                    "value": duration.id,
                    "label": duration.name,
                }
                for duration in Duration.objects.all().order_by("months", "name")
            ],
            "brands": [
                {
                    "value": brand.id,
                    "label": brand.name,
                }
                for brand in Brand.objects.all().order_by("name")
            ],
        }

    def get_status_counts(self, *, queryset) -> list[dict[str, Any]]:
        """Berilgan queryset uchun asosiy statuslar kesimida buyurtma sonini qaytaradi."""
        status_values = [
            OrderStatusChoices.PENDING_PARTNER,
            OrderStatusChoices.QABZ_PENDING,
            OrderStatusChoices.ON_WAY,
            OrderStatusChoices.RETURN_PENDING_CLIENT,
            OrderStatusChoices.ACTIVE,
        ]
        status_labels = dict(OrderStatusChoices.choices)

        counts = (
            queryset.filter(status__in=status_values)
            .order_by()
            .values("status")
            .annotate(count=Count("id"))
        )
        count_map = {item["status"]: item["count"] for item in counts}

        return [
            {
                "value": status_value,
                "label": str(status_labels.get(status_value, status_value)),
                "count": count_map.get(status_value, 0),
            }
            for status_value in status_values
        ]

    def _build_pricing_context(
        self,
        *,
        company_id: int,
        duration_id: int,
        category_ids: Iterable[Optional[int]],
        client_id: Optional[int] = None,
    ) -> PricingContext:
        """Bitta buyurtma uchun foizlar keshini quradi.

        Kesh bir marta quriladi va barcha qatorlarga qo'llanadi — `resolve_percent`
        qo'shimcha so'rov qilmaydi.
        """
        return self.pricing_service.build_context(
            duration_ids=[duration_id],
            category_ids={cid for cid in category_ids if cid},
            company_ids=[company_id],
            client_id=client_id,
        )

    @staticmethod
    def _prepared_category_ids(
        order_products_data: list[PreparedOrderProduct],
        created_products: list[Product],
    ) -> set[int]:
        """Tayyorlangan itemlardagi mahsulot kategoriyalarini yig'adi."""
        category_ids = set()
        for _, product_or_idx in order_products_data:
            product = (
                created_products[product_or_idx]
                if isinstance(product_or_idx, int)
                else product_or_idx
            )
            if product.category_id:
                category_ids.add(product.category_id)
        return category_ids

    def _build_order_products(
        self,
        order: Order,
        order_products_data: list[PreparedOrderProduct],
        created_products: list[Product],
        *,
        company_id: int,
        duration_id: int,
        pricing_context: PricingContext,
    ) -> list[OrderProduct]:
        """Saqlanmagan `OrderProduct` obyektlarini itemlar asosida tayyorlaydi."""
        order_products: list[OrderProduct] = []
        for item_data, product_or_idx in order_products_data:
            product = (
                created_products[product_or_idx]
                if isinstance(product_or_idx, int)
                else product_or_idx
            )
            quantity = item_data.get("quantity", 1)
            price_data = self.calculate_price_v2(
                company_id=company_id,
                category_id=product.category_id,
                duration_id=duration_id,
                price=item_data.get("base_price") or Decimal("0"),
                pricing_context=pricing_context,
            )
            marking = item_data.get("marking")
            imei = item_data.get("imei")
            for i in range(quantity):
                order_products.append(
                    OrderProduct(
                        order=order,
                        product=product,
                        base_price=price_data["base_price"],
                        prepayment=price_data["prepayment"],
                        markup_amount=price_data["markup_amount"],
                        price=price_data["price"],
                        marking=marking,
                        imei=imei,
                    )
                )
        return order_products

    def _apply_item_updates(
        self,
        order: Order,
        order_products_data: list[PreparedOrderProduct],
        created_products: list[Product],
        pricing_context: PricingContext,
        prepayment: Decimal = Decimal("0"),
        *,
        recalculate_totals: bool = True,
    ) -> None:
        """Order itemlarini yangilab, keraklisini yaratadi, ortiqchasini o'chiradi va jami summani qayta hisoblaydi."""
        existing_items_map: dict = defaultdict(list)
        for row in order.items.all().order_by("id"):
            existing_items_map[row.product_id].append(row)

        kept_item_ids: set[int] = set()
        rows_to_create: list[OrderProduct] = []

        for item_data, product_or_idx in order_products_data:
            product = (
                created_products[product_or_idx]
                if isinstance(product_or_idx, int)
                else product_or_idx
            )
            quantity = item_data.get("quantity", 1)
            marking = item_data.get("marking")
            imei = item_data.get("imei")
            base_price_raw = item_data.get("base_price") or Decimal("0")
            base_price = Decimal(str(base_price_raw))

            existing_rows = existing_items_map.get(product.id, [])
            to_update = min(quantity, len(existing_rows))

            for i in range(to_update):
                row = existing_rows[i]
                row.base_price = base_price
                row.prepayment = Decimal("0")
                if marking:
                    row.marking = marking
                if imei:
                    row.imei = imei
                row.save(update_fields=["base_price", "prepayment", "marking", "imei"])
                kept_item_ids.add(row.id)

            for i in range(quantity - len(existing_rows)):
                rows_to_create.append(
                    OrderProduct(
                        order=order,
                        product=product,
                        base_price=base_price,
                        prepayment=Decimal("0"),
                        markup_amount=Decimal("0"),
                        price=Decimal("0"),
                        marking=marking or None,
                        imei=imei or None,
                    )
                )

        if rows_to_create:
            OrderProduct.objects.bulk_create(rows_to_create)

        all_existing_ids = {
            row.id for rows in existing_items_map.values() for row in rows
        }
        ids_to_delete = all_existing_ids - kept_item_ids
        if ids_to_delete:
            OrderProduct.objects.filter(id__in=ids_to_delete).delete()

        current_items = list(order.items.select_related("product").all())
        total_prepayment = self._validate_prepayment_amount(
            prepayment, sum(item.base_price for item in current_items)
        )
        self._apply_pricing_to_items(
            current_items,
            total_prepayment=total_prepayment,
            company_id=order.company_id,
            duration_id=order.duration_id,
            pricing_context=pricing_context,
        )
        if current_items:
            OrderProduct.objects.bulk_update(
                current_items, ["prepayment", "markup_amount", "price"]
            )

        if recalculate_totals:
            self._recalculate_order_totals(order)

    def _get_delivery_service_price(
        self,
        region: Region,
    ) -> tuple[str, Optional[AdditionalServicePrice]]:
        """Regionga mos yetkazib berish xizmati turi va uning narxini topadi."""
        svc = get_delivery_external_service_type(region)
        if svc == ExternalSystemChoices.NESUVEZU:
            service_type = AdditionalServiceTypeChoices.DELIVERY_NESUVEZU
        else:
            service_type = AdditionalServiceTypeChoices.DELIVERY_BTS

        delivery_price_obj = AdditionalServicePrice.objects.filter(
            service_type=service_type
        ).first()
        return service_type, delivery_price_obj

    def apply_default_delivery_service(
        self, order: Order, region: Optional[Region]
    ) -> None:
        """Orderga region asosida standart delivery xizmatini ulaydi va summani yangilaydi."""
        # Avvalgi delivery xizmatlarini o'chirish
        OrderAdditionalService.objects.filter(
            order=order,
            service__service_type__in=[
                AdditionalServiceTypeChoices.DELIVERY_BTS,
                AdditionalServiceTypeChoices.DELIVERY_NESUVEZU,
            ],
        ).delete()

        if region:
            service_type, delivery_price_obj = self._get_delivery_service_price(region)
            if delivery_price_obj:
                OrderAdditionalService.objects.create(
                    order=order,
                    service=delivery_price_obj,
                    price=delivery_price_obj.price,
                )

        self._recalculate_order_totals(order)

    def remove_delivery_services(self, order: Order) -> None:
        """Orderdagi delivery bilan bog'liq qo'shimcha xizmatlarni olib tashlaydi."""
        OrderAdditionalService.objects.filter(
            order=order,
            service__service_type__in=[
                AdditionalServiceTypeChoices.DELIVERY_BTS,
                AdditionalServiceTypeChoices.DELIVERY_NESUVEZU,
            ],
        ).delete()
        self._recalculate_order_totals(order)

    def _create_delivery_address(
        self,
        order: Order,
        delivery_data: dict[str, Any],
    ) -> DeliveryAddress:
        """Tasdiqlangan delivery ma'lumotlaridan yetkazib berish manzilini yaratadi."""

        recipient_name = delivery_data.get("recipient_name", order.client.full_name_)
        recipient_phone = delivery_data.get("recipient_phone", order.client.phone)

        region = delivery_data.get("region")
        service = get_delivery_external_service_type(region)

        delivery = DeliveryAddress.objects.create(
            order=order,
            pickup_address=delivery_data.get("pickup_address"),
            region=region,
            district=delivery_data.get("district"),
            bts_filial=delivery_data.get("bts_filial"),
            street=delivery_data.get("street"),
            apartment=delivery_data.get("apartment"),
            entrance=delivery_data.get("entrance"),
            floor=delivery_data.get("floor"),
            location=delivery_data.get("location"),
            courier_note=delivery_data.get("courier_note"),
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            scheduled_start=delivery_data.get("scheduled_start"),
            scheduled_end=delivery_data.get("scheduled_end"),
            external_fields=delivery_data.get("external_fields"),
            service=service,
        )

        # Region bo'yicha default delivery xizmatini qo'shish va orderni qayta hisoblash
        self.apply_default_delivery_service(order, delivery.region)

        return delivery

    # ---------------------------------------------------------------------------
    # update_order
    # ---------------------------------------------------------------------------

    def update_order(
        self,
        order: Order,
        items_data: Optional[list[OrderItemPayload]] = None,
        duration_id: Optional[int] = None,
        prepayment: Optional[Decimal] = None,
        first_payment: Optional["date"] = None,
        additional_services: Optional[list[AdditionalServicePrice]] = None,
        pick_up_address: Optional[str] = None,
        created_by: Optional[Role] = None,
    ) -> Order:
        """Qoralama yoki hamkor kutayotgan buyurtmaning tarkibi va parametrlarini yangilaydi."""
        self.validate_order_editable_by_status(order)

        status_changed_to_draft = False
        resolved_prepayment = Decimal(str(order.prepayment))

        resolved_first_payment = self._get_default_first_payment(
            first_payment or order.first_payment
        )
        if order.first_payment != resolved_first_payment:
            order.first_payment = resolved_first_payment
            order.save(update_fields=["first_payment"])

        if pick_up_address is not None:
            order.pick_up_address = pick_up_address
            order.save(update_fields=["pick_up_address"])

        if duration_id and order.duration_id != duration_id:
            try:
                duration = Duration.objects.get(id=duration_id)
                order.duration = duration
                order.save(update_fields=["duration"])
                status_changed_to_draft = True
            except Duration.DoesNotExist:
                raise NotFoundException(_("Muddat topilmadi"))

        if prepayment is not None:
            resolved_prepayment = Decimal(str(prepayment))
            if resolved_prepayment != order.prepayment:
                status_changed_to_draft = True

        if items_data is not None:
            if not items_data:
                raise BadRequestException(_("Kamida bitta mahsulot kerak"))
            status_changed_to_draft = True

        if additional_services is not None:
            status_changed_to_draft = True

        try:
            with transaction.atomic():
                active_application = order.client.get_active_limit
                current_limit_history = LimitHistory.objects.filter(order=order).first()
                needs_totals_recalculation = False

                if items_data is not None:
                    order_products_data, created_products = (
                        self._resolve_items_to_products(items_data, order.company)
                    )
                    pricing_context = self._build_pricing_context(
                        company_id=order.company_id,
                        duration_id=order.duration_id,
                        category_ids=self._prepared_category_ids(
                            order_products_data, created_products
                        ),
                        client_id=order.client_id,
                    )
                    self._apply_item_updates(
                        order,
                        order_products_data,
                        created_products,
                        pricing_context,
                        prepayment=resolved_prepayment,
                        recalculate_totals=False,
                    )
                    needs_totals_recalculation = True
                elif status_changed_to_draft:
                    # Duration/prepayment changed but items were not replaced.
                    self._reprice_existing_items(
                        order,
                        total_prepayment=resolved_prepayment,
                        recalculate_totals=False,
                    )
                    needs_totals_recalculation = True

                if additional_services is not None:
                    order.additional_services.all().delete()
                    if additional_services:
                        OrderAdditionalService.objects.bulk_create(
                            [
                                OrderAdditionalService(
                                    order=order, service=sp, price=sp.price
                                )
                                for sp in additional_services
                                if sp
                            ]
                        )
                    needs_totals_recalculation = True

                if needs_totals_recalculation:
                    current_limit_history = self._recalculate_order_totals(
                        order,
                        application=active_application,
                        limit_history=current_limit_history,
                    )

                self._validate_private_balance(order.client, order.prepayment)

                self._validate_client_limit(
                    client=order.client,
                    duration=order.duration,
                    total_price=Decimal(order.price),
                    prepayment=Decimal(order.prepayment),
                    current_order=order,
                    current_reserved=current_limit_history,
                )

                v_items_data = items_data or [
                    {
                        "product_id": op.product.id,
                        "quantity": 1,
                        "base_price": op.base_price,
                        "marking": op.marking,
                    }
                    for op in order.items.select_related("product")
                ]

                is_valid, error_msg = self._validate_order_for_verification(
                    client=order.client,
                    duration=order.duration,
                    items_data=v_items_data,
                    total_price=order.price,
                )

                if not is_valid:
                    raise BadRequestException(error_msg)

                if status_changed_to_draft:
                    comment = "Buyurtma muddati yoki mahsulotlari yangilandi"
                    if order.status == OrderStatusChoices.PENDING_PARTNER:
                        comment = "Hamkor tomonidan buyurtma o'zgartirilganligi sababli buyurtma qoralamaga qaytarildi"
                    self.change_order_status(
                        order=order,
                        new_status=OrderStatusChoices.DRAFT,
                        created_by=created_by,
                        comment=comment,
                    )

            return order

        except Exception as e:
            raise BadRequestException(str(e))

    def _reprice_existing_items(
        self,
        order: Order,
        total_prepayment: Optional[Decimal] = None,
        *,
        recalculate_totals: bool = True,
    ) -> None:
        """Mavjud itemlar uchun narxlarni yangi muddat yoki prepayment bo'yicha qayta hisoblaydi."""
        items = list(order.items.select_related("product").all())
        pricing_context = self._build_pricing_context(
            company_id=order.company_id,
            duration_id=order.duration_id,
            category_ids=[item.line_category_id for item in items],
            client_id=order.client_id,
        )
        if items:
            validated_prepayment = self._validate_prepayment_amount(
                total_prepayment if total_prepayment is not None else order.prepayment,
                sum(item.base_price for item in items),
            )
            self._apply_pricing_to_items(
                items,
                total_prepayment=validated_prepayment,
                company_id=order.company_id,
                duration_id=order.duration_id,
                pricing_context=pricing_context,
            )
            OrderProduct.objects.bulk_update(
                items, ["prepayment", "markup_amount", "price"]
            )
        if recalculate_totals:
            self._recalculate_order_totals(order)

    def submit_order(self, order: Order, created_by: Optional[Role] = None) -> Order:
        """Qoralama buyurtmani tekshiruvga yuborib, keyingi statuslarga o'tkazadi."""
        if order.status != OrderStatusChoices.DRAFT:
            raise BadRequestException(_("Faqat qoralama buyurtmani yuborish mumkin"))

        if order.items.count() == 0:
            raise BadRequestException(_("Kamida bitta mahsulot kerak"))

        # Status o'zgartirish va log yozish
        self.change_order_status(
            order=order,
            new_status=OrderStatusChoices.PENDING_VERIFICATION,
            created_by=created_by,
            comment="Buyurtma moderatsiyaga yuborildi (Avtomatik tizim)",
        )
        self.change_order_status(
            order=order,
            new_status=OrderStatusChoices.PENDING_PARTNER,
            created_by=created_by,
            comment="Buyurtma tasdiqlandi, hamkor kutilmoqda",
        )

        return order

    def get_identifiers_status(self, order: Order) -> dict:
        """Buyurtmadagi itemlar uchun IMEI yoki markirovka to'liq kiritilganini tekshiradi."""
        items = []
        items_incomplete = 0

        for item in order.items.select_related(
            "product",
            "product__category",
        ).all():
            category = item.line_category
            marking_type = category.marking_type if category else None

            # Determine if identifier is required
            requires_identifier = marking_type in ["imei", "marking"]

            # Check if complete
            if requires_identifier:
                if marking_type == "imei" and item.imei:
                    is_complete = True
                elif marking_type == "marking" and item.marking:
                    is_complete = True
                else:
                    is_complete = False
            else:
                is_complete = True  # No identifier required

            if not is_complete:
                items_incomplete += 1

            items.append(
                {
                    "id": item.id,
                    "product_name": item.display_name,
                    "required_marking_type": marking_type,
                    "imei": item.imei,
                    "marking": item.marking,
                    "is_complete": is_complete,
                }
            )
        if order.status == OrderStatusChoices.DRAFT:
            self.change_order_status(
                order, OrderStatusChoices.PENDING_PARTNER, order.created_by
            )
            order.status = OrderStatusChoices.PENDING_PARTNER

        total_items = len(items)
        is_complete = items_incomplete == 0
        can_confirm = is_complete and order.status == OrderStatusChoices.PENDING_PARTNER

        return {
            "is_complete": is_complete,
            "can_confirm": can_confirm,
            "total_items": total_items,
            "items_incomplete": items_incomplete,
            "items": items,
        }

    def set_delivery(
        self,
        order: Order,
        address_data: dict[str, Any],
    ) -> Order:
        """Buyurtma uchun yetkazib berish manzilini saqlaydi yoki yangilaydi."""
        if order.status not in [
            OrderStatusChoices.QABZ_PENDING,
            OrderStatusChoices.QABZ_COMPLETED,
            OrderStatusChoices.PENDING_VERIFICATION,
            OrderStatusChoices.PENDING_PARTNER,
        ]:
            raise BadRequestException(
                _(
                    "Manzilni faqat qabz yoki tekshiruvdagi buyurtmalar uchun o'zgartirish mumkin"
                )
            )

        region_id = address_data.get("region_id")
        district_id = address_data.get("district_id")

        region = Region.objects.filter(id=region_id).first()
        if not region:
            raise NotFoundException(_("Viloyat topilmadi"))

        district = Region.objects.filter(id=district_id).first()
        if not district:
            raise NotFoundException(_("Tuman topilmadi"))

        if district.parent_id != region.id:
            raise BadRequestException(
                _("Tanlangan tuman ushbu viloyatga tegishli emas")
            )

        with transaction.atomic():
            DeliveryAddress.objects.update_or_create(
                order=order,
                defaults={
                    "region": region,
                    "district": district,
                    "street": address_data.get("street"),
                    "apartment": address_data.get("apartment"),
                    "entrance": address_data.get("entrance"),
                    "floor": address_data.get("floor"),
                    "location": address_data.get("location"),
                    "courier_note": address_data.get("courier_note"),
                    "recipient_name": address_data.get("recipient_name"),
                    "recipient_phone": address_data.get("recipient_phone"),
                },
            )

            # Auto-calculate delivery additional service based on region SOATO
            svc = get_delivery_external_service_type(region)
            if svc == ExternalSystemChoices.NESUVEZU:
                service_type = AdditionalServiceTypeChoices.DELIVERY_NESUVEZU
            else:
                service_type = AdditionalServiceTypeChoices.DELIVERY_BTS

            delivery_price_obj = AdditionalServicePrice.objects.filter(
                service_type=service_type
            ).first()
            price = delivery_price_obj.price if delivery_price_obj else Decimal("0")

            # Remove previous delivery services if they exist (BTS/NesuVezu)
            OrderAdditionalService.objects.filter(
                order=order,
                service__service_type__in=[
                    AdditionalServiceTypeChoices.DELIVERY_BTS,
                    AdditionalServiceTypeChoices.DELIVERY_NESUVEZU,
                ],
            ).delete()

            # Create new delivery attached to this order
            OrderAdditionalService.objects.create(
                order=order, service=delivery_price_obj, price=price
            )

            self._recalculate_order_totals(order)

        return order

    def set_pickup_branch(
        self,
        order: Order,
        branch_id: int,
    ) -> Order:
        """Olib ketish turi uchun buyurtmaga filial manzilini biriktiradi."""
        if order.status not in [
            OrderStatusChoices.QABZ_PENDING,
            OrderStatusChoices.PENDING_PARTNER,
        ]:
            raise BadRequestException(
                _(
                    "Pickup filialini faqat qutilyotgan yoki filialdagi buyurtmalar uchun o'zgartirish mumkin"
                )
            )

        branch = Branch.objects.filter(id=branch_id, company=order.company).first()
        if not branch:
            raise NotFoundException(
                _("Filial topilmadi yoki bu korxonaga tegishli emas")
            )

        with transaction.atomic():
            DeliveryAddress.objects.update_or_create(
                order=order,
                defaults={
                    "pickup_address": branch.address,
                    "region": branch.region if hasattr(branch, "region") else None,
                    "district": (
                        branch.district if hasattr(branch, "district") else None
                    ),
                },
            )

        return order

    def _create_merchant_payments_by_type(
        self,
        order: Order,
        payment_type: str,
        allowed_statuses: list[str],
    ) -> None:
        """Order itemlari bo'yicha kerakli turdagi merchant payment yozuvlarini yaratadi."""
        if order.status not in allowed_statuses:
            return None

        if order.merchant_payments.filter(type=payment_type).exists():
            return None

        payments_to_create = []
        items = order.items.all()
        company = order.company

        for item in items:
            payments_to_create.append(
                MerchantPayment(
                    company=company,
                    order=order,
                    product=item,
                    amount=item.base_price,  # Merchant uchun mahsulotning asl narxi
                    paid_amount=Decimal("0"),
                    type=payment_type,
                    status=MerchantPaymentStatusChoices.PENDING,
                    paid_at=None,
                )
            )

        if payments_to_create:
            MerchantPayment.objects.bulk_create(payments_to_create)

    def create_merchant_payments(self, order: Order) -> None:
        """Qabz qilingan buyurtma itemlari bo'yicha payment turidagi merchant payment yaratadi."""
        self._create_merchant_payments_by_type(
            order=order,
            payment_type=MerchantPaymentTypeChoices.PAYMENT,
            allowed_statuses=[OrderStatusChoices.QABZ_COMPLETED],
        )

    def create_merchant_refund_payments(self, order: Order) -> None:
        """Bekor qilingan yoki qaytarilgan buyurtma itemlari bo'yicha refund turidagi merchant payment yaratadi."""
        self._create_merchant_payments_by_type(
            order=order,
            payment_type=MerchantPaymentTypeChoices.REFUND,
            allowed_statuses=[
                OrderStatusChoices.CANCELLED_AMANAT,
                OrderStatusChoices.RETURNED,
            ],
        )

    def update_identifiers(
        self, order: Order, items_data: list[dict[str, Any]]
    ) -> Order:
        """Hamkor kiritgan IMEI va markirovkalarni buyurtma itemlariga saqlaydi."""
        if order.status != OrderStatusChoices.PENDING_PARTNER:
            raise BadRequestException(
                _(
                    "IMEI/markirovka faqat hamkor kutilmoqda statusidagi buyurtma uchun kiritish mumkin"
                )
            )

        errors = []

        try:
            with transaction.atomic():
                for item_data in items_data:
                    item_id = item_data.get("id")
                    imei = item_data.get("imei")
                    marking = item_data.get("marking")

                    try:
                        order_item = order.items.get(id=item_id)
                    except OrderProduct.DoesNotExist:
                        errors.append(_(f"Item {item_id} topilmadi"))
                        continue

                    if imei is not None:
                        if self.check_available_imei(imei, order_item.id):
                            errors.append(
                                _(f"IMEI {imei} boshqa qurilmaga biriktirilgan")
                            )
                            continue
                        order_item.imei = imei
                    if marking is not None:
                        order_item.marking = marking

                    order_item.save(update_fields=["imei", "marking"])

            if errors:
                error_msg = ", ".join(str(e) for e in errors)
                raise BadRequestException(
                    format_lazy("Ba'zi itemlar yangilanmadi: {msg}", msg=error_msg)
                )

            return order

        except Exception as e:
            raise BadRequestException(str(e))

    def check_available_imei(self, imei: str, order_product_id: int = None) -> bool:
        """IMEI boshqa aktiv buyurtma itemiga biriktirilganini tekshiradi."""
        inactive_statuses = [
            OrderStatusChoices.DRAFT,
            OrderStatusChoices.CANCELLED_MERCHANT,
            OrderStatusChoices.CANCELLED_AMANAT,
            OrderStatusChoices.RETURNED,
        ]
        return (
            OrderProduct.objects.filter(imei=imei)
            .exclude(Q(id=order_product_id) | Q(order__status__in=inactive_statuses))
            .exists()
        )

    def _is_item_identifier_complete(self, order_item: OrderProduct) -> bool:
        """Order item uchun talab qilingan identifikator to'liq kiritilganini tekshiradi."""
        category = order_item.line_category
        marking_type = category.marking_type if category else None

        if marking_type == "imei":
            return bool(order_item.imei)
        elif marking_type == "marking":
            return bool(order_item.marking)
        else:
            return True  # No identifier required

    def approve_order(self, order: Order, created_by: Role) -> Order:
        """Tekshiruvdan o'tgan buyurtmani hamkor bosqichiga o'tkazadi."""
        if order.status != OrderStatusChoices.PENDING_VERIFICATION:
            raise BadRequestException(_("Buyurtma tekshiruv kutilmoqda statusida emas"))

        # TODO: Scoring check
        # TODO: Limit check

        self.change_order_status(
            order=order,
            new_status=OrderStatusChoices.PENDING_PARTNER,
            created_by=created_by,
            comment="Buyurtma tasdiqlandi, hamkor kutilmoqda",
        )

        return order

    def confirm_identifiers(self, order: Order, created_by: Role) -> Order:
        """IMEI va markirovkalar to'liq bo'lsa, buyurtmani merchant tasdiqlash bosqichidan o'tkazadi."""
        if order.status != OrderStatusChoices.PENDING_PARTNER:
            raise BadRequestException(_("Buyurtma hamkor kutilmoqda statusida emas"))

        identifiers_status = self.get_identifiers_status(order)
        if not identifiers_status["is_complete"]:
            incomplete_items = [
                item["product_name"]
                for item in identifiers_status["items"]
                if not item["is_complete"]
            ]
            raise BadRequestException(
                _(f"IMEI/markirovkalar kiritilmagan: {', '.join(incomplete_items)}")
            )

        with transaction.atomic():
            self.create_payment_schedule(order, first_payment=order.first_payment)
            transaction.on_commit(lambda: generate_contract_pdf_task.delay(order.id))

            self.change_order_status(
                order=order,
                new_status=OrderStatusChoices.QABZ_PENDING,
                created_by=created_by,
                comment="Merchant buyurtmani tasdiqladi",
            )

            if hasattr(order, "delivery") and order.delivery:
                if settings.IS_PRODUCTION:
                    #  partially import
                    from services.delivery import DeliveryService

                    delivery_service = DeliveryService()
                    delivery_service.send_to_external_service(order, order.delivery.service)

        return order

    def confirm_receipt(
        self,
        order: Order,
        created_by: Role,
        images: Optional[list[Any]] = None,
    ) -> Order:
        """Qabz manager yukni qabul qilganini tasdiqlab, dalil rasmlarini saqlaydi."""
        if order.status != OrderStatusChoices.QABZ_PENDING:
            raise BadRequestException(
                _("Faqat qabz kutilmoqda bo'lgan buyurtmani qabul qilish mumkin.")
            )

        if not images:
            raise BadRequestException(_("Qabz uchun kamida bitta rasm yuklash shart."))

        with transaction.atomic():
            self.change_order_status(
                order=order,
                new_status=OrderStatusChoices.QABZ_COMPLETED,
                created_by=created_by,
                comment="Qabz manager (yukni) qabul qildi",
            )
            self.create_merchant_payments(order)
            # Create receipt images
            receipt_images = [
                OrderReceiptImage(
                    order=order, image=img, type=ReceiptImageTypeChoices.RECEIVED
                )
                for img in images
            ]
            OrderReceiptImage.objects.bulk_create(receipt_images)

        return order

    def confirm_pickup_with_otp(
        self, order: Order, otp_code: str, created_by: Optional[Role] = None
    ) -> Order:
        """Pickup buyurtmasini OTP orqali tasdiqlab, aktiv holatga o'tkazadi."""
        if order.status not in [
            OrderStatusChoices.QABZ_COMPLETED,
            OrderStatusChoices.ON_WAY,
        ]:
            raise BadRequestException(
                _(
                    "Faqat Qabz qilingan buyurtmani olib ketish orqali tasdiqlash mumkin."
                )
            )

        # Verify OTP
        otp_service = OTPService()
        is_valid, msg = otp_service.verify_receipt_otp(order=order, code=otp_code)

        if not is_valid:
            raise BadRequestException(msg)

        # To'g'ridan to'g'ri ACTIVE ga o'tish
        with transaction.atomic():
            self.activate_order(
                order=order,
                created_by=created_by,
                comment="Mijoz filialdan OTP orqali olib ketdi",
            )
            transaction.on_commit(lambda: generate_contract_pdf_task.delay(order.id))

        return order

    def start_delivery(self, order: Order, created_by: Role) -> Order:
        """Qabz qilingan buyurtma uchun yetkazib berish jarayonini boshlaydi."""
        if order.status != OrderStatusChoices.QABZ_COMPLETED:
            raise BadRequestException(_("Buyurtma qabz statusida emas"))

        self.change_order_status(
            order=order,
            new_status=OrderStatusChoices.ON_WAY,
            created_by=created_by,
            comment="Yetkazish boshlandi",
        )

        return order

    def confirm_delivery(self, order: Order) -> Order:
        """Yo'ldagi buyurtma mijozga topshirilganini tasdiqlaydi."""
        if order.status != OrderStatusChoices.ON_WAY:
            raise BadRequestException(_("Buyurtma yo'lda statusida emas"))

        self.change_order_status(
            order=order,
            new_status=OrderStatusChoices.DELIVERED,
            created_by=None,  # Client confirms
            comment="Mijoz tovarni qabul qildi",
        )

        return order

    def complete_order(
        self,
        order: Order,
        created_by: Optional[Role] = None,
        images: Optional[list[Any]] = None,
    ) -> Order:
        """Buyurtma topshirilganini rasmlar bilan yakuniy tasdiqlaydi."""
        if order.status != OrderStatusChoices.QABZ_COMPLETED:
            raise BadRequestException(
                _(
                    "Buyurtma hali qabul qilinmagan yoki yo'lda, uni faollashtirish mumkin emas"
                )
            )

        if not images:
            raise BadRequestException(
                _("Tugatish uchun kamida bitta rasm yuklash shart.")
            )

        with transaction.atomic():
            # Client bilan tushgan rasmlarni saqlash
            receipt_images = [
                OrderReceiptImage(
                    order=order, image=img, type=ReceiptImageTypeChoices.HANDED_OVER
                )
                for img in images
            ]
            OrderReceiptImage.objects.bulk_create(receipt_images)

        # Production muhitida tashqi tizimlarga yuborish (async)
        if settings.IS_PRODUCTION:
            upload_devices_task.delay(order.id)

        return order

    def cancel_order(
        self,
        order: Order,
        created_by: Role,
        reason: str = "",
    ) -> Order:
        """Buyurtmani merchant yoki Amanat tomondan bekor statusiga o'tkazadi."""
        reason = (reason or "").strip()
        amanat_staff = amanat_staff_by_role(created_by)

        if order.status in [
            OrderStatusChoices.CANCELLED_MERCHANT,
            OrderStatusChoices.CANCELLED_AMANAT,
            OrderStatusChoices.RETURN_PENDING_CLIENT,
            OrderStatusChoices.RETURN_PENDING_MERCHANT,
            OrderStatusChoices.RETURNED,
        ]:
            raise BadRequestException(_("Buyurtma allaqachon bekor qilingan"))

        allowed_statuses = [
            OrderStatusChoices.DRAFT,
            OrderStatusChoices.PENDING_VERIFICATION,
            OrderStatusChoices.PENDING_PARTNER,
            OrderStatusChoices.QABZ_PENDING,
        ]

        status = OrderStatusChoices.CANCELLED_MERCHANT
        if amanat_staff:
            status = OrderStatusChoices.CANCELLED_AMANAT

        if order.status not in allowed_statuses:
            raise BadRequestException(_("Yakunlangan buyurtmani bekor qilib bo'lmaydi"))

        self._process_terminal_order_effects(
            order=order,
            new_status=status,
            created_by=created_by,
            comment=reason or "Buyurtma bekor qilindi",
            create_merchant_refund=False,
        )

        return order

    def return_order(self, order: Order, created_by: Role, reason: str = "") -> Order:
        """Amanat staff tomonidan buyurtmani return pending holatiga o'tkazadi."""
        reason = (reason or "").strip()
        if not amanat_staff_by_role(created_by):
            raise BadRequestException(_("Buyurtmani qaytarishga faqat Amanat staff ruxsat etilgan"))

        if order.status in [
            OrderStatusChoices.CANCELLED_MERCHANT,
            OrderStatusChoices.CANCELLED_AMANAT,
            OrderStatusChoices.RETURN_PENDING_CLIENT,
            OrderStatusChoices.RETURN_PENDING_MERCHANT,
            OrderStatusChoices.RETURNED,
        ]:
            raise BadRequestException(_("Buyurtma allaqachon bekor qilingan"))

        if order.status not in [
            OrderStatusChoices.QABZ_COMPLETED,
            OrderStatusChoices.ON_WAY,
            OrderStatusChoices.DELIVERED,
            OrderStatusChoices.ACTIVE,
        ]:
            raise BadRequestException(_("Bu statusdagi buyurtmani qaytarib bo'lmaydi"))

        next_status = OrderStatusChoices.RETURN_PENDING_CLIENT
        default_comment = "Buyurtma qaytarilishi client tasdig'iga yuborildi"
        if order.status == OrderStatusChoices.QABZ_COMPLETED:
            next_status = OrderStatusChoices.RETURN_PENDING_MERCHANT
            default_comment = "Buyurtma qaytarilishi merchant tasdig'iga yuborildi"

        self.change_order_status(
            order=order,
            new_status=next_status,
            created_by=created_by,
            comment=reason or default_comment,
        )

        return order

    def confirm_return_by_client(
        self, order: Order, client: Client, reason: str = ""
    ) -> Order:
        """Client tomonidan qaytarishni merchant bosqichiga o'tkazadi."""
        reason = (reason or "").strip()

        if order.client_id != client.id:
            raise BadRequestException(_("Faqat buyurtma egasi qaytarishni tasdiqlashi mumkin"))

        if order.status != OrderStatusChoices.RETURN_PENDING_CLIENT:
            raise BadRequestException(
                _("Faqat client tasdig'i kutilayotgan buyurtmani tasdiqlash mumkin")
            )

        self.change_order_status(
            order=order,
            new_status=OrderStatusChoices.RETURN_PENDING_MERCHANT,
            created_by=None,
            comment=reason or "Buyurtma qaytarilishi merchant tasdig'iga yuborildi",
        )

        return order

    def confirm_return(self, order: Order, created_by: Role, reason: str = "") -> Order:
        """Merchant tomonidan return pending buyurtmani yakuniy returned holatiga o'tkazadi."""
        reason = (reason or "").strip()
        if amanat_staff_by_role(created_by):
            raise BadRequestException(_("Buyurtma qaytarilishini merchant tasdiqlashi kerak"))

        if order.status != OrderStatusChoices.RETURN_PENDING_MERCHANT:
            raise BadRequestException(
                _("Faqat merchant tasdig'i kutilayotgan buyurtmani tasdiqlash mumkin")
            )

        self._process_terminal_order_effects(
            order=order,
            new_status=OrderStatusChoices.RETURNED,
            created_by=created_by,
            comment=reason or "Buyurtma qaytarildi",
            create_merchant_refund=True,
        )

        return order

    def process_debitor_for_sms(
        self, order: Order
    ) -> tuple[Optional[str], Optional[str]]:
        """Kechikkan to'lov holatiga qarab SMS matnini tayyorlaydi va log yozadi."""

        overdue = order.overdue
        days = overdue["days"]
        text = None

        first_schedule = (
            order.payment_schedules.filter(period_number__gt=0)
            .order_by("period_number")
            .first()
        )
        if not first_schedule:
            return None, None

        due_date = first_schedule.due_date.strftime("%d.%m.%Y")
        amount = first_schedule.planned_amount
        order_number = order.number

        if days == -1:
            text = SMSMessages.format(
                SMSMessages.DEBT_REMINDER_TOMORROW,
                due_date=due_date,
                order_number=order_number,
                amount=amount,
            )
        elif days in (1, 3, 7):
            text = SMSMessages.format(
                SMSMessages.DEBT_OVERDUE_WARNING,
                due_date=due_date,
                order_number=order_number,
                amount=amount,
            )
        elif days in (15, 30):
            text = SMSMessages.format(
                SMSMessages.DEBT_LEGAL_ACTION, order_number=order_number, amount=amount
            )

        if text:
            DebtSMSlog.objects.create(order=order, client=order.client, text=text)

            if days != 0:
                collector_service = CollectorService()
                collector_service.register_action(
                    order=order,
                    created_by="system",
                    action="SENT_SMS",
                    note=f"SMS yuborildi: {text[:100]}...",
                )

            return text, order.client.phone

        return None, None

    def create_draft_order(
        self,
        *,
        client: Client,
        duration: Duration,
        base_price: Decimal,
        products_data: list[dict[str, Any]],
        additional_services: Optional[list[AdditionalServicePrice]] = None,
    ) -> DraftOrder:
        """Limit yetmaganda keyinroq ko'rib chiqish uchun draft buyurtma yaratadi."""
        company = Company.objects.filter(tin="305379480").first()
        if not company:
            company = Company.objects.first()

        with transaction.atomic():
            services_total = Decimal("0")
            if additional_services:
                for sp in additional_services:
                    if sp:
                        services_total += sp.price

            draft_order = DraftOrder.objects.create(
                client=client,
                company=company,
                duration=duration,
                base_price=base_price,
                markup_amount=Decimal("0"),
                price=base_price + services_total,
            )

            # NOTE: DraftOrder doesn't currently have additional_services relation,
            # if needed we can create DraftOrderAdditionalService, but for now it's
            # enough to just add it to the draft total.

            draft_items = []
            pricing_context = self._build_pricing_context(
                company_id=company.id,
                duration_id=duration.id,
                category_ids=[item["product"].category_id for item in products_data],
                client_id=client.id,
            )

            for item in products_data:
                product = item["product"]
                quantity = item["quantity"]
                base_price_from_data = item["price"]  # Renamed from unit_price

                price_data = self.calculate_price_v2(
                    company_id=company.id,
                    category_id=product.category_id,
                    duration_id=duration.id,
                    price=base_price_from_data,
                    pricing_context=pricing_context,
                )

                markup_amount = price_data["markup_amount"]
                final_price = price_data["price"]

                draft_items.append(
                    DraftOrderProduct(
                        draft_order=draft_order,
                        product=product,
                        quantity=quantity,
                        base_price=base_price_from_data,
                        markup_amount=markup_amount,
                        price=final_price,
                    )
                )

            DraftOrderProduct.objects.bulk_create(draft_items)

            # Recalculate totals
            total_base = sum(item.base_price * item.quantity for item in draft_items)
            total_markup = sum(
                item.markup_amount * item.quantity for item in draft_items
            )
            total_price = sum(item.price * item.quantity for item in draft_items)

            draft_order.base_price = total_base
            draft_order.markup_amount = total_markup
            draft_order.price = total_price + services_total
            draft_order.save(update_fields=["base_price", "markup_amount", "price"])

        return draft_order

    @transaction.atomic
    def pay_order_with_plum(self, order: Order, card: Cards, amount: int):
        """Оплата заказа с помощью карты Plum"""
        if amount <= 0:
            raise BadRequestException("Noto'g'ri summa")

        card_service = CardService()
        res = card_service.payment(card=card, amount=amount)

        trans = Transactions.objects.create(
            order=order,
            client=order.client,
            source=TransactionSourceChoices.PLUM,
            type=TransactionType.PAYMENT,
            status=WalletTransactionStatus.SUCCESS,
            transaction_id=res.get("transactionId"),
            description="client",
            amount=amount,
            specific_data={
                "card_number": res.get("cardNumber", card.card_number),
                "plum_response": res,
            },
        )

        return {
            "success": True,
            "transaction_id": trans.id,
            "provider_id": res.get("transactionId"),
            "amount": amount,
        }

    def _get_monthly_amount(self, order_name: str, partner: str) -> int:
        """Получить сумму ежемесячного платежа в зависимости от партнера"""
        now = timezone.now().date()
        end_of_month = date(now.year, now.month, monthrange(now.year, now.month)[1])

        if partner == PartnerPayment.amanat:
            order = Order.objects.filter(number=order_name).first()
            if not order:
                return 0

            schedules = order.payment_schedules.filter(
                period_number__gt=0,
                due_date__date__lte=end_of_month,
            ).order_by("due_date")

            if not schedules.exists():
                schedules = order.payment_schedules.filter(
                    period_number__gt=0,
                    due_date__date__lt=now,
                ).order_by("due_date")

            total_planned = sum(s.planned_amount for s in schedules)
            total_paid_on_order = order.total_paid

            recommended = max(Decimal("0"), total_planned - total_paid_on_order)

            if recommended == 0:
                next_schedule = (
                    order.payment_schedules.filter(period_number__gt=0)
                    .order_by("due_date")
                    .first()
                )
                if next_schedule:
                    return int(next_schedule.planned_amount)

            return int(recommended)

        else:  # Radius
            try:
                radius_service = RadiusCRMService()
                data = radius_service.get_state_from_1c(order_name)

                order_data = data.get("order", {})
                state_data = data.get("state", [])
                schedule_table = order_data.get("payment_schedule", [])

                recommended = 0
                for t in schedule_table:
                    date_obj = radius_service.convert_date(t["date"])
                    if date_obj and date_obj <= end_of_month:
                        plan = int(float(t["amount"]))
                        paid = sum(
                            int(float(s.get("fact") or 0))
                            for s in state_data
                            if s.get("datePlan") == t["date"]
                        )
                        if plan > paid:
                            recommended += plan - paid

                if recommended == 0 and schedule_table:
                    next_t = min(
                        (
                            t
                            for t in schedule_table
                            if radius_service.convert_date(t["date"])
                            and radius_service.convert_date(t["date"]) > end_of_month
                        ),
                        key=lambda t: radius_service.convert_date(t["date"]),
                        default=None,
                    )
                    if next_t:
                        recommended = int(float(next_t["amount"]))
                    else:
                        for t in schedule_table:
                            plan = int(float(t["amount"]))
                            paid = sum(
                                int(float(s.get("fact") or 0))
                                for s in state_data
                                if s.get("datePlan") == t["date"]
                            )
                            if plan > paid:
                                recommended = plan - paid
                                break

                return recommended
            except Exception:
                pass

        return 0

    def _get_client_passport(self, request) -> str:
        """Получить серию и номер паспорта клиента из запроса"""
        if getattr(request, "user", None) and getattr(request.user, "personal", None):
            return f"{request.user.personal.doc_series or ''}{request.user.personal.doc_number or ''}"
        return ""

    def get_payment_types_for_order(self, order_name: str, request=None) -> dict:
        is_radius = bool(re.match(r"^[A-Za-z]{2}-", order_name))
        partner = PartnerPayment.radius if is_radius else PartnerPayment.amanat

        payment_types = PaymentTypesForApp.objects.filter(
            is_active=True, partner_source=partner
        )
        if not payment_types.exists():
            return []

        amount = self._get_monthly_amount(order_name, partner)
        passport = (
            self._get_client_passport(request)
            if partner == PartnerPayment.radius
            else ""
        )

        replacements = {"{order_id}": str(order_name), "{amount}": str(amount)}
        if partner == PartnerPayment.radius:
            replacements["{passport}"] = passport
            replacements["{paspost}"] = passport

        payme_merchants = {
            PartnerPayment.radius: settings.PAYME_MERCHANT_ID_RADIUS,
            PartnerPayment.amanat: settings.PAYME_MERCHANT_ID_AMANAT,
        }

        result = []
        for pt in payment_types:
            name_lower = pt.name.lower()

            if (
                partner == PartnerPayment.radius
                and not passport
                and name_lower == "click"
            ):
                continue

            if name_lower == "payme" and partner in payme_merchants:
                params = f"m={payme_merchants[partner]};ac.order_id={order_name};a={amount * 100};l=ru"  # noqa
                deeplink = f"https://checkout.paycom.uz/{base64.b64encode(params.encode()).decode()}"  # noqa
            else:
                deeplink = pt.deeplink
                if deeplink:
                    for tag, value in replacements.items():
                        deeplink = deeplink.replace(tag, value)

            icon_url = None
            if pt.icon:
                icon_url = (
                    request.build_absolute_uri(pt.icon.url) if request else pt.icon.url
                )
                if icon_url and icon_url.startswith("http://"):
                    icon_url = icon_url.replace("http://", "https://")

            result.append(
                {
                    "name": pt.name,
                    "deeplink": deeplink,
                    "icon": icon_url,
                }
            )

        return {
            "recommended_amount": amount,
            "payment_types": result,
        }

    @staticmethod
    def get_debtor_orders():
        now = timezone.now()
        cumulative_due_sq = (
            PaymentSchedule.objects.filter(
                order=OuterRef("pk"),
                due_date__lt=now,
            )
            .values("order")
            .annotate(total=Sum("planned_amount"))
            .values("total")
        )
        paid_total_sq = (
            Transactions.objects.filter(
                order=OuterRef("pk"),
                type=TransactionType.PAYMENT,
                status=Status.SUCCESS,
            )
            .values("order")
            .annotate(total=Sum("amount"))
            .values("total")
        )
        overdue_orders = (
            Order.objects.filter(
                status__in=[
                    OrderStatusChoices.ACTIVE,
                    OrderStatusChoices.DELIVERED,
                ],
            )
            .annotate(
                cumulative_due=Coalesce(
                    Subquery(cumulative_due_sq),
                    Value(0),
                    output_field=DecimalField(),
                ),
                paid_total=Coalesce(
                    Subquery(paid_total_sq),
                    Value(0),
                    output_field=DecimalField(),
                ),
            )
            .filter(
                cumulative_due__gt=F("paid_total"),
            )
            .annotate(
                overdue_amount=F("cumulative_due") - F("paid_total"),
                first_overdue_date=Min(
                    "payment_schedules__due_date",
                    filter=Q(payment_schedules__due_date__lt=now),
                ),
            )
            .select_related("client", "company", "duration")
            .order_by("-overdue_amount")
        )
        return overdue_orders
