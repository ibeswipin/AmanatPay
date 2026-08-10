import re

from django.http import FileResponse
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from api.order.serializers.mobile import CreateOrderMobileSerializer, GetAppClientOrderSerializer, ReceiptOTPSerializer
from apps.order.models import DraftOrder, Order
from apps.personal.models import Cards
from apps.user.models import Client
from apps.utils.authentication import ClientTokenAuthentication
from apps.utils.exceptions import BadRequestException, PermissionDeniedException
from apps.utils.mixins import ActionMobilePermissionMixin, ActionSerializerMixin, ReturnResponseMixin
from apps.utils.paginations import BasePagination
from apps.utils.response import Response
from services import RadiusCRMService
from services.client import ClientService
from services.order import OrderService
from services.otp import OTPService


class OrderMobileViewSet(
    ActionSerializerMixin,
    ActionMobilePermissionMixin,
    ReturnResponseMixin,
    ModelViewSet,
):
    authentication_classes = (ClientTokenAuthentication,)
    queryset = Order.objects.all()
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    serializer_class = GetAppClientOrderSerializer
    pagination_class = BasePagination
    page_size = 10
    client_service = ClientService()
    radius_service = RadiusCRMService()
    order_service = OrderService()
    otp_service = OTPService()

    ACTION_SERIALIZERS = {
        "my_orders": GetAppClientOrderSerializer,
        "create": CreateOrderMobileSerializer,
        "request_receipt_otp": ReceiptOTPSerializer,
    }
    ACTION_PERMISSIONS = {
        "my_orders": [IsAuthenticated],
        "orders_count": [IsAuthenticated],
        "order_detail": [IsAuthenticated],
        "create": [IsAuthenticated],
        "contract": [IsAuthenticated],
        "request_receipt_otp": [IsAuthenticated],
        "payment_types": [IsAuthenticated],
        "pay_with_plum": [IsAuthenticated],
        "confirm_return": [IsAuthenticated],
    }

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        client: Client = request.user
        validated = serializer.validated_data

        result = self.order_service.create_mobile_order(
            client=client,
            duration=validated["_duration"],
            products_data=validated["_products_data"],
            additional_services=validated.get("_additional_services_data"),
            pick_up_address=validated.get("pick_up_address"),
        )

        step = result.get("step")

        if step == "scoring":
            return Response(data={"step": "scoring"}, status=200)

        elif step == "application":
            draft_order: DraftOrder = result.get("draft_order")
            return Response(
                data={"step": "application", "application_id": draft_order.id},
                status=200,
            )

        order: Order = result.get("order")
        return Response(
            data={"step": "success", "order_number": order.number}, status=200
        )

    @action(detail=False, methods=["GET"], url_path="my-orders")
    def my_orders(self, request, *args, **kwargs):
        status_ = request.query_params.get("status")
        available_statuses = {"unconfirmed": True, "done": True, "closed": True}
        if status_ and available_statuses.get(status_) is None:
            raise BadRequestException(
                detail="Allowed values are 'unconfirmed', 'done', 'closed'."
            )

        client: Client = self.request.user

        omonat_orders = self.client_service.client_orders_for_mobile(client, status_)
        # radius_orders = self.radius_service.get_client_orders(client, status_)

        # Serialize omonat orders
        omonat_data = GetAppClientOrderSerializer(
            omonat_orders, many=True, context={"request": request}
        ).data

        data = {
            "radius": [],
            "omonat": omonat_data,
        }
        return Response(data=data, status=200)

    @action(detail=False, methods=["GET"], url_path="orders-count")
    def orders_count(self, request, *args, **kwargs):
        client: Client = self.request.user

        data = {
            "radius": 0,
            "omonat": self.client_service.get_order_counts(client),
        }
        return Response(data=data, status=200)

    @action(detail=False, methods=["GET"], url_path=r"detail/(?P<number>[\w/-]+)")
    def order_detail(self, request, number, *args, **kwargs):
        if not number:
            raise BadRequestException(detail="Buyurtma raqami kiritilmagan")

        client: Client = self.request.user
        lang = getattr(client, "language", "uz")

        # RadiusCRM format check: AA-11/00001
        is_radius = re.match(r"^[A-Z]{2}-\d{2}/\d{5}$", number)

        if is_radius:
            # 1. RadiusCRM Order (1C)
            data = self.radius_service.get_state_from_1c(number)
            result = self.radius_service.payment_schedule_for_mobile_app(
                data, lang=lang
            )
        else:
            # 2. OmonatPay Order (Local)
            order = (
                Order.objects.select_related("company", "duration")
                .filter(number=number)
                .first()
            )
            if not order:
                raise BadRequestException(detail="Buyurtma topilmadi")

            result = self.order_service.get_mobile_order_detail(
                order, lang=lang, request=request
            )

        return Response(data=result, status=200)

    @action(detail=True, methods=["GET"], url_path="contract", url_name="contract")
    def contract(self, request, pk=None):
        """Buyurtma shartnomasi PDF ni yuklab olish."""
        order = self.get_object()

        # Faqat mijozning o'z buyurtmasini tekshirish
        if order.client_id != request.user.id:
            return Response(success=False, message="Ruxsat yo'q", status=403)

        contract = getattr(order, "contract", None)
        if not contract or not contract.file:
            return Response(
                success=False,
                message="Shartnoma hali tayyor emas",
                status=404,
            )

        return FileResponse(
            contract.file.open("rb"),
            content_type="application/pdf",
            as_attachment=True,
            filename=f"shartnoma_{order.number}.pdf",
        )

    @action(
        detail=True, methods=["GET"], url_path="receipt-otp", url_name="receipt-otp"
    )
    def request_receipt_otp(self, request, pk=None):
        """Buyurtmani qabul qilishda OTP kod olish."""
        order = self.get_object()

        if order.client_id != request.user.id:
            raise PermissionDeniedException()

        success, otp, error_message = self.otp_service.get_or_create_receipt_otp(
            client=request.user, order=order
        )

        if not success:
            raise BadRequestException(detail=error_message)

        serializer = self.get_serializer(otp)

        return Response(data=serializer.data)

    @action(
        detail=True,
        methods=["POST"],
        url_path="confirm-return",
        url_name="confirm-return",
    )
    def confirm_return(self, request, pk=None):
        """Client tomonidan return jarayonini merchant tasdig'i bosqichiga o'tkazadi."""
        order = self.get_object()

        if order.client_id != request.user.id:
            raise PermissionDeniedException()

        reason = request.data.get("reason", "")
        order = self.order_service.confirm_return_by_client(order, request.user, reason)

        return Response(
            data={
                "id": order.id,
                "number": order.number,
                "status": order.status,
                "status_desc": order.get_status_display(),
            },
            message="Buyurtma qaytarilishi merchant tasdig'iga yuborildi",
            status=200,
        )

    @action(
        detail=False,
        methods=["GET"],
        url_path=r"payment-types",
        url_name="payment-types",
    )
    def payment_types(self, request, *args, **kwargs):
        number = request.query_params.get("number")
        if not number:
            raise BadRequestException(detail="Buyurtma raqami kiritilmagan")

        data = self.order_service.get_payment_types_for_order(number, request=request)
        return Response(data=data, status=200)

    @action(
        detail=False,
        methods=["POST"],
        url_path=r"pay-with-plum",
        url_name="pay-with-plum",
    )
    def pay_with_plum(self, request, *args, **kwargs):
        number = request.data.get("number")
        card_id = request.data.get("card_id")
        amount = request.data.get("amount")

        if not number or not card_id or amount is None:
            raise BadRequestException(
                detail="Buyurtma raqami, karta ID va summa majburiy"
            )

        try:
            amount = int(amount)
        except ValueError:
            raise BadRequestException(detail="Summa to'g'ri kiritilmagan")

        card = Cards.objects.filter(
            pk=card_id, user=request.user, is_confirm=True
        ).first()
        if not card:
            raise BadRequestException(detail="Karta topilmadi yoki tasdiqlanmagan")

        order = Order.objects.filter(number=number).first()
        if not order:
            raise BadRequestException(detail="Buyurtma topilmadi")

        result = self.order_service.pay_order_with_plum(
            order=order, card=card, amount=amount
        )
        return Response(data=result, status=200)
