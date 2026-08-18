from api.order.views import OrderViewSet, PaymentViewSet
from api.routers import RouterClass

router = RouterClass()
router.register("orders", OrderViewSet, basename="order")
router.register("payments", PaymentViewSet, basename="payment")

urlpatterns = router.urls
