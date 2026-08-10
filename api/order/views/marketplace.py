from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import GenericViewSet

from api.order.serializers.marketplace import (
    MarketplaceDraftOrderResultSerializer,
    MarketplaceOrderCreateSerializer,
    MarketplaceOrderResultSerializer,
)
from apps.order.models import Order
from apps.user.models import Client
from apps.utils.authentication import ClientTokenAuthentication
from apps.utils.mixins import ActionMobilePermissionMixin, ActionSerializerMixin
from apps.utils.response import Response
from services.marketplace_order import MarketplaceOrderService


class MarketplaceOrderMobileViewSet(
    ActionSerializerMixin,
    ActionMobilePermissionMixin,
    GenericViewSet,
):
    """Marketplace savatidan buyurtma yaratish (v2).

    Savat merchantlar bo'yicha bo'linadi va har biri uchun alohida `Order`
    yaratiladi. Limit yetmasa hech qanday order yaratilmaydi.
    """

    authentication_classes = (ClientTokenAuthentication,)
    queryset = Order.objects.all()
    serializer_class = MarketplaceOrderCreateSerializer

    marketplace_order_service = MarketplaceOrderService()

    ACTION_SERIALIZERS = {
        "create": MarketplaceOrderCreateSerializer,
    }
    ACTION_PERMISSIONS = {
        "create": [IsAuthenticated],
    }

    def filter_by_role(self, queryset):
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        client: Client = request.user

        # Limiti umuman yo'q mijoz avval skoringdan o'tishi kerak.
        # (Limit bor, lekin yetmagan holat pastda xato bilan qaytadi.)
        if (client.get_limit or 0) <= 0 and client.scoring_enable:
            return Response(
                data={"step": "scoring"},
                message="Limit mavjud emas",
                status=status.HTTP_200_OK,
            )

        result = self.marketplace_order_service.create_orders_from_cart(
            client=client,
            duration=validated["_duration"],
            items=validated["items"],
            additional_services=validated.get("additional_services") or [],
            pick_up_address=validated.get("pick_up_address"),
        )

        # Limit yetmadi — buyurtma emas, limitni oshirish uchun ariza yaratildi
        if result["step"] == "application":
            drafts = result["draft_orders"]
            return Response(
                data={
                    "step": "application",
                    "draft_orders_count": len(drafts),
                    "draft_orders": MarketplaceDraftOrderResultSerializer(
                        drafts, many=True
                    ).data,
                },
                message="Limit yetarli emas",
                status=status.HTTP_200_OK,
            )

        orders = result["orders"]
        return Response(
            data={
                "step": "success",
                "orders_count": len(orders),
                "total_price": str(sum(order.price for order in orders)),
                "orders": MarketplaceOrderResultSerializer(orders, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )
