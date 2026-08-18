from rest_framework import serializers

from apps.merchant.models import Merchant


class MerchantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Merchant
        fields = ("uuid", "name", "inn", "commission_percent", "is_active", "created_at")
        read_only_fields = ("uuid", "created_at")
