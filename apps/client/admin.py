from django.contrib import admin

from apps.client.models import Client


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "limit", "debt", "available_limit")
    search_fields = ("full_name", "phone", "passport")
