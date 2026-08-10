from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ReadOnlyModelViewSet

from api.order.filters import DraftOrderFilter
from api.order.serializers.draft_order import DraftOrderDetailSerializer, DraftOrderListSerializer
from apps.company.models import Company
from apps.merchandise.models import Duration
from apps.order.models import DraftOrder
from apps.utils.mixins import ActionPermissionMixin, ActionSerializerMixin, ReturnResponseMixin
from apps.utils.paginations import BasePagination
from apps.utils.permissions import amanat_staffs
from apps.utils.response import Response


class DraftOrderViewSet(
    ActionSerializerMixin, ActionPermissionMixin, ReturnResponseMixin, ReadOnlyModelViewSet
):
    queryset = DraftOrder.objects.all().select_related(
        "client", "company", "duration"
    ).order_by("-created_at")
    serializer_class = DraftOrderListSerializer
    pagination_class = BasePagination

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = DraftOrderFilter
    search_fields = [
        "$id",
        "$client__phone",
        "$client__full_name",
    ]

    ACTION_SERIALIZERS = {
        "list": DraftOrderListSerializer,
        "retrieve": DraftOrderDetailSerializer,
    }
    ACTION_PERMISSIONS = {
        "attributes": [IsAuthenticated],
    }

    def get_queryset_by_action(self, queryset):
        if self.action == "retrieve":
            return queryset.prefetch_related(
                "items",
                "items__product",
                "items__product__category",
                # `marketplace_product` javob maydoni aksning teskari
                # bog'lanishidan o'qiladi — prefetchsiz N+1 bo'lardi
                "items__product__marketplace_mirror",
            )
        return queryset

    def filter_by_role(self, queryset):
        if amanat_staffs(self.request):
            return queryset

        company_tin = getattr(self.request, "credentials", {}).get("company_tin")
        if company_tin:
            return queryset.filter(company__tin=company_tin)
        return queryset.none()

    @action(methods=["GET"], detail=False, url_path="attributes", url_name="attributes")
    def attributes(self, request):
        data = {
            "duration": [
                {"value": duration.id, "label": duration.name}
                for duration in Duration.objects.all().order_by("months", "name")
            ],
            "company": [
                {"value": company.id, "label": company.name}
                for company in Company.objects.all().order_by("name")
            ],
        }
        return Response(data=data, status=status.HTTP_200_OK)
