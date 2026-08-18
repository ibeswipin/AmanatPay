from rest_framework import serializers

from apps.client.models import Client
from apps.merchant.models import Merchant
from apps.order.models import Order, Payment


class PaymentSerializer(serializers.ModelSerializer):
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Payment
        fields = ("uuid", "number", "due_date", "amount", "paid_at", "is_overdue")
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    merchant = serializers.SlugRelatedField(slug_field="uuid", queryset=Merchant.objects.all())
    client = serializers.SlugRelatedField(slug_field="uuid", queryset=Client.objects.all())
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    paid = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remainder = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            "uuid", "merchant", "client", "amount", "months", "markup_percent",
            "status", "total", "paid", "remainder", "payments", "created_at",
        )
        read_only_fields = ("uuid", "status", "created_at")


class ScheduleCreateSerializer(serializers.Serializer):
    first_due = serializers.DateField(required=False)
