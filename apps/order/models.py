from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.company.models import Branch, Company
from apps.merchandise.models import Duration, Product
from apps.order.choices import (
    AdditionalServiceTypeChoices,
    ExportStatusChoices,
    ExportTypeChoices,
    MerchantPaymentStatusChoices,
    MerchantPaymentTypeChoices,
    OrderCreationTypeChoices,
    OrderSourceChoices,
    OrderStatusChoices,
    PartnerPayment,
    RadiusOrderStatusChoices,
    RadiusOrderTypeChoices,
    ReceiptImageTypeChoices,
)
from apps.user.models import Client, Role
from apps.utils.models import BaseModel
from apps.utils.validators import imei_validator, marking_validator
from apps.wallet.choices import Status, TransactionType


class Order(BaseModel):
    """Buyurtma modeli"""

    number = models.CharField(
        max_length=9,
        unique=True,
        editable=False,
        verbose_name=_("Buyurtma raqami"),
        help_text=_("Avtomatik generatsiya qilinadi (masalan: RAD000001)"),
    )
    order_counter = models.PositiveIntegerField(
        verbose_name=_("Buyurtma tartib raqami"),
        help_text=_("Bu kompaniya uchun nechinchi buyurtma"),
    )

    # Relations
    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name=_("Mijoz"),
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name=_("Kompaniya"),
    )
    duration = models.ForeignKey(
        Duration,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name=_("Muddati"),
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        verbose_name=_("Filial"),
    )
    created_by = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_orders",
        verbose_name=_("Yaratuvchi"),
    )

    # Narxlar (OrderProduct'lardan hisoblanadi)
    base_price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name=_("Asl narx"),
        help_text=_("Jami asl narx (barcha mahsulotlar)"),
    )
    markup_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name=_("Ustama"),
        help_text=_("Jami ustama summasi"),
    )
    price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name=_("Yakuniy narx"),
        help_text=_("Jami yakuniy narx (asl narx + ustama)"),
    )
    prepayment = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name=_("Oldindan to'lov"),
        help_text=_("Jami oldindan to'lov summasi"),
    )

    # Status
    status = models.CharField(
        max_length=30,
        choices=OrderStatusChoices,
        default=OrderStatusChoices.DRAFT,
        verbose_name=_("Holat"),
    )
    first_payment = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("Birinchi to'lov sanasi"),
    )
    pick_up_address = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_("Qabul qilish uchun joylashuv"),
    )
    source = models.CharField(
        max_length=20,
        choices=OrderSourceChoices,
        default=OrderSourceChoices.OFFLINE,
        verbose_name=_("Buyurtma manbasi"),
    )
    creation_type = models.CharField(
        max_length=20,
        choices=OrderCreationTypeChoices,
        default=OrderCreationTypeChoices.MANUALLY,
        db_index=True,
        verbose_name=_("Yaratilish usuli"),
        help_text=_("Buyurtmani merchant qo'lda kiritdimi yoki mijoz ilova orqali yaratdimi"),
    )

    class Meta:
        verbose_name = _("Buyurtma")
        verbose_name_plural = _("Buyurtmalar")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.number}"

    @property
    def total_paid(self) -> Decimal:
        """Jami to'langan summa"""
        return self.transactions.filter(
            type=TransactionType.PAYMENT,
            status=Status.SUCCESS,
        ).aggregate(models.Sum("amount"))[
            "amount__sum"
        ] or Decimal("0")

    @property
    def overdue(self) -> dict:
        """
        Kechikkan kunlar va summani hisoblash.
        Agar kechikish bo'lmasa, keyingi to'lovgacha necha kun qolgani (manfiy sonda) qaytariladi.
        """
        now = timezone.now()
        total_paid = self.total_paid
        cumulative_planned = Decimal("0")
        overdue_days = 0
        overdue_amount = Decimal("0")

        for schedule in self.payment_schedules.all().order_by("due_date"):
            cumulative_planned += schedule.planned_amount
            if total_paid < cumulative_planned:
                if schedule.due_date < now:
                    if overdue_days == 0:
                        overdue_days = (now - schedule.due_date).days

                    overdue_amount = cumulative_planned - total_paid
                else:
                    if overdue_days == 0:
                        overdue_days = -(schedule.due_date - now).days
                    break

        return {
            "days": overdue_days,
            "amount": overdue_amount if overdue_amount > 0 else Decimal("0"),
        }


class ProductSnapshotMixin:
    """Buyurtma qatoridagi mahsulot ma'lumotini o'qish interfeysi.

    Qator har doim ichki `Product` ga bog'lanadi — marketplace kartochkasidan
    berilgan buyurtmalarda ham, chunki kartochkaning ichki katalogdagi aksi
    (`MarketplaceProduct.internal_product`) birinchi buyurtmada yaratiladi.

    Nom va kategoriya katalogdan o'qiladi: mahsulot o'zgarsa buyurtma ham
    o'zgaradi. Faqat `ikpu` va `sku` sotuv paytidagi holatni saqlaydi —
    ular soliq hujjatlariga tushadi, shuning uchun qatorda muzlatiladi.
    """

    @property
    def _catalog(self):
        return self.product if self.product_id else None

    @property
    def display_name(self) -> str:
        product = self._catalog
        return (product.name or "") if product else ""

    @property
    def display_name_uz(self) -> str:
        product = self._catalog
        return (product.name_uz or product.name or "") if product else ""

    @property
    def display_name_ru(self) -> str:
        product = self._catalog
        return (product.name_ru or product.name or "") if product else ""

    @property
    def display_name_en(self) -> str:
        product = self._catalog
        return (product.name_en or product.name or "") if product else ""

    @property
    def line_category_id(self):
        """Ustama va markirovka qoidalari uchun kategoriya (qo'shimcha query yo'q)."""
        return self.product.category_id if self.product_id else None

    @property
    def line_category(self):
        return self.product.category if self.product_id else None

    @property
    def line_ikpu(self):
        if self.ikpu:
            return self.ikpu
        return self.product.ikpu if self.product_id else None


class OrderProduct(ProductSnapshotMixin, BaseModel):
    """Buyurtma mahsuloti modeli"""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("Buyurtma"),
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name=_("Mahsulot"),
    )

    # Soliq hujjatlariga tushadigan maydonlar sotuv paytida muzlatiladi
    ikpu = models.CharField(
        max_length=17,
        null=True,
        blank=True,
        verbose_name=_("IKPU"),
    )
    sku = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name=_("SKU"),
    )

    imei = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        verbose_name=_("IMEI"),
        validators=[imei_validator],
        help_text=_("Qurilma IMEI raqami (15 raqam)"),
    )
    marking = models.CharField(
        max_length=38,
        blank=True,
        null=True,
        verbose_name=_("Markirovka"),
        validators=[marking_validator],
        help_text=_("Mahsulot markirovkasi"),
    )

    base_price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        verbose_name=_("Asl narx"),
    )
    prepayment = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name=_("Oldindan to'lov"),
    )
    markup_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        verbose_name=_("Ustama"),
    )
    price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        verbose_name=_("Yakuniy narx"),
    )

    class Meta:
        verbose_name = _("Buyurtma mahsuloti")
        verbose_name_plural = _("Buyurtma mahsulotlari")
        constraints = [
            models.UniqueConstraint(
                fields=["imei"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_active_product_imei",
            ),
        ]

    @property
    def is_marketplace(self) -> bool:
        """Qator marketplace savatidan kelganmi.

        Buyurtma kanalidan aniqlanadi, mahsulotdan emas: marketplace aksini
        merchant qo'lda yaratilgan buyurtmada ham tanlashi mumkin, u holda
        qator marketplace qatori hisoblanmaydi.
        """
        return self.order.source == OrderSourceChoices.MARKETPLACE

    def __str__(self):
        return f"order_id={self.order_id} - product_id={self.product_id}"


class PaymentSchedule(BaseModel):
    """To'lov jadvali modeli"""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="payment_schedules",
        verbose_name=_("Buyurtma"),
    )

    period_number = models.PositiveIntegerField(
        verbose_name=_("Davr raqami"),
        help_text=_("1, 2, 3... n-chi to'lov"),
    )
    due_date = models.DateTimeField(
        verbose_name=_("To'lov muddati"),
    )
    planned_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        verbose_name=_("Rejalashtirilgan summa"),
    )

    class Meta:
        verbose_name = _("To'lov jadvali")
        verbose_name_plural = _("To'lov jadvallari")
        ordering = ["period_number"]
        unique_together = ["order", "period_number"]

    def __str__(self):
        return f"order_id={self.order_id} - {self.period_number}-davr: {self.planned_amount}"


class MerchantPayment(BaseModel):
    """Merchant to'lovlar"""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="merchant_payments",
        verbose_name=_("Merchant"),
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="merchant_payments",
        verbose_name=_("Buyurtma"),
    )
    product = models.ForeignKey(
        OrderProduct,
        on_delete=models.CASCADE,
        related_name="merchant_payments",
        verbose_name=_("Mahsulot"),
    )
    amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        verbose_name=_("Summa"),
    )
    paid_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("To'lov qilindi"),
    )
    type = models.CharField(
        max_length=50,
        choices=MerchantPaymentTypeChoices,
        default=MerchantPaymentTypeChoices.PAYMENT,
        verbose_name=_("To'lov turi"),
    )
    status = models.CharField(
        max_length=20,
        choices=MerchantPaymentStatusChoices,
        verbose_name=_("Holat"),
    )
    contract_number = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_("Kontrakt raqami"),
    )
    comment = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Izoh"),
    )
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Merchant oldi berdi")
        verbose_name_plural = _("Merchant oldi berdilar")
        ordering = ["-created_at"]


class MerchantPaymentExport(BaseModel):
    """
    Bu file barcha export file lar uchun ishlatiladigan bo'ldi type orqali ajratib olinadi.
    """
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="merchant_payment_exports",
        verbose_name=_("Merchant"),
    )
    """Merchant to'lovlar export tarixi"""
    created_by = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("Yaratuvchi"),
        related_name="merchant_payment_exports",
    )
    file = models.FileField(
        upload_to="exports/merchant_payments/%Y/%m/%d/",
        verbose_name=_("Fayl"),
        null=True,
        blank=True,
    )
    type = models.CharField(
        max_length=50,
        choices=ExportTypeChoices,
        default=ExportTypeChoices.MERCHANT_PAYMENT,
        verbose_name=_("Export turi"),
    )
    status = models.CharField(
        max_length=20,
        choices=ExportStatusChoices,
        default=ExportStatusChoices.PENDING,
        verbose_name=_("Holat"),
    )
    task_id = models.CharField(
        max_length=255, verbose_name=_("Task ID"), null=True, blank=True
    )
    filters = models.JSONField(
        verbose_name=_("Filtrlar"), default=dict, null=True, blank=True
    )
    finished_at = models.DateTimeField(
        verbose_name=_("Yakunlangan vaqt"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("Excel file")
        verbose_name_plural = _("Excel filelar")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Export #{self.id} - {self.status}"


class OrderStatusLog(BaseModel):
    """Order status o'zgarishlarini saqlash (linked-list uslubida)"""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="status_logs",
        verbose_name=_("Buyurtma"),
    )
    prev = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="next",
        verbose_name=_("Oldingi status log"),
    )
    status = models.CharField(
        max_length=30,
        choices=OrderStatusChoices.choices,
        verbose_name=_("Yangi status"),
    )
    created_by = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,  # NULL = Client tomonidan o'zgartirilgan
        related_name="order_status_logs",
        verbose_name=_("Kim tomonidan"),
        help_text=_("Bo'sh bo'lsa Client tomonidan o'zgartirilgan"),
    )
    comment = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Izoh"),
    )

    class Meta:
        verbose_name = _("Order status log")
        verbose_name_plural = _("Order status loglar")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["order", "-created_at"]),
        ]

    def __str__(self):
        prev_status = self.prev.status if self.prev else "None"
        return f"order_id={self.order_id}: {prev_status} → {self.status}"


class DraftOrder(BaseModel):
    """
    Limit yetmagan holatda yaratiladigan vaqtincha buyurtma (qoralama).
    Bu buyurtma Order modeliga o'xshash, lekin statusi va boshqa tekshiruvlari yo'q.
    Faqat limitni oshirish uchun ariza sifatida xizmat qiladi.
    """

    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="draft_orders",
        verbose_name=_("Mijoz"),
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="draft_orders",
        verbose_name=_("Kompaniya"),
    )
    duration = models.ForeignKey(
        Duration,
        on_delete=models.PROTECT,
        related_name="draft_orders",
        verbose_name=_("Muddati"),
    )
    base_price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name=_("Asl narx"),
    )
    markup_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name=_("Ustama"),
    )
    price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name=_("Yakuniy narx"),
    )

    class Meta:
        verbose_name = _("Qoralama buyurtma")
        verbose_name_plural = _("Qoralama buyurtmalar")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Draft - client_id={self.client_id} - {self.price}"


class DraftOrderProduct(ProductSnapshotMixin, BaseModel):
    """Qoralama buyurtma mahsulotlari"""

    draft_order = models.ForeignKey(
        DraftOrder,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("Qoralama buyurtma"),
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="draft_order_items",
        verbose_name=_("Mahsulot"),
    )

    # Soliq hujjatlariga tushadigan maydonlar sotuv paytida muzlatiladi
    ikpu = models.CharField(
        max_length=17,
        null=True,
        blank=True,
        verbose_name=_("IKPU"),
    )
    sku = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name=_("SKU"),
    )

    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name=_("Soni"),
    )
    base_price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        verbose_name=_("Asl narx"),
    )
    markup_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        verbose_name=_("Ustama"),
    )
    price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        verbose_name=_("Yakuniy narx"),
    )

    class Meta:
        verbose_name = _("Qoralama buyurtma mahsuloti")
        verbose_name_plural = _("Qoralama buyurtma mahsulotlari")

    @property
    def is_marketplace(self) -> bool:
        """Mahsulot marketplace kartochkasining aksimi.

        `DraftOrder` da kanal maydoni yo'q, shuning uchun `OrderProduct` dan
        farqli o'laroq bu yerda mahsulotning o'zidan aniqlanadi.
        """
        return bool(self.product_id) and self.product.marketplace_source is not None

    def __str__(self):
        return f"draft_order_id={self.draft_order_id} - product_id={self.product_id}"


class Contract(BaseModel):
    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="contract",
        verbose_name=_("Buyurtma"),
    )
    file = models.FileField(
        upload_to="contracts/%Y/%m/",
        verbose_name=_("Shartnoma fayli"),
    )

    class Meta:
        verbose_name = _("Shartnoma")
        verbose_name_plural = _("Shartnomalar")

    def __str__(self):
        return f"Shartnoma - order_id={self.order_id}"


class OrderOTP(BaseModel):
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="order_otps",
        verbose_name=_("Mijoz"),
    )
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="otps",
        verbose_name=_("Buyurtma"),
    )
    code = models.CharField(
        max_length=6,
        verbose_name=_("OTP kod"),
    )
    expires_at = models.DateTimeField(
        verbose_name=_("Amal qilish muddati"),
    )
    is_verified = models.BooleanField(
        default=False,
        verbose_name=_("Tasdiqlangan"),
    )
    attempts = models.IntegerField(
        default=0,
        verbose_name=_("Urinishlar soni"),
    )

    class Meta:
        verbose_name = _("Buyurtma OTP")
        verbose_name_plural = _("Buyurtma OTPlar")
        indexes = [
            models.Index(fields=["client", "order", "is_verified"]),
        ]

    def __str__(self):
        return f"client_id={self.client_id} - order_id={self.order_id} ({self.code})"

    @property
    def is_active(self):
        return self.expires_at > timezone.now()


class OrderReceiptImage(BaseModel):
    """
    Qabz/Delivery rasmlari
    """

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="receipt_images",
        verbose_name=_("Buyurtma"),
    )
    image = models.ImageField(
        upload_to="receipts/%Y/%m/%d/",
        null=True,
        blank=True,
        verbose_name=_("Qabz rasmi"),
    )
    type = models.CharField(
        max_length=150,
        choices=ReceiptImageTypeChoices.choices,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = _("Qabz rasmi")
        verbose_name_plural = _("Qabz rasmlari")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Qabz Rasm - order_id={self.order_id}"


class TemporaryOrderData(BaseModel):
    application = models.ForeignKey(
        "installment.Application",
        on_delete=models.CASCADE,
        related_name="temporary_order_data",
        verbose_name=_("Vaqtinchalik zakaz"),
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="temporary_order_data",
        verbose_name=_("Mijoz"),
    )
    data = models.JSONField(
        verbose_name=_("Ma'lumotlar"),
    )

    class Meta:
        verbose_name = _("Cartdagi vaqtincha buyurtma")
        verbose_name_plural = _("Cartdagi vaqtincha buyurtmalar")

    def __str__(self):
        return f"client_id={self.client_id} - application_id={self.application_id}"


class AdditionalServicePrice(BaseModel):
    service_type = models.CharField(
        max_length=50,
        choices=AdditionalServiceTypeChoices.choices,
        unique=True,
        verbose_name=_("Xizmat turi"),
    )
    price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name=_("Narxi"),
    )

    class Meta:
        verbose_name = _("Qo'shimcha xizmat narxi")
        verbose_name_plural = _("Qo'shimcha xizmat narxlari")

    def __str__(self):
        return f"{self.get_service_type_display()} - {self.price}"


class OrderAdditionalService(BaseModel):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="additional_services",
        verbose_name=_("Buyurtma"),
    )
    service = models.ForeignKey(
        AdditionalServicePrice,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="Xizmat turi",
    )
    price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
        verbose_name=_("Narxi"),
    )

    class Meta:
        verbose_name = _("Qo'shimcha xizmat")
        verbose_name_plural = _("Qo'shimcha xizmatlar")


class PaymentTypesForApp(BaseModel):
    name = models.CharField(
        max_length=50,
        verbose_name=_("To'lov turi"),
    )
    deeplink = models.URLField(
        verbose_name=_("Deep link"),
        null=True,
        blank=True,
    )
    icon = models.FileField(
        upload_to="payment_types/%Y/%m/%d/",
        verbose_name=_("Ikonka"),
        null=True,
        blank=True,
    )
    partner_source = models.CharField(
        max_length=50,
        choices=PartnerPayment.choices,
        verbose_name=_("To'lov manbai"),
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Faol"),
    )

    class Meta:
        verbose_name = _("Ilova uchun to'lov turi")
        verbose_name_plural = _("Ilova uchun to'lov turlari")

    def __str__(self):
        return self.name


class RadiusOrder(BaseModel):
    """RadiusCRM dan kelgan buyurtmalar modeli"""

    external_id = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_("Vneshni ID (RadiusCRM Order ID)")
    )
    number = models.CharField(
        max_length=255,
        null=True, blank=True,
        verbose_name=_("Buyurtma raqami (order_name)")
    )
    pinfl = models.CharField(
        max_length=14,
        null=True, blank=True,
        db_index=True,
        verbose_name=_("Mijoz PINFL")
    )
    phone = models.CharField(
        max_length=20,
        null=True, blank=True,
        db_index=True,
        verbose_name=_("Telefon")
    )
    price = models.DecimalField(
        max_digits=20, decimal_places=2,
        null=True, blank=True,
        verbose_name=_("Jami narx")
    )
    status = models.CharField(
        max_length=255,
        choices=RadiusOrderStatusChoices,
        null=True, blank=True,
        verbose_name=_("Status")
    )
    type = models.CharField(
        max_length=255,
        choices=RadiusOrderTypeChoices,
        null=True, blank=True,
        verbose_name=_("Turi")
    )
    month = models.PositiveIntegerField(
        null=True, blank=True,
        verbose_name=_("Bo'lib to'lash oyi")
    )
    is_paid = models.BooleanField(
        default=False,
        verbose_name=_("To'langan")
    )

    class Meta:
        verbose_name = _("RadiusCRM buyurtmasi")
        verbose_name_plural = _("RadiusCRM buyurtmalari")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.number} ({self.external_id})"


class RadiusOrderProduct(BaseModel):
    """RadiusCRM buyurtmasining mahsulotlari"""

    order = models.ForeignKey(
        RadiusOrder,
        on_delete=models.CASCADE,
        related_name="order_products",
        verbose_name=_("Buyurtma")
    )
    name = models.CharField(
        max_length=512,
        verbose_name=_("Mahsulot nomi")
    )
    quantity = models.IntegerField(
        default=1,
        verbose_name=_("Soni")
    )

    class Meta:
        verbose_name = _("RadiusCRM mahsuloti")
        verbose_name_plural = _("RadiusCRM mahsulotlari")

    def __str__(self):
        return f"{self.order.number} - {self.name}"
