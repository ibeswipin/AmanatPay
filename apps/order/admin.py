from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.order.models import (
    AdditionalServicePrice,
    Contract,
    DraftOrder,
    DraftOrderProduct,
    MerchantPayment,
    MerchantPaymentExport,
    Order,
    OrderAdditionalService,
    OrderOTP,
    OrderProduct,
    OrderReceiptImage,
    OrderStatusLog,
    PaymentSchedule,
    PaymentTypesForApp,
    RadiusOrder,
    RadiusOrderProduct,
    TemporaryOrderData,
)
from apps.utils.admin import AdminSearchMixin
from apps.wallet.models import Transactions


@admin.register(MerchantPaymentExport)
class MerchantPaymentExportAdmin(AdminSearchMixin):
    list_display = [
        "id",
        "type",
        "created_by",
        "status",
        "created_at",
        "finished_at",
    ]
    list_filter = ["type", "status", "created_at"]
    search_fields = ["task_id"]
    raw_id_fields = ["created_by"]
    readonly_fields = ["created_at", "updated_at", "file", "finished_at", "task_id"]


class OrderProductInline(admin.TabularInline):
    model = OrderProduct
    extra = 0
    raw_id_fields = ["product"]
    readonly_fields = [
        "product",
        "display_name",
        "base_price",
        "markup_amount",
        "price",
    ]
    can_delete = False

    @admin.display(description=_("Mahsulot nomi"))
    def display_name(self, obj):
        return obj.display_name

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("product")
        )


class PaymentScheduleInline(admin.TabularInline):
    model = PaymentSchedule
    extra = 0
    readonly_fields = ["period_number", "due_date", "planned_amount"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class TransactionInline(admin.TabularInline):
    model = Transactions
    extra = 0
    readonly_fields = [
        "uuid",
        "client",
        "source",
        "type",
        "status",
        "amount",
        "created_at",
        "transaction_id",
        "description",
    ]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class RadiusOrderProductInline(admin.TabularInline):
    model = RadiusOrderProduct
    extra = 0
    raw_id_fields = ["order"]
    readonly_fields = ["name", "quantity", "created_at", "updated_at"]


@admin.register(Order)
class OrderAdmin(AdminSearchMixin):
    list_display = [
        "number",
        "client",
        "company",
        "branch",
        "source",
        "creation_type",
        "price",
        "status",
        "created_at",
    ]
    list_filter = ["source", "creation_type", "status", "company", "branch", "created_at"]
    search_fields = ["number", "client__full_name", "company__name"]
    raw_id_fields = ["client", "company", "duration", "branch", "created_by"]
    readonly_fields = [
        "number",
        "order_counter",
        "base_price",
        "markup_amount",
        "price",
        "first_payment",
        "created_at",
        "updated_at",
    ]
    inlines = [OrderProductInline, PaymentScheduleInline, TransactionInline]
    inner_select_related = [
        "client",
        "company",
        "duration",
        "branch",
        "created_by",
        "created_by__user",
    ]

    fieldsets = (
        (_("Asosiy ma'lumotlar"), {"fields": ("number", "order_counter", "status", "source", "creation_type")}),
        (
            _("Bog'lanishlar"),
            {"fields": ("client", "company", "duration", "branch", "created_by")},
        ),
        (_("Narxlar"), {"fields": ("base_price", "markup_amount", "price")}),
        (_("Vaqt"), {"fields": ("first_payment", "created_at", "updated_at")}),
        (_("Yetkazib berish"), {"fields": ("pick_up_address",)}),
    )


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = [
        "order",
        "file",
    ]
    list_filter = ["order__company"]
    search_fields = ["order__number"]
    raw_id_fields = ["order"]


@admin.register(OrderStatusLog)
class OrderStatusLogAdmin(AdminSearchMixin):
    list_display = [
        "order",
        "created_at",
        "status",
        "comment",
    ]
    raw_id_fields = ["order", "prev", "created_by"]
    list_filter = ["order__company", "created_at"]
    search_fields = ["order__number", "comment"]


@admin.register(OrderProduct)
class OrderProductAdmin(AdminSearchMixin):
    list_display = [
        "order",
        "product_display",
        "price",
    ]
    list_filter = ["order__company"]
    search_fields = [
        "order__number",
        "name",
        "product__name",
    ]
    raw_id_fields = ["order", "product"]
    readonly_fields = [
        "order",
        "product",
        "base_price",
        "markup_amount",
        "price",
    ]

    @admin.display(description=_("Mahsulot"))
    def product_display(self, obj):
        return obj.display_name

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("order", "product")
        )


@admin.register(PaymentSchedule)
class PaymentScheduleAdmin(AdminSearchMixin):
    list_display = [
        "order",
        "period_number",
        "due_date",
        "planned_amount",
    ]
    list_filter = ["order__company", "due_date"]
    search_fields = ["order__number"]
    raw_id_fields = ["order"]
    readonly_fields = ["order", "period_number", "due_date", "planned_amount"]


@admin.register(MerchantPayment)
class MerchantPaymentAdmin(AdminSearchMixin):
    list_display = [
        "id",
        "company",
        "order",
        "product",
        "amount",
        "paid_amount",
        "status",
        "contract_number",
        "paid_at",
    ]
    list_filter = ["status", "company", "paid_at"]
    search_fields = ["order__number", "contract_number", "comment"]
    raw_id_fields = ["company", "order", "product"]
    readonly_fields = ["created_at", "updated_at"]


class DraftOrderProductInline(admin.TabularInline):
    model = DraftOrderProduct
    extra = 0
    raw_id_fields = ["product"]
    readonly_fields = [
        "product",
        "quantity",
        "base_price",
        "markup_amount",
        "price",
    ]
    can_delete = True

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("product")
        )


@admin.register(DraftOrder)
class DraftOrderAdmin(AdminSearchMixin):
    list_display = [
        "id",
        "client",
        "company",
        "price",
        "created_at",
    ]
    list_filter = ["company", "created_at"]
    search_fields = ["client__full_name", "client__phone", "company__name"]
    raw_id_fields = ["client", "company", "duration"]
    readonly_fields = [
        "base_price",
        "markup_amount",
        "price",
        "created_at",
        "updated_at",
    ]
    inlines = [DraftOrderProductInline]


@admin.register(DraftOrderProduct)
class DraftOrderProductAdmin(AdminSearchMixin):
    list_display = [
        "draft_order",
        "product_display",
        "quantity",
        "price",
    ]
    list_filter = ["draft_order__company"]
    search_fields = [
        "draft_order__client__full_name",
        "name",
        "product__name",
    ]
    raw_id_fields = ["draft_order", "product"]
    readonly_fields = [
        "draft_order",
        "product",
        "base_price",
        "markup_amount",
        "price",
    ]

    @admin.display(description=_("Mahsulot"))
    def product_display(self, obj):
        return obj.display_name

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("draft_order", "product")
        )


@admin.register(OrderOTP)
class OrderOTPAdmin(AdminSearchMixin):
    list_display = [
        "id",
        "client",
        "order",
        "code",
        "is_verified",
        "attempts",
        "expires_at",
        "created_at",
    ]
    list_filter = ["is_verified", "created_at", "expires_at"]
    search_fields = ["client__phone", "order__number", "code"]
    raw_id_fields = ["client", "order"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(OrderReceiptImage)
class OrderReceiptImageAdmin(AdminSearchMixin):
    list_display = [
        "id",
        "order",
        "type",
        "created_at",
    ]
    list_filter = ["type", "created_at", "order__company"]
    search_fields = ["order__number"]
    raw_id_fields = ["order"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(TemporaryOrderData)
class TemporaryOrderDataAdmin(AdminSearchMixin):
    list_display = [
        "id",
        "client",
    ]
    search_fields = ["client__phone"]
    raw_id_fields = ("client", "application")


@admin.register(AdditionalServicePrice)
class AdditionalServicePriceAdmin(AdminSearchMixin):
    list_display = [
        "service_type",
        "price",
        "created_at",
        "updated_at",
    ]
    list_filter = ["service_type", "created_at"]
    search_fields = ["service_type"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(OrderAdditionalService)
class OrderAdditionalServiceAdmin(AdminSearchMixin):
    list_display = [
        "order",
        "service",
        "price",
    ]
    list_filter = ["order__company"]
    search_fields = ["order__number"]
    raw_id_fields = ["order"]
    readonly_fields = []


@admin.register(PaymentTypesForApp)
class PaymentTypesForAppAdmin(AdminSearchMixin):
    list_display = ["name", "deeplink", "is_active", "created_at", "updated_at"]
    list_filter = ["is_active", "created_at"]
    search_fields = ["name", "deeplink"]


@admin.register(RadiusOrder)
class RadiusOrderAdmin(AdminSearchMixin):
    list_display = [
        "id",
        "number",
        "external_id",
        "pinfl",
        "phone",
        "price",
        "status",
        "type",
        "is_paid",
        "created_at",
    ]
    list_filter = ["status", "type", "is_paid", "created_at"]
    search_fields = ["number", "external_id", "pinfl", "phone"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [RadiusOrderProductInline]


@admin.register(RadiusOrderProduct)
class RadiusOrderProductAdmin(AdminSearchMixin):
    list_display = ["id", "order", "name", "quantity", "created_at"]
    search_fields = ["order__number", "order__external_id", "name"]
    raw_id_fields = ["order"]
    readonly_fields = ["created_at", "updated_at"]
