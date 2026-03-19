"""
Trip API views.
"""
import logging
import math

from django.db import transaction
from rest_framework import viewsets, status
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view

from .models import Trip
from .serializers import TripCreateSerializer, TripDetailSerializer
from .routing import geocode_location, get_route
from .hos_engine import plan_trip

logger = logging.getLogger(__name__)


def error_payload(code: str, message: str, details=None) -> dict:
    """Consistent API error response body for frontend consumers."""
    payload = {
        "code": code,
        "message": message,
    }
    if details is not None:
        payload["details"] = details
    return payload


def resolve_location(data: dict, field_name: str) -> dict:
    """
    Reuse client-selected coordinates when available to avoid redundant
    geocoding requests, otherwise geocode the provided location label.
    """
    lat = data.get(f"{field_name}_lat")
    lon = data.get(f"{field_name}_lon")
    label = data[field_name]

    if lat is not None and lon is not None:
        return {"lat": lat, "lon": lon, "label": label}

    return geocode_location(label)


def haversine_miles(point_a: list[float], point_b: list[float]) -> float:
    lat1, lon1 = math.radians(point_a[0]), math.radians(point_a[1])
    lat2, lon2 = math.radians(point_b[0]), math.radians(point_b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.8 * (2 * math.asin(math.sqrt(a)))


def interpolate_route_position(route_geometry: list[list[float]], progress_ratio: float) -> tuple[float, float] | None:
    if len(route_geometry) < 2:
        return None

    clamped_ratio = max(0.0, min(1.0, progress_ratio))
    segment_lengths: list[float] = []
    total_length = 0.0

    for index in range(len(route_geometry) - 1):
        segment_length = haversine_miles(route_geometry[index], route_geometry[index + 1])
        segment_lengths.append(segment_length)
        total_length += segment_length

    if total_length <= 0.001:
        return route_geometry[0][0], route_geometry[0][1]

    target_distance = total_length * clamped_ratio
    traversed = 0.0

    for index, segment_length in enumerate(segment_lengths):
        next_traversed = traversed + segment_length
        if target_distance <= next_traversed or index == len(segment_lengths) - 1:
            local_ratio = 0.0 if segment_length <= 0.001 else (target_distance - traversed) / segment_length
            start = route_geometry[index]
            end = route_geometry[index + 1]
            lat = start[0] + (end[0] - start[0]) * local_ratio
            lon = start[1] + (end[1] - start[1]) * local_ratio
            return lat, lon
        traversed = next_traversed

    last = route_geometry[-1]
    return last[0], last[1]


def attach_stop_coordinates(stops: list[dict], route_geometry: list[list[float]], total_distance_miles: float) -> list[dict]:
    enriched_stops: list[dict] = []

    for stop in stops:
        enriched = dict(stop)
        if (
            enriched.get("lat", 0.0) == 0.0
            and enriched.get("lon", 0.0) == 0.0
            and route_geometry
            and total_distance_miles > 0
        ):
            progress_ratio = enriched.get("progress_miles", 0.0) / total_distance_miles
            interpolated = interpolate_route_position(route_geometry, progress_ratio)
            if interpolated is not None:
                enriched["lat"], enriched["lon"] = interpolated
        enriched_stops.append(enriched)

    return enriched_stops


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
        if not serializer.is_valid():
            return Response(
                error_payload(
                    code="validation_error",
                    message="Please correct the highlighted trip inputs.",
                    details=serializer.errors,
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )
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
            current_geo = resolve_location(data, "current_location")
            pickup_geo = resolve_location(data, "pickup_location")
            dropoff_geo = resolve_location(data, "dropoff_location")

            trip.current_location_lat = current_geo["lat"]
            trip.current_location_lon = current_geo["lon"]
            trip.pickup_location_lat = pickup_geo["lat"]
            trip.pickup_location_lon = pickup_geo["lon"]
            trip.dropoff_location_lat = dropoff_geo["lat"]
            trip.dropoff_location_lon = dropoff_geo["lon"]

            # Step 2: Get route for Leg 1 (Current -> Pickup)
            leg1_route = get_route(
                current_geo["lon"], current_geo["lat"],
                pickup_geo["lon"], pickup_geo["lat"],
                alternatives=False
            )
            
            # Step 3: Get one or more alternative routes for Leg 2 (Pickup -> Dropoff).
            # The routing backend (Mapbox) will return multiple candidate routes
            # when alternatives=True. We will mark the fastest one and still
            # expose all options to the client.
            leg2_variants = get_route(
                pickup_geo["lon"], pickup_geo["lat"],
                dropoff_geo["lon"], dropoff_geo["lat"],
                alternatives=True,
            )
            
            route_options = []
            
            # Process each variant into a complete trip option
            for i, leg2_route in enumerate(leg2_variants):
                leg1_miles = leg1_route["distance_miles"]
                leg2_miles = leg2_route["distance_miles"]
                leg1_duration = leg1_route["duration_hours"]
                leg2_duration = leg2_route["duration_hours"]
                total_distance = leg1_miles + leg2_miles
                geometry = leg1_route["geometry"] + leg2_route["geometry"]
                instructions = [
                    *leg1_route.get("instructions", []),
                    *leg2_route.get("instructions", []),
                ]
                
                # Step 4: Run HOS engine for this specific variant
                try:
                    hos_result = plan_trip(
                        total_distance_miles=total_distance,
                        leg1_miles=leg1_miles,
                        leg2_miles=leg2_miles,
                        leg1_duration_hours=leg1_duration,
                        leg2_duration_hours=leg2_duration,
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
                    enriched_stops = attach_stop_coordinates(
                        hos_result["stops"],
                        geometry,
                        total_distance,
                    )
                    
                    route_options.append({
                        "id": i,
                        "leg1_miles": leg1_miles,
                        "leg2_miles": leg2_miles,
                        "total_distance_miles": total_distance,
                        "leg1_duration_hours": leg1_duration,
                        "leg2_duration_hours": leg2_duration,
                        "route_geometry": geometry,
                        "route_instructions": instructions,
                        "stops": enriched_stops,
                        "daily_logs": hos_result["daily_logs"],
                        "total_on_duty_hours": hos_result["total_on_duty_hours"],
                        "total_drive_hours": hos_result["total_drive_hours"],
                        "hos_compliant": hos_result["hos_compliant"],
                        "weekly_hours_used": hos_result["weekly_hours_used"],
                        "weekly_hours_remaining": hos_result["weekly_hours_remaining"],
                    })
                except ValueError as e:
                    logger.warning("Variant %s violated constraints immediately: %s", i, e)

            if not route_options:
                raise ValueError("No viable routes could be planned within limits.")

            # Step 5: Rank and annotate route options.
            # Fastest route is the one with the smallest total drive duration.
            route_options.sort(key=lambda opt: opt["total_distance_miles"])
            fastest_index = min(
                range(len(route_options)),
                key=lambda idx: route_options[idx]["total_drive_hours"],
            )
            for idx, option in enumerate(route_options):
                option["is_fastest"] = idx == fastest_index
                option["label"] = "Fastest route" if option["is_fastest"] else "Alternative route"

            # Use the fastest route as the primary trip geometry.
            best_route = route_options[fastest_index]
            
            trip.route_options = route_options
            trip.route_instructions = best_route["route_instructions"]
            trip.leg1_miles = best_route["leg1_miles"]
            trip.leg2_miles = best_route["leg2_miles"]
            trip.leg1_duration_hours = best_route["leg1_duration_hours"]
            trip.leg2_duration_hours = best_route["leg2_duration_hours"]
            trip.total_distance_miles = best_route["total_distance_miles"]
            trip.route_geometry = best_route["route_geometry"]
            trip.stops = best_route["stops"]
            trip.daily_logs = best_route["daily_logs"]
            trip.total_on_duty_hours = best_route["total_on_duty_hours"]
            trip.total_drive_hours = best_route["total_drive_hours"]
            trip.hos_compliant = best_route["hos_compliant"]
            trip.weekly_hours_used = best_route["weekly_hours_used"]
            trip.weekly_hours_remaining = best_route["weekly_hours_remaining"]
            
            trip.status = Trip.Status.COMPUTED
            trip.save()

            output_serializer = TripDetailSerializer(trip)
            return Response(output_serializer.data, status=status.HTTP_201_CREATED)

        except ValueError as e:
            trip.status = Trip.Status.FAILED
            trip.error_message = str(e)
            trip.save()
            return Response(
                {
                    **error_payload(
                        code="trip_planning_failed",
                        message=str(e),
                    ),
                    "trip_id": str(trip.id),
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except Exception as e:
            logger.exception("Unexpected error computing trip %s", trip.id)
            trip.status = Trip.Status.FAILED
            trip.error_message = f"Internal error: {str(e)}"
            trip.save()
            return Response(
                error_payload(
                    code="internal_error",
                    message="An unexpected error occurred while computing the trip. Please try again.",
                ),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
