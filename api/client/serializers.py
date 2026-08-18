from rest_framework import serializers

from apps.client.models import Client


class ClientSerializer(serializers.ModelSerializer):
    debt = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    available_limit = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Client
        fields = ("uuid", "full_name", "phone", "passport", "limit", "debt", "available_limit", "created_at")
        read_only_fields = ("uuid", "created_at")
