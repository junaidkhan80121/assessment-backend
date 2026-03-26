"""
config URL Configuration for ELD Trip Planner.
"""
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("admin/", admin.site.urls),

    # ── API ───────────────────────────────────────────────────────────────
    path("api/v1/", include("trips.urls")),

    # ── API Docs ──────────────────────────────────────────────────────────
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema-v1"),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="schema-v1"), name="swagger-ui-v1"),
]
