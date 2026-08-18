from django.contrib import admin

from apps.merchant.models import Merchant


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ("name", "inn", "commission_percent", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "inn")
