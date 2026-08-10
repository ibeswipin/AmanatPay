from rest_framework import serializers

from apps.merchandise.models import Duration, Product
from apps.order.models import AdditionalServicePrice, Order
from apps.user.models import OTP
from apps.utils.functions import merchant_payload


class GetAppClientOrderSerializer(serializers.ModelSerializer):
    order_name = serializers.CharField(source="number")
    merchant = serializers.SerializerMethodField()

    class Meta:
        model = Order

        fields = (
            "id",
            "order_name",
            "status",
            "created_at",
            "pick_up_address",
            "merchant",
        )

    def get_merchant(self, obj):
        return merchant_payload(obj.company, self.context.get("request"))


class CreateOrderProductMobileSerializer(serializers.Serializer):
    product_id = serializers.CharField(help_text="Product.external_id orqali qidirish")
    quantity = serializers.IntegerField(min_value=1)
    price = serializers.DecimalField(max_digits=20, decimal_places=2)
    name = serializers.CharField(required=False)


class CreateOrderMobileSerializer(serializers.Serializer):
    duration = serializers.IntegerField(help_text="Duration.months orqali qidirish")
    order_products = CreateOrderProductMobileSerializer(many=True)
    additional_services = serializers.PrimaryKeyRelatedField(
        queryset=AdditionalServicePrice.objects.all(),
        many=True,
        required=False,
        allow_null=True
    )
    pick_up_address = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )

    def validate_duration(self, value):
        duration = Duration.objects.filter(months=value).first()
        if not duration:
            raise serializers.ValidationError(f"Duration topilmadi (months={value})")
        self._duration = duration
        return value

    def validate_order_products(self, value):
        if not value:
            raise serializers.ValidationError("Kamida bitta mahsulot kerak")
        return value

    def validate(self, attrs):
        products_data = []
        for idx, item in enumerate(attrs["order_products"]):
            product = Product.objects.filter(external_id=item["product_id"]).first()
            if not product:
                product = Product.objects.create(
                    name=item.get("name"),
                    external_id=item["product_id"],
                )

            products_data.append({
                "product": product,
                "quantity": item["quantity"],
                "price": item["price"],
            })

        attrs["_duration"] = self._duration
        attrs["_products_data"] = products_data

        # Pass additional_services objects directly
        attrs["_additional_services_data"] = attrs.get("additional_services", [])
        return attrs


class ReceiptOTPSerializer(serializers.ModelSerializer):
    class Meta:
        model = OTP
        fields = ("code", "expires_at")
