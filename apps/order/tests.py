from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from apps.company.models import Company
from apps.installment.models import Application
from apps.marketplace.choices import StatusChoices
from apps.marketplace.models import MarketplaceProduct
from apps.merchandise.choices import MarkupTypeChoices, ProductMarkingChoices
from apps.merchandise.models import Brand, Category, Duration, Markup, Product
from apps.order.choices import (
    AdditionalServiceTypeChoices,
    OrderSourceChoices,
    OrderStatusChoices,
)
from apps.order.models import AdditionalServicePrice, DraftOrder, Order, OrderProduct
from apps.user.models import Client, LimitHistory
from apps.utils.exceptions import BadRequestException
from services.marketplace_order import MarketplaceOrderService
from services.order import OrderService
from services.pricing import PricingService


class LimitStubMixin:
    """Skoring quyi tizimini chetlab o'tib, mijoz limitini boshqaradi.

    `Client.get_limit` -> `ClientService.calculate_limit` ni patch qiladi,
    shunda mijoz obyekti DB dan qayta yuklansa ham limit bir xil bo'ladi.
    """

    def stub_limit(self, monthly_limit):
        self._monthly_limit = Decimal(str(monthly_limit))
        if getattr(self, "_limit_patcher", None) is None:
            self._limit_patcher = patch(
                "services.client.ClientService.calculate_limit",
                side_effect=lambda client: self._monthly_limit,
            )
            self._limit_patcher.start()
            self.addCleanup(self._limit_patcher.stop)


class MarketplaceOrderTestCase(LimitStubMixin, TestCase):
    """Marketplace savatidan buyurtma yaratish oqimi."""

    _limit_patcher = None

    def setUp(self):
        self.service = MarketplaceOrderService()

        self.duration = Duration.objects.create(
            name="12 oy", months=12, percent=Decimal("20.00")
        )

        self.parent_category = Category.objects.create(name="Texnika")
        self.category = Category.objects.create(
            name="Muzlatgich", parent=self.parent_category
        )
        self.gsm_category = Category.objects.create(
            name="Telefon",
            parent=self.parent_category,
            marking_type=ProductMarkingChoices.IMEI,
        )

        self.merchant_a = Company.objects.create(name="Alfa", tin="111111111")
        self.merchant_b = Company.objects.create(name="Beta", tin="222222222")

        self.client_obj = Client.objects.create(
            full_name="Test Mijoz", phone="998900000001"
        )
        self.application = Application.objects.create(client=self.client_obj)
        self._set_limit(Decimal("10000000"))

    # ------------------------------------------------------------------
    # Yordamchi metodlar
    # ------------------------------------------------------------------

    def _set_limit(self, monthly_limit: Decimal):
        self.stub_limit(monthly_limit)
        self.client_obj.clear_limit_cache()
        self.client_obj._cached_active_limit = self.application

    def _make_product(self, merchant, price, *, category=None, name="Mahsulot", **kwargs):
        defaults = {
            "name": name,
            "merchant": merchant,
            "category": category or self.category,
            "price": Decimal(str(price)),
            "status": StatusChoices.ACTIVE,
            "ikpu": "12345678901234567",
            "sku": f"SKU-{name}",
        }
        defaults.update(kwargs)
        return MarketplaceProduct.objects.create(**defaults)

    def _create_raw(self, items, **kwargs):
        return self.service.create_orders_from_cart(
            client=self.client_obj,
            duration=self.duration,
            items=items,
            **kwargs,
        )

    def _create(self, items, **kwargs):
        """Muvaffaqiyatli oqim — buyurtmalar ro'yxatini qaytaradi."""
        result = self._create_raw(items, **kwargs)
        self.assertEqual(result["step"], "success")
        return result["orders"]

    # ------------------------------------------------------------------
    # Savatning merchantlar bo'yicha bo'linishi
    # ------------------------------------------------------------------

    def test_cart_splits_into_one_order_per_merchant(self):
        p1 = self._make_product(self.merchant_a, "1000000", name="A1")
        p2 = self._make_product(self.merchant_a, "2000000", name="A2")
        p3 = self._make_product(self.merchant_b, "3000000", name="B1")

        orders = self._create(
            [
                {"marketplace_product_id": p1.id, "quantity": 1},
                {"marketplace_product_id": p2.id, "quantity": 1},
                {"marketplace_product_id": p3.id, "quantity": 1},
            ]
        )

        self.assertEqual(len(orders), 2)
        by_company = {order.company_id: order for order in orders}
        self.assertEqual(set(by_company), {self.merchant_a.id, self.merchant_b.id})

        order_a = by_company[self.merchant_a.id]
        order_b = by_company[self.merchant_b.id]
        self.assertEqual(order_a.items.count(), 2)
        self.assertEqual(order_b.items.count(), 1)

        for order in orders:
            self.assertEqual(order.source, OrderSourceChoices.MARKETPLACE)
            self.assertEqual(order.status, OrderStatusChoices.DRAFT)
            self.assertEqual(order.client_id, self.client_obj.id)
            # Har order o'z merchantining kodi bilan raqamlanadi
            self.assertTrue(order.number.startswith(order.company.unique_code))

        self.assertEqual(order_a.base_price, Decimal("3000000"))
        self.assertEqual(order_b.base_price, Decimal("3000000"))

    def test_quantity_creates_one_row_per_unit(self):
        product = self._make_product(self.merchant_a, "500000")

        orders = self._create([{"marketplace_product_id": product.id, "quantity": 3}])

        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].items.count(), 3)
        self.assertEqual(orders[0].base_price, Decimal("1500000"))

    def test_duplicate_lines_are_merged(self):
        product = self._make_product(self.merchant_a, "500000")

        orders = self._create(
            [
                {"marketplace_product_id": product.id, "quantity": 1},
                {"marketplace_product_id": product.id, "quantity": 2},
            ]
        )

        self.assertEqual(orders[0].items.count(), 3)

    # ------------------------------------------------------------------
    # Ichki katalogdagi aks (internal_product)
    # ------------------------------------------------------------------

    def test_mirror_product_is_created_lazily(self):
        """Aks oldindan emas, birinchi buyurtmada yaratiladi."""
        product = self._make_product(self.merchant_a, "1000000", name="Muzlatgich X")
        self.assertIsNone(product.internal_product_id)

        self._create([{"marketplace_product_id": product.id, "quantity": 1}])

        product.refresh_from_db()
        mirror = product.internal_product
        self.assertIsNotNone(mirror)
        self.assertEqual(mirror.company_id, self.merchant_a.id)
        self.assertEqual(mirror.category_id, self.category.id)
        self.assertEqual(mirror.external_id, f"mp:{product.id}")

    def test_mirror_product_is_reused_across_orders(self):
        """Takroriy buyurtmalar dublikat `Product` yaratmaydi."""
        product = self._make_product(self.merchant_a, "1000000", name="Muzlatgich X")

        self._create([{"marketplace_product_id": product.id, "quantity": 1}])
        product.refresh_from_db()
        first_mirror_id = product.internal_product_id

        self._create([{"marketplace_product_id": product.id, "quantity": 2}])
        product.refresh_from_db()

        self.assertEqual(product.internal_product_id, first_mirror_id)
        self.assertEqual(
            Product.objects.filter(external_id=f"mp:{product.id}").count(), 1
        )

    def test_mirror_fields_are_taken_from_the_card(self):
        """Aksning barcha maydonlari marketplace kartochkasidan olinadi."""
        brand = Brand.objects.create(name="Artel")
        product = self._make_product(
            self.merchant_a,
            "1000000",
            name="Muzlatgich X",
            brand=brand,
            ikpu="98765432109876543",
        )
        product.name_ru = "Холодильник X"
        product.name_en = "Refrigerator X"
        product.save()

        self._create([{"marketplace_product_id": product.id, "quantity": 1}])

        product.refresh_from_db()
        mirror = product.internal_product
        self.assertEqual(mirror.name, "Muzlatgich X")
        self.assertEqual(mirror.name_uz, "Muzlatgich X")
        self.assertEqual(mirror.name_ru, "Холодильник X")
        self.assertEqual(mirror.name_en, "Refrigerator X")
        self.assertEqual(mirror.category_id, self.category.id)
        self.assertEqual(mirror.brand_id, brand.id)
        self.assertEqual(mirror.company_id, self.merchant_a.id)
        self.assertEqual(mirror.ikpu, "98765432109876543")
        self.assertEqual(mirror.external_id, f"mp:{product.id}")

    def test_mirror_is_resynced_when_the_card_changes(self):
        """Kartochka o'zgarsa aks ham yangilanadi.

        Aks eskirib qolsa narxlash kartochkaning yangi kategoriyasidan,
        qatorning `line_category_id` si esa aksning eski kategoriyasidan
        borib, ikkisi bir-biriga zid bo'lib qolardi.
        """
        product = self._make_product(self.merchant_a, "1000000", name="Muzlatgich X")
        self._create([{"marketplace_product_id": product.id, "quantity": 1}])

        product.refresh_from_db()
        mirror_id = product.internal_product_id

        new_category = Category.objects.create(
            name="Konditsioner", parent=self.parent_category
        )
        product.name = "Muzlatgich XL"
        product.name_ru = "Холодильник XL"
        product.category = new_category
        product.ikpu = "11111111111111111"
        product.save()

        orders = self._create([{"marketplace_product_id": product.id, "quantity": 1}])

        product.refresh_from_db()
        mirror = product.internal_product
        # Yangi aks yaratilmaydi — mavjudi yangilanadi
        self.assertEqual(mirror.id, mirror_id)
        self.assertEqual(mirror.name, "Muzlatgich XL")
        self.assertEqual(mirror.name_ru, "Холодильник XL")
        self.assertEqual(mirror.category_id, new_category.id)
        self.assertEqual(mirror.ikpu, "11111111111111111")

        # Narxlash va qator kategoriyasi bir xil manbadan boradi
        item = orders[0].items.first()
        self.assertEqual(item.line_category_id, new_category.id)

    def test_prices_trace_back_to_the_source_card(self):
        """Qator narxi qator -> aks -> kartochka zanjiri bo'yicha tiklanadi.

        `OrderProduct.base_price` unga bog'langan `Product` ning manba
        kartochkasidagi narx bilan bir xil bo'lishi kerak.
        """
        product = self._make_product(self.merchant_a, "1500000", name="Muzlatgich X")

        orders = self._create([{"marketplace_product_id": product.id, "quantity": 2}])
        order = orders[0]

        self.assertEqual(order.items.count(), 2)
        for item in order.items.all():
            # Zanjir: qator -> ichki aks -> manba kartochkasi
            source_card = item.product.marketplace_source
            self.assertIsNotNone(source_card)
            self.assertEqual(source_card.id, product.id)
            self.assertEqual(item.base_price, source_card.price)

            # Duration.percent = 20%
            self.assertEqual(item.markup_amount, Decimal("300000"))
            self.assertEqual(item.price, item.base_price + item.markup_amount)
            self.assertEqual(item.prepayment, Decimal("0"))

        self.assertEqual(order.base_price, Decimal("3000000"))
        self.assertEqual(order.markup_amount, Decimal("600000"))
        self.assertEqual(order.price, Decimal("3600000"))

    def test_rejected_cart_leaves_no_trace_in_catalog(self):
        """Savat tekshiruvdan o'tmasa hech qanday aks yaratilmaydi.

        Aks narx hisoblashda emas, yozish tranzaksiyasida yaratiladi —
        shuning uchun rad etilgan savatdagi yaroqli mahsulot ham katalogda
        iz qoldirmaydi.
        """
        valid = self._make_product(self.merchant_a, "1000000", name="Muzlatgich X")
        inactive = self._make_product(
            self.merchant_a, "500000", name="Sotuvda emas", status=StatusChoices.DRAFT
        )
        before = Product.objects.count()

        with self.assertRaises(BadRequestException):
            self._create_raw(
                [
                    {"marketplace_product_id": valid.id, "quantity": 1},
                    {"marketplace_product_id": inactive.id, "quantity": 1},
                ]
            )

        valid.refresh_from_db()
        self.assertIsNone(valid.internal_product_id)
        self.assertEqual(Product.objects.count(), before)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def test_snapshot_fields_are_filled(self):
        product = self._make_product(self.merchant_a, "1000000", name="Muzlatgich X")

        orders = self._create([{"marketplace_product_id": product.id, "quantity": 1}])
        item = orders[0].items.first()

        product.refresh_from_db()
        self.assertIsNotNone(product.internal_product_id)
        self.assertEqual(item.product_id, product.internal_product_id)
        # Faqat soliq maydonlari qatorda muzlatiladi
        self.assertEqual(item.ikpu, "12345678901234567")
        self.assertEqual(item.sku, product.sku)
        # Accessorlar snapshotdan o'qiydi
        self.assertEqual(item.display_name, "Muzlatgich X")
        self.assertEqual(item.line_category_id, self.category.id)
        self.assertTrue(item.is_marketplace)

    def test_translated_names_come_from_the_mirror(self):
        """Nomlar qatorda saqlanmaydi — ichki aksdan o'qiladi.

        Aks birinchi buyurtmada yaratiladi va nomi o'shanda muzlaydi,
        shuning uchun kartochka keyin qayta nomlansa buyurtma o'zgarmaydi.
        """
        product = self._make_product(self.merchant_a, "1000000", name="Muzlatgich")
        product.name_ru = "Холодильник"
        product.name_en = "Refrigerator"
        product.save()

        orders = self._create([{"marketplace_product_id": product.id, "quantity": 1}])
        item = orders[0].items.first()

        self.assertEqual(item.display_name_uz, "Muzlatgich")
        self.assertEqual(item.display_name_ru, "Холодильник")
        self.assertEqual(item.display_name_en, "Refrigerator")

        product.name_ru = "Другое"
        product.save()
        item.refresh_from_db()
        self.assertEqual(item.display_name_ru, "Холодильник")
        self.assertEqual(item.display_name_en, "Refrigerator")

    def test_snapshot_survives_catalog_rename(self):
        product = self._make_product(self.merchant_a, "1000000", name="Eski nom")
        orders = self._create([{"marketplace_product_id": product.id, "quantity": 1}])

        product.name = "Yangi nom"
        product.save()

        item = OrderProduct.objects.get(order=orders[0])
        self.assertEqual(item.display_name, "Eski nom")

    # ------------------------------------------------------------------
    # Qo'shimcha xizmatlar va olib ketish manzili
    # ------------------------------------------------------------------

    def test_prepayment_is_always_zero(self):
        """Bu oqimda oldindan to'lov yo'q — buyurtma to'liq bo'lib to'lashga."""
        product = self._make_product(self.merchant_a, "1000000")

        orders = self._create([{"marketplace_product_id": product.id, "quantity": 1}])

        self.assertEqual(orders[0].prepayment, Decimal("0"))
        for item in orders[0].items.all():
            self.assertEqual(item.prepayment, Decimal("0"))

    def test_additional_services_are_added_to_every_order(self):
        """Har merchant o'z jo'natmasini yuboradi — xizmat har orderga alohida."""
        service = AdditionalServicePrice.objects.create(
            service_type=AdditionalServiceTypeChoices.DELIVERY_BTS,
            price=Decimal("50000"),
        )
        p1 = self._make_product(self.merchant_a, "1000000", name="A1")
        p2 = self._make_product(self.merchant_b, "1000000", name="B1")

        orders = self._create(
            [
                {"marketplace_product_id": p1.id, "quantity": 1},
                {"marketplace_product_id": p2.id, "quantity": 1},
            ],
            additional_services=[service],
        )

        self.assertEqual(len(orders), 2)
        for order in orders:
            self.assertEqual(order.additional_services.count(), 1)
            # 1 000 000 + 20% ustama + 50 000 xizmat
            self.assertEqual(order.price, Decimal("1250000"))
            # Xizmat narxi asl narxga va ustamaga qo'shilmaydi
            self.assertEqual(order.base_price, Decimal("1000000"))
            self.assertEqual(order.markup_amount, Decimal("200000"))

    def test_without_additional_services_price_has_no_extras(self):
        product = self._make_product(self.merchant_a, "1000000")

        orders = self._create([{"marketplace_product_id": product.id, "quantity": 1}])

        self.assertEqual(orders[0].additional_services.count(), 0)
        self.assertEqual(orders[0].price, Decimal("1200000"))

    def test_pick_up_address_is_saved_on_every_order(self):
        p1 = self._make_product(self.merchant_a, "1000000", name="A1")
        p2 = self._make_product(self.merchant_b, "1000000", name="B1")

        orders = self._create(
            [
                {"marketplace_product_id": p1.id, "quantity": 1},
                {"marketplace_product_id": p2.id, "quantity": 1},
            ],
            pick_up_address="Toshkent, Chilonzor 5",
        )

        for order in orders:
            self.assertEqual(order.pick_up_address, "Toshkent, Chilonzor 5")

    def test_no_delivery_address_is_created(self):
        """Yetkazib berish bu oqimda yo'q."""
        from apps.delivery.models import DeliveryAddress

        product = self._make_product(self.merchant_a, "1000000")
        orders = self._create([{"marketplace_product_id": product.id, "quantity": 1}])

        self.assertFalse(
            DeliveryAddress.objects.filter(order__in=orders).exists()
        )

    # ------------------------------------------------------------------
    # Limit — savat darajasida; yetmasa ariza (DraftOrder)
    # ------------------------------------------------------------------

    def test_insufficient_limit_creates_drafts_not_orders(self):
        """Limit yetmasa buyurtma emas, har merchant uchun ariza yaratiladi."""
        # 12 oylik limit = get_limit * 12 * 1
        self._set_limit(Decimal("100000"))  # => 1 200 000 umumiy limit

        p1 = self._make_product(self.merchant_a, "1000000", name="A1")
        p2 = self._make_product(self.merchant_b, "1000000", name="B1")

        result = self._create_raw(
            [
                {"marketplace_product_id": p1.id, "quantity": 1},
                {"marketplace_product_id": p2.id, "quantity": 1},
            ]
        )

        self.assertEqual(result["step"], "application")
        self.assertEqual(result["orders"], [])
        self.assertEqual(len(result["draft_orders"]), 2)

        # Haqiqiy buyurtma va limit bandi yaratilmaydi
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderProduct.objects.count(), 0)
        self.assertEqual(LimitHistory.objects.count(), 0)

        # Har merchant o'z arizasini ko'radi
        self.assertEqual(
            set(DraftOrder.objects.values_list("company_id", flat=True)),
            {self.merchant_a.id, self.merchant_b.id},
        )

    def test_draft_order_keeps_product_snapshot(self):
        self._set_limit(Decimal("1"))
        product = self._make_product(self.merchant_a, "1000000", name="Muzlatgich X")

        result = self._create_raw(
            [{"marketplace_product_id": product.id, "quantity": 2}]
        )

        self.assertEqual(result["step"], "application")
        draft = result["draft_orders"][0]
        self.assertEqual(draft.base_price, Decimal("2000000"))

        items = list(draft.items.all())
        self.assertEqual(len(items), 2)
        product.refresh_from_db()
        self.assertIsNotNone(product.internal_product_id)
        for item in items:
            self.assertEqual(item.product_id, product.internal_product_id)
            self.assertEqual(item.ikpu, "12345678901234567")
            self.assertEqual(item.display_name, "Muzlatgich X")
            self.assertTrue(item.is_marketplace)

    def test_limit_is_checked_on_cart_total_not_per_order(self):
        """Alohida-alohida sig'adigan, lekin birgalikda sig'maydigan savat."""
        self._set_limit(Decimal("150000"))  # 12 oy => 1 800 000

        # Har biri ustama bilan ~1 200 000, ikkitasi ~2 400 000 > 1 800 000
        p1 = self._make_product(self.merchant_a, "1000000", name="A1")
        p2 = self._make_product(self.merchant_b, "1000000", name="B1")

        # Bittasi alohida sig'adi
        orders = self._create([{"marketplace_product_id": p1.id, "quantity": 1}])
        self.assertEqual(len(orders), 1)
        Order.objects.all().delete()
        LimitHistory.objects.all().delete()
        self._set_limit(Decimal("150000"))

        # Ikkalasi birga sig'maydi -> ariza
        result = self._create_raw(
            [
                {"marketplace_product_id": p1.id, "quantity": 1},
                {"marketplace_product_id": p2.id, "quantity": 1},
            ]
        )
        self.assertEqual(result["step"], "application")
        self.assertEqual(Order.objects.count(), 0)

    def test_limit_history_created_per_order(self):
        p1 = self._make_product(self.merchant_a, "1000000", name="A1")
        p2 = self._make_product(self.merchant_b, "1000000", name="B1")

        orders = self._create(
            [
                {"marketplace_product_id": p1.id, "quantity": 1},
                {"marketplace_product_id": p2.id, "quantity": 1},
            ]
        )

        self.assertEqual(LimitHistory.objects.count(), 2)
        for order in orders:
            self.assertTrue(LimitHistory.objects.filter(order=order).exists())

    # ------------------------------------------------------------------
    # Mahsulot tekshiruvlari
    # ------------------------------------------------------------------

    def test_inactive_product_is_rejected(self):
        product = self._make_product(
            self.merchant_a, "1000000", status=StatusChoices.DRAFT
        )

        with self.assertRaises(BadRequestException):
            self._create([{"marketplace_product_id": product.id, "quantity": 1}])
        self.assertEqual(Order.objects.count(), 0)

    def test_product_without_category_is_rejected(self):
        product = self._make_product(self.merchant_a, "1000000")
        MarketplaceProduct.objects.filter(id=product.id).update(category=None)

        with self.assertRaises(BadRequestException):
            self._create([{"marketplace_product_id": product.id, "quantity": 1}])
        self.assertEqual(Order.objects.count(), 0)

    def test_product_without_price_is_rejected(self):
        product = self._make_product(self.merchant_a, "1000000")
        MarketplaceProduct.objects.filter(id=product.id).update(price=None)

        with self.assertRaises(BadRequestException):
            self._create([{"marketplace_product_id": product.id, "quantity": 1}])
        self.assertEqual(Order.objects.count(), 0)

    def test_unknown_product_is_rejected(self):
        with self.assertRaises(Exception):
            self._create([{"marketplace_product_id": 999999, "quantity": 1}])
        self.assertEqual(Order.objects.count(), 0)

    def test_client_price_is_ignored(self):
        """Mijoz yuborgan narx e'tiborga olinmaydi — narx serverdan olinadi."""
        product = self._make_product(self.merchant_a, "1000000")

        orders = self._create(
            [{"marketplace_product_id": product.id, "quantity": 1, "price": "1"}]
        )
        self.assertEqual(orders[0].base_price, Decimal("1000000"))

    def test_pending_changes_price_is_ignored(self):
        """Moderatsiyada turgan narx emas, tasdiqlangan narx ishlatiladi."""
        product = self._make_product(self.merchant_a, "1000000")
        product.pending_changes = {"price": "1"}
        product.save()

        orders = self._create([{"marketplace_product_id": product.id, "quantity": 1}])
        self.assertEqual(orders[0].base_price, Decimal("1000000"))

    # ------------------------------------------------------------------
    # IMEI (GSM) cheklovi — savat darajasida
    # ------------------------------------------------------------------

    def test_gsm_limit_applies_across_merchants(self):
        """Merchantlar bo'yicha bo'linish cheklovni chetlab o'tishga imkon bermasligi kerak."""
        p1 = self._make_product(
            self.merchant_a, "1000000", category=self.gsm_category, name="Tel A"
        )
        p2 = self._make_product(
            self.merchant_b, "1000000", category=self.gsm_category, name="Tel B"
        )

        with self.assertRaises(BadRequestException):
            self._create(
                [
                    {"marketplace_product_id": p1.id, "quantity": 2},
                    {"marketplace_product_id": p2.id, "quantity": 1},
                ]
            )
        self.assertEqual(Order.objects.count(), 0)

    def test_gsm_limit_allows_two(self):
        p1 = self._make_product(
            self.merchant_a, "1000000", category=self.gsm_category, name="Tel A"
        )

        orders = self._create([{"marketplace_product_id": p1.id, "quantity": 2}])
        self.assertEqual(orders[0].items.count(), 2)

    # ------------------------------------------------------------------
    # Narxlash — mahsulot kartochkasi bilan bir xil bo'lishi kerak
    # ------------------------------------------------------------------

    def test_markup_matches_installment_options(self):
        """Order ustamasi kartochkadagi `installment_options` bilan mos kelishi kerak."""
        Markup.objects.create(
            type=MarkupTypeChoices.CATEGORY,
            category=self.category,
            duration=self.duration,
            markup_percentage=Decimal("15.00"),
        )
        product = self._make_product(self.merchant_a, "1000000")

        # Kartochka ko'rsatadigan foiz
        card_context = PricingService().build_context(
            durations=[self.duration],
            category_ids={product.category_id},
            company_ids={product.merchant_id},
            client_id=self.client_obj.id,
        )
        card_percent = card_context.resolve_percent(
            duration_id=self.duration.id,
            category_id=product.category_id,
            company_id=product.merchant_id,
        )
        self.assertEqual(card_percent, Decimal("15.00"))

        orders = self._create([{"marketplace_product_id": product.id, "quantity": 1}])
        order = orders[0]

        expected_markup = Decimal("1000000") * card_percent / 100
        self.assertEqual(order.markup_amount, expected_markup)
        self.assertEqual(order.price, Decimal("1000000") + expected_markup)

    def test_company_markup_wins_when_lower(self):
        Markup.objects.create(
            type=MarkupTypeChoices.CATEGORY,
            category=self.category,
            duration=self.duration,
            markup_percentage=Decimal("30.00"),
        )
        Markup.objects.create(
            type=MarkupTypeChoices.COMPANY,
            company=self.merchant_a,
            duration=self.duration,
            markup_percentage=Decimal("10.00"),
        )
        product = self._make_product(self.merchant_a, "1000000")

        orders = self._create([{"marketplace_product_id": product.id, "quantity": 1}])
        self.assertEqual(orders[0].markup_amount, Decimal("100000"))

    def test_markup_applies_to_full_base_price(self):
        """Oldindan to'lov yo'q — ustama to'liq asl narxga qo'llanadi."""
        Markup.objects.create(
            type=MarkupTypeChoices.CATEGORY,
            category=self.category,
            duration=self.duration,
            markup_percentage=Decimal("10.00"),
        )
        product = self._make_product(self.merchant_a, "1000000")

        orders = self._create([{"marketplace_product_id": product.id, "quantity": 1}])

        self.assertEqual(orders[0].markup_amount, Decimal("100000"))
        self.assertEqual(orders[0].price, Decimal("1100000"))


class ProductSnapshotAccessorTestCase(TestCase):
    """Eski (v1) qatorlar accessorlar orqali o'zgarishsiz o'qilishi kerak."""

    def setUp(self):
        self.company = Company.objects.create(name="Gamma", tin="333333333")
        self.duration = Duration.objects.create(name="6 oy", months=6)
        self.parent_category = Category.objects.create(name="Texnika")
        self.category = Category.objects.create(
            name="Noutbuk",
            parent=self.parent_category,
            marking_type=ProductMarkingChoices.IMEI,
        )
        self.client_obj = Client.objects.create(
            full_name="Eski Mijoz", phone="998900000002"
        )
        self.product = Product.objects.create(
            name="Noutbuk Z",
            category=self.category,
            company=self.company,
            ikpu="99999999999999999",
        )
        self.order = Order.objects.create(
            number="GAM000001",
            order_counter=1,
            client=self.client_obj,
            company=self.company,
            duration=self.duration,
        )

    def test_accessors_fall_back_to_product(self):
        """Snapshot bo'sh bo'lsa accessorlar `product` dan o'qiydi."""
        item = OrderProduct.objects.create(
            order=self.order,
            product=self.product,
            base_price=Decimal("100"),
            markup_amount=Decimal("0"),
            price=Decimal("100"),
        )

        self.assertFalse(item.is_marketplace)
        self.assertEqual(item.display_name, "Noutbuk Z")
        self.assertEqual(item.display_name_uz, "Noutbuk Z")
        self.assertEqual(item.display_name_ru, "Noutbuk Z")
        self.assertEqual(item.line_category_id, self.category.id)
        self.assertEqual(item.line_category, self.category)
        self.assertEqual(item.line_ikpu, "99999999999999999")
        self.assertEqual(item.product, self.product)

    def test_mirror_in_manual_order_is_not_a_marketplace_line(self):
        """Aksni qo'lda buyurtmada tanlash mumkin, lekin qator marketplace emas.

        `is_marketplace` buyurtma kanalidan aniqlanadi: mahsulot marketplace
        kartochkasining aksi bo'lsa ham, offline buyurtmadagi qator ichki
        qator bo'lib qoladi.
        """
        merchant = Company.objects.create(name="Delta", tin="444444444")
        mirror = Product.objects.create(
            name="MP",
            category=self.category,
            company=merchant,
        )
        MarketplaceProduct.objects.create(
            name="MP",
            merchant=merchant,
            category=self.category,
            price=Decimal("10"),
            internal_product=mirror,
        )

        item = OrderProduct.objects.create(
            order=self.order,
            product=mirror,
            base_price=Decimal("100"),
            markup_amount=Decimal("0"),
            price=Decimal("100"),
        )

        self.assertEqual(self.order.source, OrderSourceChoices.OFFLINE)
        self.assertFalse(item.is_marketplace)
        # Manba kartochkasiga yo'l baribir ochiq
        self.assertEqual(item.product.marketplace_source.name, "MP")

    def test_hard_delete_of_referenced_product_is_blocked(self):
        """PROTECT buyurtma yozuvini «egasiz» qoldirishga yo'l qo'ymaydi.

        Modellar soft delete ishlatadi, lekin admindagi `hard_delete_selected`
        haqiqiy DELETE yuboradi — o'sha yerda PROTECT ishlaydi.
        """
        OrderProduct.objects.create(
            order=self.order,
            product=self.product,
            base_price=Decimal("100"),
            markup_amount=Decimal("0"),
            price=Decimal("100"),
        )

        with self.assertRaises(ProtectedError):
            self.product.hard_delete()

    def test_nulling_product_would_break_the_constraint(self):
        """`SET_NULL` bo'lganda CheckConstraint buzilar edi — PROTECT shuning uchun."""
        item = OrderProduct.objects.create(
            order=self.order,
            product=self.product,
            base_price=Decimal("100"),
            markup_amount=Decimal("0"),
            price=Decimal("100"),
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrderProduct.objects.filter(id=item.id).update(product=None)

    def test_cannot_reference_neither_catalog(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrderProduct.objects.create(
                    order=self.order,
                    base_price=Decimal("100"),
                    markup_amount=Decimal("0"),
                    price=Decimal("100"),
                )


class LegacyOrderRegressionTestCase(LimitStubMixin, TestCase):
    """Mavjud (v1) buyurtma oqimi o'zgarishsiz ishlashi kerak."""

    _limit_patcher = None

    def setUp(self):
        self.order_service = OrderService()
        self.company = Company.objects.create(name="Omega", tin="555555555")
        self.duration = Duration.objects.create(
            name="12 oy", months=12, percent=Decimal("20.00")
        )
        self.parent_category = Category.objects.create(name="Texnika")
        self.category = Category.objects.create(
            name="Kir yuvish", parent=self.parent_category
        )
        self.client_obj = Client.objects.create(
            full_name="Eski oqim", phone="998900000003"
        )
        self.application = Application.objects.create(client=self.client_obj)
        self.stub_limit(Decimal("10000000"))

        self.product = Product.objects.create(
            name="Kir yuvish mashinasi",
            category=self.category,
            company=self.company,
            ikpu="11111111111111111",
        )

    def test_create_order_with_existing_product(self):
        order = self.order_service.create_order(
            client_id=self.client_obj.id,
            duration_id=self.duration.id,
            company_id=self.company.id,
            items=[
                {
                    "product_id": self.product.id,
                    "base_price": Decimal("2000000"),
                    "quantity": 1,
                }
            ],
        )

        self.assertEqual(order.status, OrderStatusChoices.DRAFT)
        self.assertEqual(order.company_id, self.company.id)
        self.assertEqual(order.base_price, Decimal("2000000"))
        # Duration.percent = 20%
        self.assertEqual(order.markup_amount, Decimal("400000"))
        self.assertEqual(order.price, Decimal("2400000"))

        item = order.items.first()
        self.assertEqual(item.product_id, self.product.id)
        self.assertEqual(item.display_name, "Kir yuvish mashinasi")
        self.assertEqual(item.line_category_id, self.category.id)
        self.assertEqual(item.line_ikpu, "11111111111111111")

    def test_manual_order_applies_category_markup(self):
        """Qo'lda buyurtmada ham `Markup` amal qiladi.

        Ilgari `calculate_price` markupni `company_id` va `category_id` ni
        birga so'rar edi. `markup_type_target_consistent` cheklovi bunday
        qatorga yo'l qo'ymaydi, shuning uchun so'rov hech qachon mos
        kelmasdi va ustama har doim `Duration.percent` dan olinardi.
        """
        Markup.objects.create(
            type=MarkupTypeChoices.CATEGORY,
            category=self.category,
            duration=self.duration,
            markup_percentage=Decimal("5.00"),
        )

        order = self.order_service.create_order(
            client_id=self.client_obj.id,
            duration_id=self.duration.id,
            company_id=self.company.id,
            items=[
                {
                    "product_id": self.product.id,
                    "base_price": Decimal("2000000"),
                    "quantity": 1,
                }
            ],
        )

        # Duration.percent = 20% emas, kategoriya markupi = 5%
        self.assertEqual(order.markup_amount, Decimal("100000"))
        self.assertEqual(order.price, Decimal("2100000"))

    def test_manual_order_takes_lowest_of_available_markups(self):
        """Kompaniya stavkasi pastroq bo'lsa o'sha qo'llanadi."""
        Markup.objects.create(
            type=MarkupTypeChoices.CATEGORY,
            category=self.category,
            duration=self.duration,
            markup_percentage=Decimal("30.00"),
        )
        Markup.objects.create(
            type=MarkupTypeChoices.COMPANY,
            company=self.company,
            duration=self.duration,
            markup_percentage=Decimal("10.00"),
        )

        order = self.order_service.create_order(
            client_id=self.client_obj.id,
            duration_id=self.duration.id,
            company_id=self.company.id,
            items=[
                {
                    "product_id": self.product.id,
                    "base_price": Decimal("1000000"),
                    "quantity": 1,
                }
            ],
        )

        self.assertEqual(order.markup_amount, Decimal("100000"))

    def test_create_order_creating_new_product(self):
        """`product_id` siz item yangi `Product` yaratadi (eski xatti-harakat)."""
        order = self.order_service.create_order(
            client_id=self.client_obj.id,
            duration_id=self.duration.id,
            company_id=self.company.id,
            items=[
                {
                    "name": "Yangi tovar",
                    "category_id": self.category.id,
                    "_category": self.category,
                    "base_price": Decimal("1000000"),
                    "quantity": 1,
                }
            ],
        )

        item = order.items.first()
        self.assertIsNotNone(item.product_id)
        self.assertEqual(item.display_name, "Yangi tovar")
        self.assertTrue(
            Product.objects.filter(name="Yangi tovar", company=self.company).exists()
        )

    def test_identifiers_status_uses_category_from_product(self):
        gsm_category = Category.objects.create(
            name="Telefonlar",
            parent=self.parent_category,
            marking_type=ProductMarkingChoices.IMEI,
        )
        gsm_product = Product.objects.create(
            name="Telefon", category=gsm_category, company=self.company
        )
        order = self.order_service.create_order(
            client_id=self.client_obj.id,
            duration_id=self.duration.id,
            company_id=self.company.id,
            items=[
                {
                    "product_id": gsm_product.id,
                    "base_price": Decimal("1000000"),
                    "quantity": 1,
                }
            ],
        )

        result = self.order_service.get_identifiers_status(order)
        self.assertFalse(result["is_complete"])
        self.assertEqual(result["items"][0]["product_name"], "Telefon")
        self.assertEqual(result["items"][0]["required_marking_type"], "imei")


class OrderItemSerializerTestCase(TestCase):
    """`OrderItemSerializer` ikkala katalog uchun ham to'liq javob berishi kerak."""

    def setUp(self):
        self.company = Company.objects.create(name="Sigma", tin="666666666")
        self.duration = Duration.objects.create(name="6 oy", months=6)
        self.parent_category = Category.objects.create(name="Texnika")
        self.category = Category.objects.create(name="TV", parent=self.parent_category)
        self.client_obj = Client.objects.create(
            full_name="Serializer", phone="998900000004"
        )
        self.order = Order.objects.create(
            number="SIG000001",
            order_counter=1,
            client=self.client_obj,
            company=self.company,
            duration=self.duration,
        )

    def _serialize(self, item):
        from api.order.serializers.order import OrderItemSerializer

        return OrderItemSerializer(item).data

    def test_internal_line(self):
        product = Product.objects.create(
            name="Televizor",
            category=self.category,
            company=self.company,
            ikpu="22222222222222222",
        )
        item = OrderProduct.objects.create(
            order=self.order,
            product=product,
            base_price=Decimal("100"),
            markup_amount=Decimal("0"),
            price=Decimal("100"),
        )

        data = self._serialize(item)
        self.assertEqual(data["source"], "internal")
        self.assertEqual(data["name"], "Televizor")
        self.assertEqual(data["ikpu"], "22222222222222222")
        self.assertEqual(data["category"]["id"], self.category.id)
        self.assertIsNotNone(data["product"])
        self.assertIsNone(data["marketplace_product"])

    def test_marketplace_line(self):
        """Marketplace buyurtmasidagi qator manba kartochkasini ham qaytaradi."""
        self.order.source = OrderSourceChoices.MARKETPLACE
        self.order.save(update_fields=["source"])

        merchant = Company.objects.create(name="Merchant", tin="777777777")
        mirror = Product.objects.create(
            name="Marketplace TV",
            category=self.category,
            company=merchant,
            ikpu="33333333333333333",
        )
        mp = MarketplaceProduct.objects.create(
            name="Marketplace TV",
            merchant=merchant,
            category=self.category,
            price=Decimal("100"),
            ikpu="33333333333333333",
            internal_product=mirror,
        )
        item = OrderProduct.objects.create(
            order=self.order,
            product=mirror,
            ikpu="33333333333333333",
            base_price=Decimal("100"),
            markup_amount=Decimal("0"),
            price=Decimal("100"),
        )

        data = self._serialize(item)
        self.assertEqual(data["source"], "marketplace")
        self.assertEqual(data["name"], "Marketplace TV")
        self.assertEqual(data["ikpu"], "33333333333333333")
        self.assertEqual(data["category"]["id"], self.category.id)
        self.assertEqual(data["product"]["id"], mirror.id)
        self.assertEqual(data["marketplace_product"]["id"], mp.id)


class MobileOrderMerchantTestCase(TestCase):
    """Mobil order list va detail javoblarida merchant nomi va logosi."""

    def setUp(self):
        self.duration = Duration.objects.create(name="12 oy", months=12)
        self.merchant = Company.objects.create(
            name="Alfa Savdo",
            tin="123123123",
            logo=SimpleUploadedFile(
                "logo.png", b"\x89PNG\r\n\x1a\n", content_type="image/png"
            ),
        )
        self.client_obj = Client.objects.create(
            full_name="Mobil Mijoz", phone="998900000009"
        )
        self.order = Order.objects.create(
            number="ALF000001",
            order_counter=1,
            client=self.client_obj,
            company=self.merchant,
            duration=self.duration,
            price=Decimal("1200000"),
            status=OrderStatusChoices.ACTIVE,
        )

        from services.client import ClientService

        tokens = ClientService().generate_client_tokens(self.client_obj)
        self.auth = f"Client {tokens['access_token']}"

    def test_list_includes_merchant_without_changing_other_fields(self):
        response = self.client.get(
            "/api/mobile/order/my-orders/", HTTP_AUTHORIZATION=self.auth
        )

        self.assertEqual(response.status_code, 200, response.content)
        row = response.json()["data"]["omonat"][0]

        # Mavjud maydonlar joyida qoladi
        for field in ("id", "order_name", "status", "created_at", "pick_up_address"):
            self.assertIn(field, row)
        self.assertEqual(row["order_name"], "ALF000001")

        self.assertEqual(row["merchant"]["id"], self.merchant.id)
        self.assertEqual(row["merchant"]["name"], "Alfa Savdo")
        self.assertTrue(row["merchant"]["logo"])

    def test_detail_includes_merchant_without_changing_other_fields(self):
        response = self.client.get(
            f"/api/mobile/order/detail/{self.order.number}/",
            HTTP_AUTHORIZATION=self.auth,
        )

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]

        # Mavjud kalitlar o'zgarmagan
        for field in (
            "order_name",
            "status",
            "status_desc",
            "source",
            "products",
            "total",
            "tarif",
            "monthly",
        ):
            self.assertIn(field, data)

        self.assertEqual(data["merchant"]["id"], self.merchant.id)
        self.assertEqual(data["merchant"]["name"], "Alfa Savdo")
        self.assertTrue(data["merchant"]["logo"])

    def test_merchant_logo_is_null_when_not_set(self):
        self.merchant.logo = None
        self.merchant.save()

        response = self.client.get(
            "/api/mobile/order/my-orders/", HTTP_AUTHORIZATION=self.auth
        )
        row = response.json()["data"]["omonat"][0]

        self.assertEqual(row["merchant"]["name"], "Alfa Savdo")
        self.assertIsNone(row["merchant"]["logo"])

    def test_list_and_detail_use_the_same_merchant_shape(self):
        list_row = self.client.get(
            "/api/mobile/order/my-orders/", HTTP_AUTHORIZATION=self.auth
        ).json()["data"]["omonat"][0]
        detail = self.client.get(
            f"/api/mobile/order/detail/{self.order.number}/",
            HTTP_AUTHORIZATION=self.auth,
        ).json()["data"]

        self.assertEqual(list_row["merchant"], detail["merchant"])


class MarketplaceOrderApiTestCase(LimitStubMixin, TestCase):
    """`POST /api/mobile/order/v2/create/` uchidan-uchiga."""

    _limit_patcher = None
    url = "/api/mobile/order/v2/create/"

    def setUp(self):
        self.duration = Duration.objects.create(
            name="12 oy", months=12, percent=Decimal("20.00")
        )
        self.parent_category = Category.objects.create(name="Texnika")
        self.category = Category.objects.create(
            name="Konditsioner", parent=self.parent_category
        )
        self.merchant_a = Company.objects.create(name="ApiAlfa", tin="888888888")
        self.merchant_b = Company.objects.create(name="ApiBeta", tin="999999999")
        self.client_obj = Client.objects.create(
            full_name="Api Mijoz", phone="998900000005"
        )
        Application.objects.create(client=self.client_obj)
        self.stub_limit(Decimal("10000000"))

        from services.client import ClientService

        tokens = ClientService().generate_client_tokens(self.client_obj)
        self.auth = f"Client {tokens['access_token']}"

    def _product(self, merchant, price, name):
        return MarketplaceProduct.objects.create(
            name=name,
            merchant=merchant,
            category=self.category,
            price=Decimal(str(price)),
            status=StatusChoices.ACTIVE,
        )

    def test_create_returns_one_order_per_merchant(self):
        p1 = self._product(self.merchant_a, "1000000", "A1")
        p2 = self._product(self.merchant_b, "2000000", "B1")

        response = self.client.post(
            self.url,
            data={
                "duration": self.duration.months,
                "items": [
                    {"marketplace_product_id": p1.id, "quantity": 1},
                    {"marketplace_product_id": p2.id, "quantity": 1},
                ],
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth,
        )

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()["data"]
        self.assertEqual(payload["step"], "success")
        self.assertEqual(payload["orders_count"], 2)
        self.assertEqual(len(payload["orders"]), 2)

        merchants = {row["merchant"]["name"] for row in payload["orders"]}
        self.assertEqual(merchants, {"ApiAlfa", "ApiBeta"})

        first = payload["orders"][0]
        self.assertIn("order_name", first)
        self.assertIn("items", first)
        self.assertEqual(first["status"], OrderStatusChoices.DRAFT)

    def test_create_with_services_and_pickup_address(self):
        service = AdditionalServicePrice.objects.create(
            service_type=AdditionalServiceTypeChoices.DELIVERY_BTS,
            price=Decimal("50000"),
        )
        p1 = self._product(self.merchant_a, "1000000", "A1")

        response = self.client.post(
            self.url,
            data={
                "duration": self.duration.months,
                "items": [{"marketplace_product_id": p1.id, "quantity": 1}],
                "additional_services": [service.id],
                "pick_up_address": "Toshkent, Chilonzor 5",
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth,
        )

        self.assertEqual(response.status_code, 201, response.content)
        order_data = response.json()["data"]["orders"][0]
        self.assertEqual(order_data["pick_up_address"], "Toshkent, Chilonzor 5")
        # 1 000 000 + 20% + 50 000
        self.assertEqual(order_data["price"], "1250000.00")

        order = Order.objects.get(id=order_data["id"])
        self.assertEqual(order.additional_services.count(), 1)
        self.assertEqual(order.prepayment, Decimal("0"))

    def test_prepayment_and_delivery_are_not_accepted(self):
        """Olib tashlangan maydonlar yuborilsa jimgina e'tiborsiz qoldiriladi."""
        p1 = self._product(self.merchant_a, "1000000", "A1")

        response = self.client.post(
            self.url,
            data={
                "duration": self.duration.months,
                "items": [{"marketplace_product_id": p1.id, "quantity": 1}],
                "prepayment": "500000.00",
                "delivery": {"region_id": 1},
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth,
        )

        self.assertEqual(response.status_code, 201, response.content)
        order = Order.objects.get(id=response.json()["data"]["orders"][0]["id"])
        self.assertEqual(order.prepayment, Decimal("0"))
        self.assertEqual(order.price, Decimal("1200000"))
        self.assertFalse(hasattr(order, "delivery"))

    def test_insufficient_limit_returns_application_step(self):
        self.stub_limit(Decimal("1"))
        p1 = self._product(self.merchant_a, "1000000", "A1")
        p2 = self._product(self.merchant_b, "2000000", "B1")

        response = self.client.post(
            self.url,
            data={
                "duration": self.duration.months,
                "items": [
                    {"marketplace_product_id": p1.id, "quantity": 1},
                    {"marketplace_product_id": p2.id, "quantity": 1},
                ],
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth,
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()["data"]
        self.assertEqual(payload["step"], "application")
        self.assertEqual(payload["draft_orders_count"], 2)
        self.assertEqual(len(payload["draft_orders"]), 2)
        self.assertIn("merchant", payload["draft_orders"][0])
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(DraftOrder.objects.count(), 2)

    def test_create_requires_authentication(self):
        response = self.client.post(
            self.url,
            data={"duration": self.duration.months, "items": []},
            content_type="application/json",
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(Order.objects.count(), 0)

    def test_empty_items_rejected(self):
        response = self.client.post(
            self.url,
            data={"duration": self.duration.months, "items": []},
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_custom_duration_is_not_selected_by_months(self):
        """Bir xil oyli maxsus muddat bo'lsa ham kartochkadagi muddat tanlanadi.

        Kartochka faqat `is_custom=False` muddatlarni ko'rsatadi; maxsus
        muddat tanlansa foiz boshqa bo'lib, narx mos kelmay qolardi.
        """
        custom = Duration.objects.create(
            name="12 oy (maxsus)",
            months=12,
            is_custom=True,
            percent=Decimal("99.00"),
        )
        self.assertLess(self.duration.id, custom.id)

        p1 = self._product(self.merchant_a, "1000000", "A1")
        response = self.client.post(
            self.url,
            data={
                "duration": 12,
                "items": [{"marketplace_product_id": p1.id, "quantity": 1}],
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth,
        )

        self.assertEqual(response.status_code, 201, response.content)
        order = Order.objects.get(id=response.json()["data"]["orders"][0]["id"])
        self.assertEqual(order.duration_id, self.duration.id)
        # 99% emas, 20% qo'llanishi kerak
        self.assertEqual(order.markup_amount, Decimal("200000"))

    def test_unknown_duration_rejected(self):
        p1 = self._product(self.merchant_a, "1000000", "A1")
        response = self.client.post(
            self.url,
            data={
                "duration": 999,
                "items": [{"marketplace_product_id": p1.id, "quantity": 1}],
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)
