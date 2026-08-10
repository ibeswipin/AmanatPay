from django.urls import include, path

from api.order.views.draft_order import DraftOrderViewSet
from api.order.views.order import OrderViewSet


app_name = "order"

# Qabz & Delivery
qabz_urlpatterns = [
    path(
        "search/",
        OrderViewSet.as_view({"get": "qabz_search"}),
        name="list",
    ),
    path(
        "list/",
        OrderViewSet.as_view({"get": "qabz_orders"}),
        name="list",
    ),
    path(
        "<int:pk>/confirm-receipt/",
        OrderViewSet.as_view({"post": "confirm_receipt"}),
        name="order_confirm_receipt",
    ),
    path(
        "confirm-pickup-otp/",
        OrderViewSet.as_view({"post": "confirm_pickup_with_otp"}),
        name="order_confirm_pickup_otp",
    ),

    path(
        "<int:pk>/complete/",
        OrderViewSet.as_view({"post": "complete"}),
        name="order_complete",
    ),
    path(
        "detail/<int:pk>/",
        OrderViewSet.as_view({"get": "retrieve"}),
        name="detail",
    ),
]

draft_order_urlpatterns = [
    path(
        "list/",
        DraftOrderViewSet.as_view({"get": "list"}),
        name="list",
    ),
    path(
        "detail/<int:pk>/",
        DraftOrderViewSet.as_view({"get": "retrieve"}),
        name="detail",
    ),
    path(
        "attributes/",
        DraftOrderViewSet.as_view({"get": "attributes"}),
        name="attributes",
    ),
]

urlpatterns = [
    path("qabz/", include((qabz_urlpatterns, "qabz"), namespace="qabz")),
    path(
        "draft-order/",
        include((draft_order_urlpatterns, "draft_order"), namespace="draft_order"),
    ),
    # Basic CRUD
    path(
        "orders/create/",
        OrderViewSet.as_view({"post": "create"}),
        name="order_create",
    ),
    path(
        "orders/list/",
        OrderViewSet.as_view({"get": "list"}),
        name="order_list",
    ),
    path(
        "orders/status-counts/",
        OrderViewSet.as_view({"get": "status_counts"}),
        name="order_list",
    ),
    path(
        "orders/attributes/",
        OrderViewSet.as_view({"get": "attributes"}),
        name="order_attributes",
    ),
    path(
        "orders/detail/<int:pk>/",
        OrderViewSet.as_view({"get": "retrieve"}),
        name="order_detail",
    ),

    # Draft operations
    path(
        "orders/<int:pk>/update/",
        OrderViewSet.as_view({"put": "update"}),
        name="order_update",
    ),
    path(
        "orders/<int:pk>/submit/",
        OrderViewSet.as_view({"post": "submit"}),
        name="order_submit",
    ),

    # Moderation (AmanatPay)
    path(
        "orders/<int:pk>/approve/",
        OrderViewSet.as_view({"post": "approve"}),
        name="order_approve",
    ),
    # Merchant - IMEI/Marking
    path(
        "orders/<int:pk>/identifiers/",
        OrderViewSet.as_view({"get": "identifiers"}),
        name="order_identifiers",
    ),
    path(
        "orders/<int:pk>/update-identifiers/",
        OrderViewSet.as_view({"put": "update_identifiers"}),
        name="order_update_identifiers",
    ),
    path(
        "orders/<int:pk>/confirm-identifiers/",
        OrderViewSet.as_view({"post": "confirm_identifiers"}),
        name="order_confirm_identifiers",
    ),
    path(
        "orders/<int:pk>/set-pickup/",
        OrderViewSet.as_view({"post": "set_pickup"}),
        name="order_set_pickup",
    ),

    path(
        "orders/<int:pk>/cancel/",
        OrderViewSet.as_view({"post": "cancel"}),
        name="order_cancel",
    ),
    path(
        "orders/<int:pk>/return/",
        OrderViewSet.as_view({"post": "return_order"}),
        name="order_return",
    ),
    path(
        "orders/<int:pk>/confirm-return/",
        OrderViewSet.as_view({"post": "confirm_return"}),
        name="order_confirm_return",
    ),
    path(
        "orders/<int:pk>/status-logs/",
        OrderViewSet.as_view({"get": "status_logs"}),
        name="order_status_logs",
    ),
    path(
        "orders/<int:pk>/status-flow/",
        OrderViewSet.as_view({"get": "status_flow"}),
        name="order_status_flow",
    ),
    path(
        "orders/<int:pk>/payment-facts/",
        OrderViewSet.as_view({"get": "payment_facts"}),
        name="order_payment_facts",
    ),
    path(
        "orders/<int:pk>/shipping-note/",
        OrderViewSet.as_view({"get": "shipping_note"}),
        name="order_shipping_note",
    ),
    path(
        "orders/<int:pk>/download-shipping-note/",
        OrderViewSet.as_view({"get": "download_shipping_note"}),
        name="order_download_shipping_note",
    ),
]
