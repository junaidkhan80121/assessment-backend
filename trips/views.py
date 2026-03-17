"""
Trip API views.
"""
import logging

from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view

from .models import Trip
from .serializers import TripCreateSerializer, TripDetailSerializer
from .routing import geocode_location, get_route
from .hos_engine import plan_trip

logger = logging.getLogger(__name__)


@extend_schema_view(
    create=extend_schema(
        summary="Create a new trip plan",
        description=(
            "Accepts current/pickup/dropoff locations and cycle hours, "
            "geocodes the locations, calculates routes, and computes an "
            "FMCSA-compliant HOS schedule."
        ),
        request=TripCreateSerializer,
        responses={201: TripDetailSerializer},
    ),
    retrieve=extend_schema(
        summary="Retrieve a trip plan",
        description="Returns the full trip plan details including stops and daily logs.",
        responses={200: TripDetailSerializer},
    ),
    list=extend_schema(
        summary="List all trips",
        description="Returns a list of all trip plans, ordered by creation date.",
        responses={200: TripDetailSerializer(many=True)},
    ),
)
class TripViewSet(viewsets.ModelViewSet):
    """
    ViewSet for creating and retrieving trip plans.

    POST /api/trips/   — Create a new trip plan
    GET  /api/trips/   — List all trips
    GET  /api/trips/:id/ — Retrieve a trip by ID
    """
    queryset = Trip.objects.all()
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return TripCreateSerializer
        return TripDetailSerializer

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = TripCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Create trip in COMPUTING state
        trip = Trip.objects.create(
            current_location=data["current_location"],
            pickup_location=data["pickup_location"],
            dropoff_location=data["dropoff_location"],
            current_cycle_used=data["current_cycle_used"],
            status=Trip.Status.COMPUTING,
        )

        try:
            # Step 1: Geocode locations
            current_geo = geocode_location(data["current_location"])
            pickup_geo = geocode_location(data["pickup_location"])
            dropoff_geo = geocode_location(data["dropoff_location"])

            trip.current_location_lat = current_geo["lat"]
            trip.current_location_lon = current_geo["lon"]
            trip.pickup_location_lat = pickup_geo["lat"]
            trip.pickup_location_lon = pickup_geo["lon"]
            trip.dropoff_location_lat = dropoff_geo["lat"]
            trip.dropoff_location_lon = dropoff_geo["lon"]

            # Step 2: Get routes for both legs
            leg1_route = get_route(
                current_geo["lon"], current_geo["lat"],
                pickup_geo["lon"], pickup_geo["lat"],
            )
            leg2_route = get_route(
                pickup_geo["lon"], pickup_geo["lat"],
                dropoff_geo["lon"], dropoff_geo["lat"],
            )

            trip.leg1_miles = leg1_route["distance_miles"]
            trip.leg2_miles = leg2_route["distance_miles"]
            trip.leg1_duration_hours = leg1_route["duration_hours"]
            trip.leg2_duration_hours = leg2_route["duration_hours"]
            trip.total_distance_miles = trip.leg1_miles + trip.leg2_miles

            # Merge route geometries
            trip.route_geometry = leg1_route["geometry"] + leg2_route["geometry"]

            # Step 3: Run HOS engine
            hos_result = plan_trip(
                total_distance_miles=trip.total_distance_miles,
                leg1_miles=trip.leg1_miles,
                leg2_miles=trip.leg2_miles,
                leg1_duration_hours=trip.leg1_duration_hours,
                leg2_duration_hours=trip.leg2_duration_hours,
                current_cycle_used=data["current_cycle_used"],
                pickup_location=data["pickup_location"],
                dropoff_location=data["dropoff_location"],
                pickup_lat=pickup_geo["lat"],
                pickup_lon=pickup_geo["lon"],
                dropoff_lat=dropoff_geo["lat"],
                dropoff_lon=dropoff_geo["lon"],
                current_location=data["current_location"],
                current_lat=current_geo["lat"],
                current_lon=current_geo["lon"],
            )

            # Step 4: Store results
            trip.stops = hos_result["stops"]
            trip.daily_logs = hos_result["daily_logs"]
            trip.total_on_duty_hours = hos_result["total_on_duty_hours"]
            trip.total_drive_hours = hos_result["total_drive_hours"]
            trip.hos_compliant = hos_result["hos_compliant"]
            trip.weekly_hours_used = hos_result["weekly_hours_used"]
            trip.weekly_hours_remaining = hos_result["weekly_hours_remaining"]
            trip.status = Trip.Status.COMPUTED
            trip.save()

            output_serializer = TripDetailSerializer(trip)
            return Response(output_serializer.data, status=status.HTTP_201_CREATED)

        except ValueError as e:
            trip.status = Trip.Status.FAILED
            trip.error_message = str(e)
            trip.save()
            return Response(
                {"error": str(e), "trip_id": str(trip.id)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception as e:
            logger.exception("Unexpected error computing trip %s", trip.id)
            trip.status = Trip.Status.FAILED
            trip.error_message = f"Internal error: {str(e)}"
            trip.save()
            return Response(
                {"error": "An unexpected error occurred. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
