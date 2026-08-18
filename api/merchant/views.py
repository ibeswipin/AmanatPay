from rest_framework import viewsets

from api.merchant.serializers import MerchantSerializer
from apps.merchant.models import Merchant


class MerchantViewSet(viewsets.ModelViewSet):
    queryset = Merchant.objects.all()
    serializer_class = MerchantSerializer
    lookup_field = "uuid"
    filterset_fields = ("is_active",)
    search_fields = ("name", "inn")
