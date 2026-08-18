from api.client.views import ClientViewSet
from api.routers import RouterClass

router = RouterClass()
router.register("clients", ClientViewSet, basename="client")

urlpatterns = router.urls
