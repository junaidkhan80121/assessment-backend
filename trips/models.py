"""
Trip model — stores trip inputs and computed HOS plan results.
"""
import uuid

from django.db import models


class Trip(models.Model):
    """A trip plan with HOS-compliant schedule."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPUTING = "COMPUTING", "Computing"
        COMPUTED = "COMPUTED", "Computed"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ── Inputs ───────────────────────────────────────────────────────────
    current_location = models.CharField(max_length=255)
    current_location_lat = models.FloatField(default=0.0)
    current_location_lon = models.FloatField(default=0.0)

    pickup_location = models.CharField(max_length=255)
    pickup_location_lat = models.FloatField(default=0.0)
    pickup_location_lon = models.FloatField(default=0.0)

    dropoff_location = models.CharField(max_length=255)
    dropoff_location_lat = models.FloatField(default=0.0)
    dropoff_location_lon = models.FloatField(default=0.0)

    current_cycle_used = models.FloatField(
        help_text="Hours already used in the current 8-day cycle (0–70)"
    )

    # ── Computed Outputs ─────────────────────────────────────────────────
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.TextField(blank=True, default="")

    # Route data from OpenRouteService
    route_geometry = models.JSONField(default=list, blank=True)
    route_options = models.JSONField(default=list, blank=True)
    route_instructions = models.JSONField(default=list, blank=True)
    total_distance_miles = models.FloatField(default=0.0)
    leg1_miles = models.FloatField(default=0.0)
    leg2_miles = models.FloatField(default=0.0)
    leg1_duration_hours = models.FloatField(default=0.0)
    leg2_duration_hours = models.FloatField(default=0.0)

    # HOS plan result
    stops = models.JSONField(default=list, blank=True)
    daily_logs = models.JSONField(default=list, blank=True)
    total_on_duty_hours = models.FloatField(default=0.0)
    total_drive_hours = models.FloatField(default=0.0)
    hos_compliant = models.BooleanField(default=False)
    weekly_hours_used = models.FloatField(default=0.0)
    weekly_hours_remaining = models.FloatField(default=0.0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Trip {self.id} — {self.current_location} → {self.dropoff_location}"
