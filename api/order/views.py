from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from api.order.filters import OrderFilter, PaymentFilter
from api.order.serializers import OrderSerializer, PaymentSerializer, ScheduleCreateSerializer
from apps.order.models import Order, Payment
from services.order import InstallmentError, build_schedule, cancel


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.select_related("merchant", "client").prefetch_related("payments")
    serializer_class = OrderSerializer
    filterset_class = OrderFilter
    lookup_field = "uuid"
    search_fields = ("client__full_name", "client__phone")

    @action(detail=True, methods=["post"], url_path="schedule")
    def schedule(self, request, uuid=None):
        """Построить график рассрочки по заказу."""
        order = self.get_object()
        payload = ScheduleCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            build_schedule(order, payload.validated_data.get("first_due"))
        except InstallmentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        order.refresh_from_db()  # сбрасывает prefetch-кэш payments
        return Response(self.get_serializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, uuid=None):
        order = self.get_object()
        try:
            cancel(order)
        except InstallmentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(order).data)


class PaymentViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Payment.objects.select_related("order")
    serializer_class = PaymentSerializer
    filterset_class = PaymentFilter
    lookup_field = "uuid"

    @action(detail=True, methods=["post"])
    def pay(self, request, uuid=None):
        payment = self.get_object()
        payment.pay()
        return Response(self.get_serializer(payment).data)
