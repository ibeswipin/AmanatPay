
from rest_framework.routers import DefaultRouter, SimpleRouter
from django.conf import settings

if settings.DEBUG:
    RouterClass = DefaultRouter
else:
    RouterClass = SimpleRouter
