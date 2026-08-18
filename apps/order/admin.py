from django.contrib import admin, messages

from apps.order.models import Order, Payment
from services.order import InstallmentError, build_schedule


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("number", "due_date", "amount", "paid_at", "is_overdue")
    readonly_fields = ("is_overdue",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "client", "merchant", "amount", "months", "total", "paid", "remainder", "status")
    list_filter = ("status", "merchant")
    search_fields = ("client__full_name", "client__phone")
    autocomplete_fields = ("client", "merchant")
    inlines = [PaymentInline]
    actions = ["make_schedule"]

    @admin.action(description="Построить график рассрочки")
    def make_schedule(self, request, queryset):
        for order in queryset:
            try:
                build_schedule(order)
            except InstallmentError as exc:
                self.message_user(request, f"Заказ #{order.pk}: {exc}", messages.WARNING)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("order", "number", "due_date", "amount", "paid_at", "is_overdue")
    list_filter = ("due_date", "paid_at")
    actions = ["mark_paid"]

    @admin.action(description="Отметить оплаченными")
    def mark_paid(self, request, queryset):
        for payment in queryset:
            payment.pay()
