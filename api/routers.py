from django.conf import settings
from rest_framework.routers import DefaultRouter, SimpleRouter

RouterClass = DefaultRouter if settings.DEBUG else SimpleRouter
