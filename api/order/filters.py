from django_filters import rest_framework
from django_filters.rest_framework import FilterSet

from apps.order.models import DraftOrder, Order


class OrderFilter(FilterSet):
    start_date = rest_framework.DateFilter(
        field_name="created_at", lookup_expr="date__gte"
    )
    end_date = rest_framework.DateFilter(
        field_name="created_at", lookup_expr="date__lte"
    )
    pinfl = rest_framework.CharFilter(field_name="client__personal__pinfl", lookup_expr="exact")
    full_name = rest_framework.CharFilter(field_name="client__full_name", lookup_expr="icontains")
    phone_number = rest_framework.CharFilter(field_name="client__phone", lookup_expr="icontains")
    number = rest_framework.CharFilter(field_name="number", lookup_expr="icontains")

    class Meta:
        model = Order
        fields = {
            "duration": ["exact"],
            "company": ["exact"],
            "status": ["exact"],
        }


class DraftOrderFilter(FilterSet):
    start_date = rest_framework.DateFilter(
        field_name="created_at", lookup_expr="date__gte"
    )
    end_date = rest_framework.DateFilter(
        field_name="created_at", lookup_expr="date__lte"
    )
    pinfl = rest_framework.CharFilter(
        field_name="client__personal__pinfl", lookup_expr="exact"
    )
    full_name = rest_framework.CharFilter(
        field_name="client__full_name", lookup_expr="icontains"
    )
    phone_number = rest_framework.CharFilter(
        field_name="client__phone", lookup_expr="icontains"
    )

    class Meta:
        model = DraftOrder
        fields = {
            "duration": ["exact"],
            "company": ["exact"],
        }
