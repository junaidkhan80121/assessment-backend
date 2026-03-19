"""
Trip app URL configuration.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import TripViewSet, health_check

router = DefaultRouter()
router.register(r"trips", TripViewSet, basename="trip")

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("", include(router.urls)),
]
