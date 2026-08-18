from api.merchant.views import MerchantViewSet
from api.routers import RouterClass

router = RouterClass()
router.register("merchants", MerchantViewSet, basename="merchant")

urlpatterns = router.urls
