from django.urls import include, path

urlpatterns = [
    path("v1/", include("api.merchant.urls")),
    path("v1/", include("api.client.urls")),
    path("v1/", include("api.order.urls")),
]
