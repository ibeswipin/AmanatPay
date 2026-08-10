from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from api.delivery.serializers.delivery import DeliveryAddressDetailSerializer, OrderDeliveryAddressCreateSerializer
from api.merchandise.serializers.brand import BrandMiniSerializer
from api.merchandise.serializers.category import CategoryMiniSerializer
from api.merchandise.serializers.duration import DurationMiniSerializer
from api.merchandise.serializers.product import ProductMiniSerializer
from api.user.serializers.clients import ClientMiniSerializer, ClientWithLimitSerializer
from api.user.serializers.users import RoleMiniSerializer
from apps.marketplace.models import MarketplaceProduct
from apps.merchandise.choices import ProductMarkingChoices
from apps.merchandise.models import Brand, Category
from apps.order.choices import PaymentFactStatusChoices
from apps.order.models import (
    AdditionalServicePrice,
    Order,
    OrderAdditionalService,
    OrderProduct,
    OrderReceiptImage,
    OrderStatusLog,
    TemporaryOrderData,
)
from apps.utils.exceptions import CustomException
from apps.utils.validators import first_payment_validator, ikpu_validator, imei_validator, marking_validator


class OrderClientSerializer(serializers.ModelSerializer):
    status_desc = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "number",
            "duration",
            "price",
            "prepayment",
            "base_price",
            "markup_amount",
            "status",
            "status_desc",
            "created_at",
        ]


class TemporaryOrderDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = TemporaryOrderData
        fields = (
            "created_at",
            "data",
        )


class MarketplaceProductMiniSerializer(serializers.ModelSerializer):
    """`ProductMiniSerializer` bilan bir xil shakl — marketplace katalogi uchun."""

    category = CategoryMiniSerializer(read_only=True)
    brand = BrandMiniSerializer(read_only=True)

    class Meta:
        model = MarketplaceProduct
        fields = ("id", "name", "category", "brand")


class OrderItemSerializer(serializers.ModelSerializer):
    """OrderProduct serializer.

    Qator har doim ichki `product` ga bog'lanadi. Marketplace kartochkasidan
    kelgan qatorlarda `product` — kartochkaning ichki katalogdagi aksi, va
    `marketplace_product` javob shakli saqlanib qolishi uchun o'sha aks
    orqali manba kartochkasini qaytaradi.
    """

    product = ProductMiniSerializer(read_only=True)
    marketplace_product = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()
    name = serializers.CharField(source="display_name", read_only=True)
    category = serializers.SerializerMethodField()
    ikpu = serializers.CharField(source="line_ikpu", read_only=True)

    class Meta:
        model = OrderProduct
        fields = [
            "id",
            "source",
            "name",
            "category",
            "product",
            "marketplace_product",
            "base_price",
            "prepayment",
            "markup_amount",
            "price",
            "ikpu",
            "imei",
            "marking",
        ]

    def get_source(self, obj) -> str:
        return "marketplace" if obj.is_marketplace else "internal"

    def get_marketplace_product(self, obj) -> dict | None:
        source_card = obj.product.marketplace_source if obj.product_id else None
        if not source_card:
            return None
        return MarketplaceProductMiniSerializer(source_card).data

    def get_category(self, obj):
        category = obj.line_category
        if not category:
            return None
        return CategoryMiniSerializer(category).data


class OrderStatusLogSerializer(serializers.ModelSerializer):
    """Serializer for OrderStatusLog"""

    created = serializers.SerializerMethodField()
    status_desc = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = OrderStatusLog
        fields = [
            "id",
            "status",
            "status_desc",
            "created",
            "comment",
            "created_at",
        ]

    def get_created(self, obj):
        if not obj.created_by:
            return _("Mijoz")
        user = getattr(obj.created_by, "user", None)
        if not user:
            return f"Role #{obj.created_by_id}"

        full_name = user.get_full_name()
        if full_name:
            return full_name

        if getattr(user, "username", None):
            return user.username

        return f"Role #{obj.created_by_id}"


class PaymentFactTransactionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    uuid = serializers.CharField()
    source = serializers.CharField()
    source_display = serializers.CharField()
    amount = serializers.IntegerField()
    date = serializers.DateTimeField()


class PaymentFactRowSerializer(serializers.Serializer):
    date = serializers.DateField()
    planned = serializers.IntegerField()
    actual = serializers.IntegerField()
    overdue = serializers.IntegerField()
    overdue_days = serializers.IntegerField(default=None, allow_null=True)
    status = serializers.ChoiceField(choices=PaymentFactStatusChoices.choices)
    status_desc = serializers.SerializerMethodField()
    transactions = PaymentFactTransactionSerializer(many=True)

    def get_status_desc(self, obj):
        status = obj.get("status")
        try:
            return PaymentFactStatusChoices(status).label
        except ValueError:
            return status or ""


class OrderListSerializer(serializers.ModelSerializer):
    """Serializer for order list view with nested mini serializers"""

    client = ClientMiniSerializer(read_only=True)
    company = serializers.CharField(source="company_name", read_only=True)
    branch = serializers.SerializerMethodField()
    duration = DurationMiniSerializer(read_only=True)
    status_desc = serializers.CharField(source="get_status_display", read_only=True)
    source_desc = serializers.CharField(source="get_source_display", read_only=True)

    def get_branch(self, obj):
        branch_name = getattr(obj, "branch_name", None) or getattr(getattr(obj, "branch", None), "name", None)
        return branch_name or "Mobile"

    class Meta:
        model = Order
        fields = [
            "id",
            "number",
            "client",
            "company",
            "branch",
            "duration",
            "source",
            "source_desc",
            "status",
            "status_desc",
            "base_price",
            "prepayment",
            "price",
            # "total_paid", # bu order listda nima kerak??? queryni O(N) qivorayaptiku.
            "pick_up_address",
            "created_at",
        ]


class OrderAdditionalServiceSerializer(serializers.ModelSerializer):
    """Serializer for OrderAdditionalService"""
    service_type = serializers.CharField(source="service.service_type", read_only=True)
    service_type_desc = serializers.CharField(source="service.get_service_type_display", read_only=True)

    class Meta:
        model = OrderAdditionalService
        fields = [
            # "id",
            "service",
            "service_type",
            "service_type_desc",
            "price",
        ]


class OrderReceiptImageSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    type_desc = serializers.CharField(source="get_type_display", read_only=True)

    class Meta:
        model = OrderReceiptImage
        fields = [
            "id",
            "image",
            "type",
            "type_desc",
        ]

    def get_image(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url


class OrderDetailSerializer(serializers.ModelSerializer):
    """Serializer for order detail view with nested mini serializers"""

    client = ClientWithLimitSerializer(read_only=True)
    company = serializers.CharField(source="company_name", read_only=True)
    branch = serializers.CharField(source="branch_name", read_only=True)
    duration = DurationMiniSerializer(read_only=True)
    created_by = RoleMiniSerializer(read_only=True)
    status_desc = serializers.CharField(source="get_status_display", read_only=True)
    source_desc = serializers.CharField(source="get_source_display", read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    delivery = DeliveryAddressDetailSerializer(read_only=True)  # chopiladi front ulab bo'lgandna keyin
    additional_services = OrderAdditionalServiceSerializer(many=True, read_only=True)
    receipt_images = OrderReceiptImageSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "number",
            "client",
            "company",
            "branch",
            "duration",
            "source",
            "source_desc",
            "first_payment",
            "status",
            "status_desc",
            "items",
            "additional_services",
            "base_price",
            "prepayment",
            "markup_amount",
            "price",
            "total_paid",
            "delivery",
            "receipt_images",
            "pick_up_address",
            "created_by",
            "created_at",
        ]


class OrderIdentifierItemSerializer(serializers.Serializer):
    """Serializer for single identifier item"""

    id = serializers.IntegerField(read_only=True)
    product_name = serializers.CharField(read_only=True)
    category_marking_type = serializers.CharField(read_only=True)
    requires_identifier = serializers.BooleanField(read_only=True)
    imei = serializers.CharField(read_only=True, allow_null=True)
    marking = serializers.CharField(read_only=True, allow_null=True)
    is_complete = serializers.BooleanField(read_only=True)


class OrderIdentifiersSerializer(serializers.Serializer):
    """Serializer for identifiers status response"""

    is_complete = serializers.BooleanField(read_only=True)
    can_confirm = serializers.BooleanField(read_only=True)
    total_items = serializers.IntegerField(read_only=True)
    items_complete = serializers.IntegerField(read_only=True)
    items_incomplete = serializers.IntegerField(read_only=True)
    items = OrderIdentifierItemSerializer(many=True, read_only=True)


class UpdateIdentifiersSerializer2(serializers.Serializer):
    id = serializers.IntegerField()

    imei = serializers.CharField(
        required=False,
        validators=[imei_validator],
    )

    marking = serializers.CharField(
        required=False,
        validators=[marking_validator],
    )


class UpdateIdentifiersSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=True)

    imei = serializers.CharField(
        required=False,
        validators=[imei_validator],
        allow_null=True,
        allow_blank=True,
    )

    marking = serializers.CharField(
        required=False,
        validators=[marking_validator],
        allow_null=True,
        allow_blank=True,
    )


class OrderUpdateIdentifiersSerializer(serializers.Serializer):
    """Serializer for updating identifiers"""

    items = UpdateIdentifiersSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("Kamida bitta item kerak")
        return value


class OrderCreateItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=False, allow_null=True)

    name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    category_id = serializers.IntegerField(required=False, allow_null=True)
    brand_id = serializers.IntegerField(required=False, allow_null=True)

    quantity = serializers.IntegerField(default=1, min_value=1)
    base_price = serializers.DecimalField(max_digits=20, decimal_places=2)
    ikpu = serializers.CharField(
        max_length=17, required=False, allow_null=True, allow_blank=True,
        validators=[ikpu_validator]
    )

    marking = serializers.CharField(
        max_length=150, required=False, allow_null=True, allow_blank=True,
        validators=[marking_validator]
    )

    def validate(self, attrs):
        product_id = attrs.get("product_id")
        name = attrs.get("name")
        category_id = attrs.get("category_id")
        brand_id = attrs.get("brand_id")

        if product_id is not None and product_id <= 0:
            attrs["product_id"] = None
            product_id = None

        if not product_id:
            if not name or not category_id:
                raise CustomException(detail="product_id yoki (name + category_id) kerak")

        if brand_id and not Brand.objects.filter(id=brand_id).exists():
            raise CustomException(detail=f"Brand topilmadi (id={brand_id})")

        return attrs


class OrderCreateSerializer(serializers.Serializer):
    client_id = serializers.IntegerField(required=True)
    company_id = serializers.IntegerField(required=False, allow_null=True)
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    duration_id = serializers.IntegerField(required=True)
    prepayment = serializers.DecimalField(
        max_digits=20, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    first_payment = serializers.DateField(
        required=False, allow_null=True, validators=[first_payment_validator]
    )
    items = OrderCreateItemSerializer(many=True)
    additional_services = serializers.PrimaryKeyRelatedField(
        queryset=AdditionalServicePrice.objects.all(),
        many=True,
        required=False,
        allow_null=True
    )
    delivery = OrderDeliveryAddressCreateSerializer(required=False, allow_null=True)
    pick_up_address = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("Kamida bitta mahsulot kerak")

        category_ids = set()
        for item in value:
            if not item.get("product_id") and item.get("category_id"):
                category_ids.add(item["category_id"])

        if category_ids:
            categories = Category.objects.prefetch_related("children").filter(
                id__in=category_ids
            )
            categories_cache = {cat.id: cat for cat in categories}

            for item in value:
                category_id = item.get("category_id")
                if not item.get("product_id") and category_id:
                    category = categories_cache.get(category_id)
                    if not category:
                        raise CustomException(detail=f"Kategoriya topilmadi (id={category_id})")
                    if category.children.exists():
                        raise CustomException(detail=f"Faqat oxirgi kategoriyani tanlash mumkin (id={category_id})")
                    if (
                            category.marking_type == ProductMarkingChoices.MARKING
                            and not item.get("marking")
                    ):
                        raise CustomException(detail=f"Bu kategoriya uchun Marking majburiy (id={category_id})")
                    item["_category"] = category

        return value


class OrderUpdateItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=False, allow_null=True)

    name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    category_id = serializers.IntegerField(required=False, allow_null=True)
    brand_id = serializers.IntegerField(required=False, allow_null=True)

    quantity = serializers.IntegerField(default=1, min_value=1)
    base_price = serializers.DecimalField(max_digits=20, decimal_places=2, required=False, allow_null=True)
    ikpu = serializers.CharField(
        max_length=17, required=False, allow_null=True, allow_blank=True
    )

    marking = serializers.CharField(
        max_length=150, required=False, allow_null=True, allow_blank=True,
        validators=[marking_validator]
    )

    def validate(self, attrs):
        product_id = attrs.get("product_id")
        name = attrs.get("name")
        category_id = attrs.get("category_id")
        brand_id = attrs.get("brand_id")

        if product_id is not None and product_id <= 0:
            attrs["product_id"] = None
            product_id = None

        if not product_id:
            if not name or not category_id:
                raise CustomException(detail="product_id yoki (name + category_id) kerak")

        if brand_id and not Brand.objects.filter(id=brand_id).exists():
            raise CustomException(detail=f"Brand topilmadi (id={brand_id})")

        return attrs


class OrderUpdateSerializer(serializers.Serializer):
    """Serializer for updating order"""

    items = OrderUpdateItemSerializer(many=True, required=False)
    prepayment = serializers.DecimalField(
        max_digits=20, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    additional_services = serializers.PrimaryKeyRelatedField(
        queryset=AdditionalServicePrice.objects.all(),
        many=True,
        required=False,
        allow_null=True
    )
    duration_id = serializers.IntegerField(required=False, allow_null=True)
    first_payment = serializers.DateField(
        required=False, allow_null=True, validators=[first_payment_validator]
    )
    pick_up_address = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )

    def validate_items(self, value):
        if value is not None and not value:
            raise serializers.ValidationError("Kamida bitta mahsulot kerak")

        category_ids = set()
        for item in value:
            if not item.get("product_id") and item.get("category_id"):
                category_ids.add(item["category_id"])

        if category_ids:
            categories = Category.objects.prefetch_related("children").filter(
                id__in=category_ids
            )
            categories_cache = {cat.id: cat for cat in categories}

            for item in value:
                category_id = item.get("category_id")
                if not item.get("product_id") and category_id:
                    category = categories_cache.get(category_id)
                    if not category:
                        raise serializers.ValidationError(
                            f"Kategoriya topilmadi (id={category_id})"
                        )
                    if category.children.exists():
                        raise serializers.ValidationError(
                            f"Faqat oxirgi kategoriyani tanlash mumkin (id={category_id})"
                        )
                    if (
                            category.marking_type == ProductMarkingChoices.MARKING
                            and not item.get("marking")
                    ):
                        raise serializers.ValidationError(
                            f"Bu kategoriya uchun Marking majburiy (id={category_id})"
                        )
                    item["_category"] = category

        return value


class OrderConfirmReceiptSerializer(serializers.Serializer):
    """
    Serializer for confirming order receipt and uploading images.
    """
    images = serializers.ListField(
        child=serializers.ImageField(),
        required=True,
        allow_empty=False,
        error_messages={
            "empty": "Kamida bitta rasm yuklash majburiy.",
            "required": "Rasmlar ro'yxati kiritilmadi."
        }
    )


class OrderConfirmPickupOTPSerializer(serializers.Serializer):
    """
    Serializer for confirming pickup with OTP.
    """
    id = serializers.IntegerField()
    otp = serializers.CharField(max_length=6)


class OrderCompleteSerializer(serializers.Serializer):
    """
    Serializer for completing order with images.
    Client tovarni olganini rasmlar bilan tasdiqlaydi.
    """
    images = serializers.ListField(
        child=serializers.ImageField(),
        required=True,
        allow_empty=False,
        error_messages={
            "empty": "Kamida bitta rasm yuklash majburiy.",
            "required": "Rasmlar ro'yxati kiritilmadi."
        }
    )


class AdditionalServicePriceSerializer(serializers.ModelSerializer):
    service_type_desc = serializers.CharField(source="get_service_type_display", read_only=True)

    class Meta:
        model = AdditionalServicePrice
        fields = [
            "id",
            "service_type",
            "service_type_desc",
            "price",
        ]


class OrderQabzSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = [
            "id",
            "number",
            "status",
        ]
