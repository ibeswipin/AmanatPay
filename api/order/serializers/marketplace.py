from rest_framework import serializers

from apps.merchandise.models import Duration
from apps.order.models import AdditionalServicePrice, DraftOrder, Order


class MarketplaceOrderItemSerializer(serializers.Serializer):
    """Savatning bitta qatori.

    Narx yuborilmaydi — u serverda `MarketplaceProduct.price` dan olinadi.
    """

    marketplace_product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, default=1)


class MarketplaceOrderCreateSerializer(serializers.Serializer):
    """Marketplace savatidan buyurtma yaratish so'rovi."""

    duration = serializers.IntegerField(
        min_value=1,
        help_text="Oy soni (Duration.months) — mahsulot kartochkasidagi "
        "installment_options[].months",
    )
    items = MarketplaceOrderItemSerializer(many=True, allow_empty=False)
    additional_services = serializers.PrimaryKeyRelatedField(
        queryset=AdditionalServicePrice.objects.all(),
        many=True,
        required=False,
        allow_null=True,
        help_text="Har bir buyurtmaga alohida qo'shiladi",
    )
    pick_up_address = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )

    def validate_duration(self, value):
        # `is_custom=False` — mahsulot kartochkasi aynan shu muddatlarni
        # ko'rsatadi (InstallmentContextMixin). Bir xil oyli maxsus muddat
        # tanlanib qolsa foiz boshqacha bo'lib, narx kartochkadagidan farq
        # qilardi.
        duration = (
            Duration.objects.filter(months=value, is_custom=False)
            .order_by("id")
            .first()
        )
        if not duration:
            raise serializers.ValidationError(f"Muddat topilmadi (months={value})")
        self._duration = duration
        return value

    def validate(self, attrs):
        attrs["_duration"] = self._duration
        return attrs


class MarketplaceOrderItemResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(source="display_name")
    marketplace_product_id = serializers.SerializerMethodField()
    base_price = serializers.DecimalField(max_digits=20, decimal_places=2)
    markup_amount = serializers.DecimalField(max_digits=20, decimal_places=2)
    price = serializers.DecimalField(max_digits=20, decimal_places=2)

    def get_marketplace_product_id(self, obj) -> int | None:
        """Qator bog'langan ichki aksdan manba kartochkasining id sini oladi."""
        source_card = obj.product.marketplace_source if obj.product_id else None
        return source_card.id if source_card else None


class MarketplaceOrderResultSerializer(serializers.ModelSerializer):
    """Yaratilgan orderlardan biri — har merchant uchun bittadan."""

    order_name = serializers.CharField(source="number", read_only=True)
    merchant = serializers.SerializerMethodField()
    status_desc = serializers.CharField(source="get_status_display", read_only=True)
    items = MarketplaceOrderItemResultSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            "id",
            "order_name",
            "merchant",
            "duration",
            "base_price",
            "markup_amount",
            "price",
            "status",
            "status_desc",
            "first_payment",
            "pick_up_address",
            "items",
            "created_at",
        )

    def get_merchant(self, obj):
        return {"id": obj.company_id, "name": obj.company.name}


class MarketplaceDraftOrderResultSerializer(serializers.ModelSerializer):
    """Limit yetmaganda yaratilgan ariza — har merchant uchun bittadan."""

    merchant = serializers.SerializerMethodField()

    class Meta:
        model = DraftOrder
        fields = (
            "id",
            "merchant",
            "duration",
            "base_price",
            "markup_amount",
            "price",
            "created_at",
        )

    def get_merchant(self, obj):
        return {"id": obj.company_id, "name": obj.company.name}
