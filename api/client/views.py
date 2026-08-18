from rest_framework import viewsets

from api.client.serializers import ClientSerializer
from apps.client.models import Client


class ClientViewSet(viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    lookup_field = "uuid"
    search_fields = ("full_name", "phone", "passport")
