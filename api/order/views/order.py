from django.db import transaction
from django.db.models import Q
from django.db.models.expressions import F
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from api.order.filters import OrderFilter
from api.order.serializers.order import (
    AdditionalServicePriceSerializer,
    OrderCompleteSerializer,
    OrderConfirmPickupOTPSerializer,
    OrderConfirmReceiptSerializer,
    OrderCreateSerializer,
    OrderDetailSerializer,
    OrderIdentifiersSerializer,
    OrderListSerializer,
    OrderQabzSearchSerializer,
    OrderStatusLogSerializer,
    OrderUpdateIdentifiersSerializer,
    OrderUpdateSerializer,
    PaymentFactRowSerializer,
)
from apps.order.choices import OrderSourceChoices, OrderStatusChoices
from apps.order.models import AdditionalServicePrice, Order
from apps.user.models import Role
from apps.utils.exceptions import BadRequestException, NotFoundException
from apps.utils.mixins import ActionPermissionMixin, ActionSerializerMixin, ReturnResponseMixin
from apps.utils.paginations import BasePagination
from apps.utils.permissions import amanat_staffs
from apps.utils.response import Response
from services.delivery import DeliveryService
from services.order import OrderService


class OrderViewSet(
    ActionSerializerMixin, ActionPermissionMixin, ReturnResponseMixin, ModelViewSet
):
    """
    Order ViewSet for Shop Assistant operations.

    Endpoints:
        - POST /orders/create/ - Create new order in draft status
        - GET /orders/list/ - Order list
        - GET /orders/detail/<pk>/ - Order detail
        - PUT /orders/<pk>/update-items/ - Update items (DRAFT status only)
        - POST /orders/<pk>/submit/ - Submit order (DRAFT → PENDING_VERIFICATION)

    Moderation (AmanatPay):
        - POST /orders/<pk>/approve/ - Approve order
        - POST /orders/<pk>/reject/ - Reject order

    Merchant:
        - GET /orders/<pk>/identifiers/ - Get identifiers status
        - PUT /orders/<pk>/update-identifiers/ - Update identifiers (PENDING_PARTNER only)
        - POST /orders/<pk>/confirm-identifiers/ - Confirm identifiers
        - POST /orders/<pk>/set-pickup/ - Set pickup branch

    Qabz & Delivery:
        - POST /orders/<pk>/start-delivery/ - Start delivery
        - POST /orders/<pk>/confirm-delivery/ - Confirm delivery (OTP)
        - POST /orders/<pk>/complete/ - Complete order
        - POST /orders/<pk>/cancel/ - Cancel order
        - POST /orders/<pk>/return/ - Return order
        - POST /orders/<pk>/confirm-return/ - Confirm return
    """

    queryset = Order.objects.all().select_related(
        "client", "duration", "created_by", "created_by__user"
    ).order_by("-created_at")

    serializer_class = OrderListSerializer
    pagination_class = BasePagination
    service = OrderService()

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = OrderFilter
    search_fields = [
        "number",
        "client__phone",
    ]

    ACTION_SERIALIZERS = {
        "list": OrderListSerializer,
        "retrieve": OrderDetailSerializer,
        "create": OrderCreateSerializer,
        "update": OrderUpdateSerializer,
        "identifiers": OrderIdentifiersSerializer,
        "update_identifiers": OrderUpdateIdentifiersSerializer,
        "status_logs": OrderStatusLogSerializer,
        "status_flow": OrderStatusLogSerializer,
        "confirm_receipt": OrderConfirmReceiptSerializer,
        "complete": OrderCompleteSerializer,
        "confirm_pickup_with_otp": OrderConfirmPickupOTPSerializer,
        "qabz_search": OrderQabzSearchSerializer,
        "payment_facts": PaymentFactRowSerializer,
    }
    ACTION_PERMISSIONS = {
        "additional_services": [IsAuthenticated],
        "attributes": [IsAuthenticated],
    }

    def get_queryset_by_action(self, queryset):
        if self.action == "list":
            return queryset.annotate(
                company_name=F("company__name"),
                branch_name=F("branch__name"),
            )
        elif self.action == "retrieve":
            return queryset.annotate(
                company_name=F("company__name"),
                branch_name=F("branch__name"),
            ).prefetch_related(
                "items",
                "items__product",
                "items__product__category",
                # `marketplace_product` javob maydoni aksning teskari
                # bog'lanishidan o'qiladi — prefetchsiz N+1 bo'lardi
                "items__product__marketplace_mirror",
                "delivery__region",
                "receipt_images",
            )
        elif self.action == "qabz_orders":
            role = self._get_role(self.request)
            return queryset.filter(
                status=OrderStatusChoices.QABZ_COMPLETED,
                delivery__isnull=True,
                status_logs__status=OrderStatusChoices.QABZ_COMPLETED,
                status_logs__created_by=role,
            ).annotate(
                company_name=F("company__name"),
                branch_name=F("branch__name"),
            ).distinct()
        return queryset

    def filter_by_role(self, queryset):
        """Filter orders by user role (branch or company)"""
        if amanat_staffs(self.request):
            return queryset

        company_tin = self.request.credentials.get("company_tin")
        if company_tin:
            queryset = queryset.filter(company__tin=company_tin)
            role = self._get_role(self.request)
            if role and role.branch_id:
                branch = role.branch
                if branch and branch.is_online:
                    return queryset.filter(
                        Q(
                            source__in=[
                                OrderSourceChoices.ONLINE,
                                OrderSourceChoices.MARKETPLACE,
                            ]
                        )
                        | Q(
                            source=OrderSourceChoices.OFFLINE,
                            branch_id=role.branch_id,
                        )
                    )

                return queryset.filter(source=OrderSourceChoices.OFFLINE)
            else:
                return queryset

        return queryset.none()

    def _get_role(self, request) -> Role:
        """Get Role from request credentials"""
        credentials = getattr(request, 'credentials', {})
        role_id = credentials.get('role_id')
        if role_id:
            return Role.objects.select_related("user", "company", "branch").filter(id=role_id).first()
        return Role.objects.none().first()

    def create(self, request, *args, **kwargs):
        """
        Create new order with products.
        Products can be specified by product_id or created with (name + category_id).
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        created_by = self._get_role(request)

        delivery_data = data.get("delivery")

        with transaction.atomic():
            order = self.service.create_order(
                client_id=data["client_id"],
                duration_id=data["duration_id"],
                items=data["items"],
                company_id=created_by.company_id,
                branch_id=created_by.branch_id,
                created_by=created_by,
                delivery_data=delivery_data,
                prepayment=data.get("prepayment"),
                first_payment=data.get("first_payment"),
                additional_services=data.get("additional_services"),
                pick_up_address=data.get("pick_up_address"),
            )

        return Response(
            message=_("Buyurtma muvaffaqiyatli yaratildi"),
            data={"order_id": order.id, "order_number": order.number},
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        """
        Update order items (only for DRAFT status).
        Replaces all existing items with new ones.
        Supports both existing products (product_id) and new products (name + category_id).
        """
        order = self.get_object()

        # Validate request data
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Update items via service
        self.service.update_order(
            order=order,
            items_data=serializer.validated_data.get("items"),
            duration_id=serializer.validated_data.get("duration_id"),
            prepayment=serializer.validated_data.get("prepayment"),
            first_payment=serializer.validated_data.get("first_payment"),
            additional_services=serializer.validated_data.get("additional_services"),
            pick_up_address=serializer.validated_data.get("pick_up_address"),
            created_by=getattr(request.user, "role", None),
        )

        return Response(
            message="Mahsulotlar yangilandi",
            status=status.HTTP_200_OK,
        )

    @action(methods=["GET"], detail=False, url_path="search", url_name="qabz_orders")
    def qabz_search(self, request, *args, **kwargs):
        order_number = request.query_params.get("order_number")
        if order_number:
            order_number = order_number.upper()
            order = Order.objects.filter(number=order_number, status=OrderStatusChoices.QABZ_PENDING).first()
            if order:
                data = self.get_serializer(order).data
                return Response(data=data)
        return Response(status=status.HTTP_200_OK, data=None)

    @action(methods=["GET"], detail=False, url_path="list", url_name="qabz_orders")
    def qabz_orders(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    @action(methods=["POST"], detail=True, url_path="submit", url_name="submit")
    def submit(self, request, pk=None):
        """
        Submit order to moderation (DRAFT → PENDING_VERIFICATION).
        After this, items cannot be changed.
        """
        order = self.get_object()
        created_by = self._get_role(request)

        self.service.submit_order(order, created_by)

        return Response(
            data={
                "id": order.id,
                "number": order.number,
                "status": order.status,
                "status_desc": order.get_status_display(),
            },
            message="Buyurtma moderatsiyaga yuborildi",
            status=status.HTTP_200_OK,
        )

    @action(methods=["GET"], detail=True, url_path="identifiers", url_name="identifiers")
    def identifiers(self, request, pk=None):
        """
        Get identifiers status for all items.
        Shows which items need IMEI and current status.
        """
        order = self.get_object()

        result = self.service.get_identifiers_status(order)

        return Response(
            data=result,
            status=status.HTTP_200_OK,
        )

    @action(methods=["PUT"], detail=True, url_path="update-identifiers", url_name="update_identifiers")
    def update_identifiers(self, request, pk=None):
        """
        Update IMEI/marking for order items (only for PENDING_PARTNER status).
        """
        order = self.get_object()

        # Validate request data
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = self.service.update_identifiers(
            order=order,
            items_data=serializer.validated_data["items"]
        )

        result = self.service.get_identifiers_status(order)

        return Response(
            # data={"items": result["items"]},
            data=result,
            message="Identifierlar yangilandi",
            status=status.HTTP_200_OK,
        )

    @action(methods=["POST"], detail=True, url_path="set-pickup", url_name="set_pickup")
    def set_pickup(self, request, pk=None):
        """
        Sklad/Filial orqali olib ketishni (Pickup) sozlash.
        """
        order = self.get_object()

        branch_id = request.data.get("branch_id")
        if not branch_id:
            return Response(
                success=False,
                error="missing_branch",
                message="branch_id kiritilishi shart",
                status=status.HTTP_400_BAD_REQUEST,
            )

        order = self.service.set_pickup_branch(
            order=order,
            branch_id=branch_id,
        )

        return Response(
            data={"id": order.id, "delivery_address": order.delivery.pickup_address},
            message="Olib ketish manzili (Sklad) tanlandi",
            status=status.HTTP_200_OK,
        )

    # ==================== MODERATION (AmanatPay) ====================

    @action(methods=["POST"], detail=True, url_path="approve", url_name="approve")
    def approve(self, request, pk=None):
        """
        Approve order after moderation (PENDING_VERIFICATION → PENDING_PARTNER).
        """
        order = self.get_object()
        created_by = self._get_role(request)

        order = self.service.approve_order(order, created_by)

        return Response(
            data={
                "id": order.id,
                "number": order.number,
                "status": order.status,
                "status_desc": order.get_status_display(),
            },
            message="Buyurtma tasdiqlandi, hamkor IMEI kiritishini kutmoqda",
            status=status.HTTP_200_OK,
        )

    # ==================== MERCHANT ====================

    @action(methods=["POST"], detail=True, url_path="confirm-identifiers", url_name="confirm_identifiers")
    def confirm_identifiers(self, request, pk=None):
        """
        Confirm identifiers and complete merchant confirmation
        (PENDING_PARTNER → QABZ_PENDING).
        """
        order = self.get_object()
        created_by = self._get_role(request)

        order = self.service.confirm_identifiers(
            order=order,
            created_by=created_by,
        )

        if hasattr(order, "delivery") and order.delivery:
            message = _("Yetazib berish xizmatiga yuborildi, qabz kutilmoqda")
        else:
            message = _("Qabz menejerga yuborildi, qabz kutilmoqda")

        return Response(
            data={"id": order.id, "status": order.status},
            message=message,
            status=status.HTTP_200_OK,
        )

    @action(methods=["POST"], detail=True, url_path="confirm-receipt", url_name="confirm_receipt")
    def confirm_receipt(self, request, pk=None):
        """
        Qabzni qabul qilib olish (QABZ_PENDING -> QABZ_COMPLETED).
        AmanatPay hodimi buyurtmani qabul qilib oldi.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = self.get_object()
        created_by = self._get_role(request)

        order = self.service.confirm_receipt(
            order=order,
            created_by=created_by,
            images=serializer.validated_data.get("images", []),
        )

        return Response(
            data={"id": order.id, "status": order.status},
            message="Buyurtma muvaffaqiyatli qabul qilindi",
            status=status.HTTP_200_OK,
        )

    @action(methods=["POST"], detail=False, url_path="confirm-pickup-otp", url_name="confirm_pickup_otp")
    def confirm_pickup_with_otp(self, request, pk=None):
        """
        Pickup (O'zi olib ketish) gacha borgan buyurtmani OTP tekshirib ACTIVE qilish.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order_id = serializer.validated_data["id"]
        otp_code = serializer.validated_data["otp"]

        order = Order.objects.filter(pk=order_id).first()
        if not order:
            raise NotFoundException(_("Buyurtma topilmadi"))

        created_by = self._get_role(request)

        order = self.service.confirm_pickup_with_otp(
            order=order,
            otp_code=str(otp_code),
            created_by=created_by,
        )

        return Response(
            data={"id": order.id, "status": order.status},
            message="Buyurtma muvaffaqiyatli olib ketildi va status faollashtirildi",
            status=status.HTTP_200_OK,
        )

    @action(methods=["POST"], detail=True, url_path="complete", url_name="complete")
    def complete(self, request, pk=None):
        """
        Complete order successfully (QABZ_COMPLETED → ACTIVE).
        Client tovarni olganini rasmlar bilan tasdiqlaydi.
        Content-Type: multipart/form-data
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = self.get_object()
        created_by = self._get_role(request)

        order = self.service.complete_order(
            order=order,
            created_by=created_by,
            images=serializer.validated_data.get("images", []),
        )

        return Response(
            data={
                "id": order.id,
                "number": order.number,
                "status": order.status,
                "status_desc": order.get_status_display(),
            },
            message="Rasm muvaffaqiyatli yuklandi",
            status=status.HTTP_200_OK,
        )

    @action(methods=["POST"], detail=True, url_path="cancel", url_name="cancel")
    def cancel(self, request, pk=None):
        """
        Cancel order
        """
        order = self.get_object()
        created_by = self._get_role(request)
        reason = request.data.get("reason", "")
        order = self.service.cancel_order(order, created_by, reason)

        return Response(
            data={
                "id": order.id,
                "number": order.number,
                "status": order.status,
                "status_desc": order.get_status_display(),
            },
            message="Buyurtma bekor qilindi",
            status=status.HTTP_200_OK,
        )

    @action(methods=["POST"], detail=True, url_path="return", url_name="return")
    def return_order(self, request, pk=None):
        """
        Return order
        """
        order = self.get_object()
        created_by = self._get_role(request)
        reason = request.data.get("reason", "")
        order = self.service.return_order(order, created_by, reason)

        return Response(
            data={
                "id": order.id,
                "number": order.number,
                "status": order.status,
                "status_desc": order.get_status_display(),
            },
            message="Buyurtma qaytarilishi boshlandi",
            status=status.HTTP_200_OK,
        )

    @action(methods=["POST"], detail=True, url_path="confirm-return", url_name="confirm_return")
    def confirm_return(self, request, pk=None):
        """
        Confirm pending return
        """
        order = self.get_object()
        created_by = self._get_role(request)
        reason = request.data.get("reason", "")
        order = self.service.confirm_return(order, created_by, reason)

        return Response(
            data={
                "id": order.id,
                "number": order.number,
                "status": order.status,
                "status_desc": order.get_status_display(),
            },
            message="Buyurtma qaytarilishi tasdiqlandi",
            status=status.HTTP_200_OK,
        )

    @action(methods=["GET"], detail=True, url_path="status-logs", url_name="status_logs")
    def status_logs(self, request, pk=None):
        """
        Get order status logs history.
        """
        order = self.get_object()
        logs = order.status_logs.select_related("created_by", "created_by__user").order_by("id")
        serializer = self.get_serializer(logs, many=True)

        return Response(
            data=serializer.data,
            status=status.HTTP_200_OK,
        )

    @action(methods=["GET"], detail=True, url_path="status-flow", url_name="status_flow")
    def status_flow(self, request, pk=None):
        """
        Get order status flow.
        Filters out duplicate statuses, preserving only the last chronological occurrence's data
        while maintaining the original sequence order.
        """
        order = self.get_object()
        logs = order.status_logs.select_related("created_by", "created_by__user").order_by("id")

        filtered_logs_dict = {}
        for log in logs:
            filtered_logs_dict[log.status] = log

        filtered_logs = list(filtered_logs_dict.values())

        serializer = self.get_serializer(filtered_logs, many=True)

        return Response(
            data=serializer.data,
            status=status.HTTP_200_OK,
        )

    @action(methods=["GET"], detail=True, url_path="payment-facts", url_name="payment_facts")
    def payment_facts(self, request, pk=None):
        order = self.get_object()
        rows = self.service.get_payment_facts(order)
        serializer = self.get_serializer(rows, many=True)
        data = serializer.data
        return Response(data=data, status=status.HTTP_200_OK)

    @action(methods=["GET"], detail=True, url_path="shipping-note", url_name="shipping_note")
    def shipping_note(self, request, pk=None):
        order = self.get_object()
        delivery = getattr(order, "delivery", None)
        if not delivery:
            raise BadRequestException(_("Buyurtma manzilini aniqlash mumkin emas"))

        delivery_service = DeliveryService()
        context = delivery_service.generate_shipping_note(delivery=delivery, download=False)

        return render(request, "documents/shipping_note.html", context)

    @action(methods=["GET"], detail=True, url_path="download-shipping-note", url_name="download_shipping_note")
    def download_shipping_note(self, request, pk=None):
        """
        Download Shipping Note (Yukxati) PDF for the order.
        """

        order = self.get_object()
        delivery = getattr(order, "delivery", None)
        if not delivery:
            raise BadRequestException(_("Buyurtma manzilini aniqlash mumkin emas"))

        delivery_service = DeliveryService()
        pdf_bytes = delivery_service.generate_shipping_note(delivery=delivery)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="Накладная_{order.number}.pdf"'  # noqa
        return response

    @action(detail=False, methods=["get"], url_path="additional-services")
    def additional_services(self, request):
        """
        API view to list all available additional service prices.
        Available for authenticated users to get actual `service_id` limits.
        """
        queryset = AdditionalServicePrice.objects.all()
        serializer = AdditionalServicePriceSerializer(queryset, many=True)
        return Response(data=serializer.data)

    @action(methods=["GET"], detail=False, url_path="attributes", url_name="attributes")
    def attributes(self, request):
        return Response(data=self.service.get_order_attributes(), status=status.HTTP_200_OK)

    @action(methods=["GET"], detail=False, url_path="status-counts", url_name="status_counts")
    def status_counts(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        data = self.service.get_status_counts(queryset=queryset)
        return Response(data=data, status=status.HTTP_200_OK)
