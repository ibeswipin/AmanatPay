from django_filters import rest_framework as filters

from apps.order.models import Order, Payment


class OrderFilter(filters.FilterSet):
    created_from = filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = filters.DateFilter(field_name="created_at", lookup_expr="date__lte")

    class Meta:
        model = Order
        fields = ("status", "merchant__uuid", "client__uuid", "created_from", "created_to")


class PaymentFilter(filters.FilterSet):
    is_paid = filters.BooleanFilter(field_name="paid_at", lookup_expr="isnull", exclude=True)

    class Meta:
        model = Payment
        fields = ("order__uuid", "due_date", "is_paid")
