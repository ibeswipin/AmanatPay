from api.order.views import mobile
from apps.utils.viewset import RouterClass


router = RouterClass()
router.register(
    "", mobile.OrderMobileViewSet, basename="order-mobile"
)
