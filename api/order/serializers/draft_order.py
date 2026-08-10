from rest_framework import serializers

from api.merchandise.serializers.duration import DurationMiniSerializer
from api.merchandise.serializers.product import ProductMiniSerializer
from api.order.serializers.order import MarketplaceProductMiniSerializer
from api.user.serializers.clients import ClientMiniSerializer, ClientWithLimitSerializer
from apps.order.models import DraftOrder, DraftOrderProduct


class DraftOrderItemSerializer(serializers.ModelSerializer):
    product = ProductMiniSerializer(read_only=True)
    marketplace_product = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()
    name = serializers.CharField(source="display_name", read_only=True)
    ikpu = serializers.CharField(source="line_ikpu", read_only=True)

    class Meta:
        model = DraftOrderProduct
        fields = [
            "id",
            "source",
            "name",
            "product",
            "marketplace_product",
            "quantity",
            "base_price",
            "markup_amount",
            "price",
            "ikpu",
        ]

    def get_source(self, obj) -> str:
        return "marketplace" if obj.is_marketplace else "internal"

    def get_marketplace_product(self, obj) -> dict | None:
        source_card = obj.product.marketplace_source if obj.product_id else None
        if not source_card:
            return None
        return MarketplaceProductMiniSerializer(source_card).data


class DraftOrderListSerializer(serializers.ModelSerializer):
    client = ClientMiniSerializer(read_only=True)
    company = serializers.CharField(source="company.name", read_only=True)
    duration = DurationMiniSerializer(read_only=True)

    class Meta:
        model = DraftOrder
        fields = [
            "id",
            "client",
            "company",
            "duration",
            "base_price",
            "markup_amount",
            "price",
            "created_at",
        ]


class DraftOrderDetailSerializer(serializers.ModelSerializer):
    client = ClientWithLimitSerializer(read_only=True)
    company = serializers.CharField(source="company.name", read_only=True)
    duration = DurationMiniSerializer(read_only=True)
    items = DraftOrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = DraftOrder
        fields = [
            "id",
            "client",
            "company",
            "duration",
            "items",
            "base_price",
            "markup_amount",
            "price",
            "created_at",
            "updated_at",
        ]
