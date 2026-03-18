"""
DRF Serializers for Trip model.
"""
from rest_framework import serializers
from .models import Trip


class TripCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a new trip (input only)."""

    current_location_lat = serializers.FloatField(required=False)
    current_location_lon = serializers.FloatField(required=False)
    pickup_location_lat = serializers.FloatField(required=False)
    pickup_location_lon = serializers.FloatField(required=False)
    dropoff_location_lat = serializers.FloatField(required=False)
    dropoff_location_lon = serializers.FloatField(required=False)

    class Meta:
        model = Trip
        fields = [
            "current_location",
            "current_location_lat",
            "current_location_lon",
            "pickup_location",
            "pickup_location_lat",
            "pickup_location_lon",
            "dropoff_location",
            "dropoff_location_lat",
            "dropoff_location_lon",
            "current_cycle_used",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)

        for prefix in ("current_location", "pickup_location", "dropoff_location"):
            lat = attrs.get(f"{prefix}_lat")
            lon = attrs.get(f"{prefix}_lon")
            if (lat is None) != (lon is None):
                raise serializers.ValidationError(
                    {prefix: "Latitude and longitude must be provided together."}
                )

        return attrs

    def validate_current_cycle_used(self, value: float) -> float:
        if value < 0.0 or value > 70.0:
            raise serializers.ValidationError(
                "Cycle hours used must be between 0.0 and 70.0"
            )
        return value

    def validate_current_location(self, value: str) -> str:
        if not value or not value.strip():
            raise serializers.ValidationError("Current location is required")
        return value.strip()

    def validate_pickup_location(self, value: str) -> str:
        if not value or not value.strip():
            raise serializers.ValidationError("Pickup location is required")
        return value.strip()

    def validate_dropoff_location(self, value: str) -> str:
        if not value or not value.strip():
            raise serializers.ValidationError("Dropoff location is required")
        return value.strip()


class TripDetailSerializer(serializers.ModelSerializer):
    """Serializer for reading trip details (output)."""

    class Meta:
        model = Trip
        fields = [
            "id",
            "current_location",
            "current_location_lat",
            "current_location_lon",
            "pickup_location",
            "pickup_location_lat",
            "pickup_location_lon",
            "dropoff_location",
            "dropoff_location_lat",
            "dropoff_location_lon",
            "current_cycle_used",
            "status",
            "error_message",
            "route_geometry",
            "route_options",
            "total_distance_miles",
            "leg1_miles",
            "leg2_miles",
            "leg1_duration_hours",
            "leg2_duration_hours",
            "stops",
            "daily_logs",
            "total_on_duty_hours",
            "total_drive_hours",
            "hos_compliant",
            "weekly_hours_used",
            "weekly_hours_remaining",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
