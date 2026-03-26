"""
Trip API views.
"""
from concurrent.futures import ThreadPoolExecutor
import logging
import math
import threading
from time import perf_counter

from django.conf import settings
from django.db import transaction
from django.db import close_old_connections
from django.views.decorators.csrf import csrf_exempt
from rest_framework import serializers, viewsets, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view, inline_serializer

from .models import Trip
from .serializers import TripCreateSerializer, TripDetailSerializer, TripListSerializer
from .routing import geocode_location, get_route, find_nearby_stop_poi
from .hos_engine import plan_trip
from .throttles import TripReadAnonThrottle, TripWriteAnonThrottle

logger = logging.getLogger(__name__)


class TripHistoryPagination(PageNumberPagination):
    page_size = 5
    page_size_query_param = "page_size"
    max_page_size = 50


def normalize_route_variants(routes: dict | list[dict]) -> list[dict]:
    if isinstance(routes, dict):
        return [routes]
    return list(routes)


def route_geometry_signature(geometry: list[list[float]]) -> tuple:
    if not geometry:
        return ()

    sample_indexes = sorted({0, len(geometry) // 2, len(geometry) - 1})
    return tuple(
        (round(geometry[index][0], 3), round(geometry[index][1], 3))
        for index in sample_indexes
    )


def route_option_signature(option: dict) -> tuple:
    return (
        round(float(option.get("total_distance_miles", 0.0) or 0.0), 1),
        round(float(option.get("total_drive_hours", 0.0) or 0.0), 2),
        route_geometry_signature(option.get("route_geometry", [])),
    )


def dedupe_route_variants(routes: list[dict]) -> list[dict]:
    unique_routes: list[dict] = []
    seen_signatures: set[tuple] = set()

    for route in routes:
        signature = (
            round(float(route.get("distance_miles", 0.0) or 0.0), 1),
            round(float(route.get("duration_hours", 0.0) or 0.0), 2),
            route_geometry_signature(route.get("geometry", [])),
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        unique_routes.append(route)

    return unique_routes


def build_route_candidate_pairs(
    leg1_variants: list[dict],
    leg2_variants: list[dict],
    max_candidates: int,
) -> list[tuple[int, dict, dict]]:
    ranked_candidates: list[tuple[float, float, int, dict, dict]] = []

    for leg1_index, leg1_route in enumerate(leg1_variants):
        for leg2_index, leg2_route in enumerate(leg2_variants):
            ranked_candidates.append(
                (
                    float(leg1_route["duration_hours"]) + float(leg2_route["duration_hours"]),
                    float(leg1_route["distance_miles"]) + float(leg2_route["distance_miles"]),
                    leg1_index * max(1, len(leg2_variants)) + leg2_index,
                    leg1_route,
                    leg2_route,
                )
            )

    ranked_candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return [
        (candidate_index, leg1_route, leg2_route)
        for _, _, candidate_index, leg1_route, leg2_route in ranked_candidates[:max_candidates]
    ]


def select_returned_route_options(route_options: list[dict], max_returned_options: int) -> list[dict]:
    selected_options: list[dict] = []
    seen_signatures: set[tuple] = set()

    for option in route_options:
        signature = route_option_signature(option)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        selected_options.append(option)
        if len(selected_options) >= max_returned_options:
            break

    return selected_options


def _compute_trip_payload(trip_id, data: dict) -> None:
    close_old_connections()
    try:
        trip = Trip.objects.get(pk=trip_id)
        request_started_at = perf_counter()

        geocode_started_at = perf_counter()
        with ThreadPoolExecutor(max_workers=3) as executor:
            current_geo_future = executor.submit(resolve_location, data, "current_location")
            pickup_geo_future = executor.submit(resolve_location, data, "pickup_location")
            dropoff_geo_future = executor.submit(resolve_location, data, "dropoff_location")

            current_geo = current_geo_future.result()
            pickup_geo = pickup_geo_future.result()
            dropoff_geo = dropoff_geo_future.result()
        logger.info("Trip %s geocoding completed in %.2fs", trip.id, perf_counter() - geocode_started_at)

        trip.current_location_lat = current_geo["lat"]
        trip.current_location_lon = current_geo["lon"]
        trip.pickup_location_lat = pickup_geo["lat"]
        trip.pickup_location_lon = pickup_geo["lon"]
        trip.dropoff_location_lat = dropoff_geo["lat"]
        trip.dropoff_location_lon = dropoff_geo["lon"]

        routing_started_at = perf_counter()
        include_alternatives = getattr(settings, "ROUTE_INCLUDE_ALTERNATIVES", False)
        with ThreadPoolExecutor(max_workers=2) as executor:
            leg1_variants_future = executor.submit(
                get_route,
                current_geo["lon"],
                current_geo["lat"],
                pickup_geo["lon"],
                pickup_geo["lat"],
                include_alternatives,
            )
            leg2_variants_future = executor.submit(
                get_route,
                pickup_geo["lon"],
                pickup_geo["lat"],
                dropoff_geo["lon"],
                dropoff_geo["lat"],
                include_alternatives,
            )

            leg1_variants = leg1_variants_future.result()
            leg2_variants = leg2_variants_future.result()
        leg1_variants = dedupe_route_variants(normalize_route_variants(leg1_variants))
        leg2_variants = dedupe_route_variants(normalize_route_variants(leg2_variants))
        max_variants_per_leg = max(1, getattr(settings, "ROUTE_MAX_VARIANTS_PER_LEG", 3))
        max_combined_candidates = max(1, getattr(settings, "ROUTE_MAX_COMBINED_CANDIDATES", 6))
        leg1_variants = sorted(
            leg1_variants,
            key=lambda route: (
                float(route["duration_hours"]),
                float(route["distance_miles"]),
            ),
        )[:max_variants_per_leg]
        leg2_variants = sorted(
            leg2_variants,
            key=lambda route: (
                float(route["duration_hours"]),
                float(route["distance_miles"]),
            ),
        )[:max_variants_per_leg]
        route_candidate_pairs = build_route_candidate_pairs(
            leg1_variants,
            leg2_variants,
            max_candidates=max_combined_candidates,
        )
        logger.info(
            "Trip %s routing completed in %.2fs with %s x %s variant(s), %s pair(s) queued",
            trip.id,
            perf_counter() - routing_started_at,
            len(leg1_variants),
            len(leg2_variants),
            len(route_candidate_pairs),
        )

        route_options = []
        variant_errors: list[str] = []

        for variant_index, leg1_route, leg2_route in route_candidate_pairs:
            leg1_miles = leg1_route["distance_miles"]
            leg2_miles = leg2_route["distance_miles"]
            leg1_duration = leg1_route["duration_hours"]
            leg2_duration = leg2_route["duration_hours"]
            total_distance = leg1_miles + leg2_miles

            try:
                hos_started_at = perf_counter()
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
                logger.info(
                    "Trip %s HOS planning for variant %s completed in %.2fs",
                    trip.id,
                    variant_index,
                    perf_counter() - hos_started_at,
                )

                route_options.append({
                    "id": variant_index,
                    "leg1_miles": leg1_miles,
                    "leg2_miles": leg2_miles,
                    "total_distance_miles": total_distance,
                    "leg1_duration_hours": leg1_duration,
                    "leg2_duration_hours": leg2_duration,
                    "route_geometry": leg1_route["geometry"] + leg2_route["geometry"],
                    "route_instructions": [
                        *leg1_route.get("instructions", []),
                        *leg2_route.get("instructions", []),
                    ],
                    "stops": hos_result["stops"],
                    "daily_logs": hos_result["daily_logs"],
                    "total_on_duty_hours": hos_result["total_on_duty_hours"],
                    "total_drive_hours": hos_result["total_drive_hours"],
                    "hos_compliant": hos_result["hos_compliant"],
                    "weekly_hours_used": hos_result["weekly_hours_used"],
                    "weekly_hours_remaining": hos_result["weekly_hours_remaining"],
                })
            except ValueError as e:
                variant_errors.append(str(e))
                logger.warning("Variant %s violated constraints immediately: %s", variant_index, e)

        if not route_options:
            if variant_errors:
                raise ValueError(variant_errors[0])
            raise ValueError("No viable routes could be planned within limits.")

        route_options.sort(key=lambda opt: (opt["total_drive_hours"], opt["total_distance_miles"], opt["id"]))
        route_options = select_returned_route_options(
            route_options,
            max_returned_options=max(1, getattr(settings, "ROUTE_MAX_RETURNED_OPTIONS", 2)),
        )
        for idx, option in enumerate(route_options):
            option["stops"] = enrich_stop_metadata(
                option["stops"],
                option["route_geometry"],
                option["total_distance_miles"],
                option["route_instructions"],
                resolve_real_poi=False,
            )
            option["is_fastest"] = idx == 0
            option["label"] = "Fastest route" if option["is_fastest"] else "Alternative route"

        best_route = route_options[0]
        best_route["stops"] = enrich_stop_metadata(
            best_route["stops"],
            best_route["route_geometry"],
            best_route["total_distance_miles"],
            best_route["route_instructions"],
            resolve_real_poi=getattr(settings, "RESOLVE_STOP_POIS_DURING_COMPUTE", False),
        )

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
        logger.info("Trip %s fully computed in %.2fs", trip.id, perf_counter() - request_started_at)

    except ValueError as e:
        Trip.objects.filter(pk=trip_id).update(
            status=Trip.Status.FAILED,
            error_message=str(e),
        )
    except Exception as e:
        logger.exception("Unexpected error computing trip %s", trip_id)
        Trip.objects.filter(pk=trip_id).update(
            status=Trip.Status.FAILED,
            error_message=f"Internal error: {str(e)}",
        )
    finally:
        close_old_connections()


def start_trip_compute_job(trip_id, data: dict) -> None:
    thread = threading.Thread(
        target=_compute_trip_payload,
        args=(trip_id, data),
        daemon=True,
        name=f"trip-compute-{trip_id}",
    )
    thread.start()


@extend_schema(
    summary="Health check",
    description="Public health check endpoint for uptime monitoring.",
    responses={
        200: OpenApiResponse(
            response=inline_serializer(
                name="HealthCheckResponse",
                fields={
                    "status": serializers.CharField(default="ok"),
                },
            ),
            description="Service is healthy.",
        )
    },
)
@csrf_exempt
@api_view(["GET", "HEAD"])
@authentication_classes([])
@permission_classes([AllowAny])
def health_check(_request):
    """Lightweight uptime endpoint for hosting health checks."""
    return Response({"status": "ok"})


def error_payload(code: str, message: str, details=None) -> dict:
    """Consistent API error response body for frontend consumers."""
    payload = {
        "code": code,
        "message": message,
    }
    if details is not None:
        payload["details"] = details
    return payload


def find_reusable_trip(data: dict) -> Trip | None:
    """
    Reuse a recent identical trip instead of recalculating the route plan.
    This avoids duplicate routing/geocoding work for repeated submissions.
    """
    filters = {
        "current_location": data["current_location"],
        "pickup_location": data["pickup_location"],
        "dropoff_location": data["dropoff_location"],
        "current_cycle_used": data["current_cycle_used"],
    }

    for field in (
        "current_location_lat",
        "current_location_lon",
        "pickup_location_lat",
        "pickup_location_lon",
        "dropoff_location_lat",
        "dropoff_location_lon",
    ):
        value = data.get(field)
        if value is not None:
            filters[field] = value

    return (
        Trip.objects.filter(
            status__in=[Trip.Status.COMPUTING, Trip.Status.COMPUTED],
            **filters,
        )
        .order_by("-updated_at")
        .first()
    )


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
            clamped_ratio = max(0.0, min(1.0, progress_ratio))
            interpolated = interpolate_route_position(route_geometry, progress_ratio)
            if interpolated is not None:
                enriched["lat"], enriched["lon"] = interpolated
            elif route_geometry:
                # Fallback: sample along polyline so map markers still render
                idx = int(round(clamped_ratio * (len(route_geometry) - 1)))
                idx = max(0, min(len(route_geometry) - 1, idx))
                pt = route_geometry[idx]
                enriched["lat"], enriched["lon"] = pt[0], pt[1]
        enriched_stops.append(enriched)

    return enriched_stops


def describe_progress_stop(stop: dict, total_distance_miles: float, route_instructions: list[dict]) -> dict:
    enriched = dict(stop)
    progress_miles = float(enriched.get("progress_miles", 0.0) or 0.0)
    rounded_progress = int(round(progress_miles))

    nearest_instruction = None
    if route_instructions:
        nearest_instruction = min(
            route_instructions,
            key=lambda instruction: abs(
                float(instruction.get("cumulative_distance_miles", 0.0) or 0.0) - progress_miles
            ),
        )

    road_name = ""
    if nearest_instruction:
        road_name = str(nearest_instruction.get("road_name") or "").strip()

    if enriched.get("location", "").startswith("En route"):
        if road_name:
            enriched["location"] = f"Approx. near {road_name}"
        elif total_distance_miles > 0:
            enriched["location"] = f"Approx. mile {rounded_progress} of {int(round(total_distance_miles))}"

    stop_type = enriched.get("type")
    if stop_type == "FUEL":
        enriched["description"] = (
            f"{enriched.get('description', 'Fuel stop')} around mile {rounded_progress}"
            + (f" near {road_name}" if road_name else "")
        )
    elif stop_type == "REST":
        enriched["description"] = (
            f"{enriched.get('description', 'Mandatory rest')} beginning around mile {rounded_progress}"
            + (f" near {road_name}" if road_name else "")
        )
    elif stop_type == "BREAK":
        enriched["description"] = (
            f"{enriched.get('description', '30-min break')} taken around mile {rounded_progress}"
            + (f" near {road_name}" if road_name else "")
        )

    return enriched


def attach_real_stop_poi(stop: dict) -> dict:
    enriched = dict(stop)
    if enriched.get("type") not in {"FUEL", "REST", "BREAK"}:
        return enriched

    lat = float(enriched.get("lat", 0.0) or 0.0)
    lon = float(enriched.get("lon", 0.0) or 0.0)
    poi = find_nearby_stop_poi(lat, lon, enriched.get("type", ""))
    if not poi:
        return enriched

    enriched["lat"] = poi["lat"]
    enriched["lon"] = poi["lon"]
    enriched["location"] = poi["name"]

    distance_suffix = f" ({poi['distance_miles']} mi away)" if poi.get("distance_miles") is not None else ""
    if enriched.get("type") == "FUEL":
        enriched["description"] = f"Fuel stop at {poi['name']}{distance_suffix}"
    elif enriched.get("type") == "REST":
        enriched["description"] = f"Mandatory 10-hr rest near {poi['name']}{distance_suffix}"
    elif enriched.get("type") == "BREAK":
        enriched["description"] = f"30-min break near {poi['name']}{distance_suffix}"

    return enriched


def enrich_stop_metadata(
    stops: list[dict],
    route_geometry: list[list[float]],
    total_distance_miles: float,
    route_instructions: list[dict],
    resolve_real_poi: bool = True,
) -> list[dict]:
    positioned_stops = attach_stop_coordinates(stops, route_geometry, total_distance_miles)
    enriched_stops = [
        describe_progress_stop(stop, total_distance_miles, route_instructions)
        for stop in positioned_stops
    ]
    if not resolve_real_poi:
        return enriched_stops
    return [attach_real_stop_poi(stop) for stop in enriched_stops]


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
    pagination_class = TripHistoryPagination

    def get_queryset(self):
        queryset = Trip.objects.all()
        if self.action == "list":
            return queryset.defer("route_geometry", "route_options", "route_instructions")
        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return TripCreateSerializer
        if self.action == "list":
            return TripListSerializer
        return TripDetailSerializer

    def get_throttles(self):
        # Throttling is temporarily disabled for development/demo use.
        # if self.action == "create":
        #     throttle_classes = [TripWriteAnonThrottle]
        # elif self.action in {"list", "retrieve"}:
        #     throttle_classes = [TripReadAnonThrottle]
        # else:
        #     throttle_classes = []
        # return [throttle() for throttle in throttle_classes]
        return []

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
        reusable_trip = find_reusable_trip(data)
        if reusable_trip is not None:
            output_serializer = TripDetailSerializer(reusable_trip)
            response_status = (
                status.HTTP_202_ACCEPTED
                if reusable_trip.status == Trip.Status.COMPUTING
                else status.HTTP_200_OK
            )
            return Response(output_serializer.data, status=response_status)

        # Create trip in COMPUTING state
        trip = Trip.objects.create(
            current_location=data["current_location"],
            pickup_location=data["pickup_location"],
            dropoff_location=data["dropoff_location"],
            current_cycle_used=data["current_cycle_used"],
            status=Trip.Status.COMPUTING,
        )

        if getattr(settings, "TRIP_COMPUTE_ASYNC", True):
            payload_data = dict(data)
            transaction.on_commit(lambda: start_trip_compute_job(trip.id, payload_data))
            output_serializer = TripDetailSerializer(trip)
            return Response(output_serializer.data, status=status.HTTP_202_ACCEPTED)

        try:
            _compute_trip_payload(trip.id, dict(data))
            trip.refresh_from_db()
            output_serializer = TripDetailSerializer(trip)
            if trip.status == Trip.Status.FAILED:
                return Response(
                    {
                        **error_payload(
                            code="trip_planning_failed",
                            message=trip.error_message,
                        ),
                        "trip_id": str(trip.id),
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            return Response(output_serializer.data, status=status.HTTP_201_CREATED)
        except Exception:
            trip.refresh_from_db()
            return Response(
                error_payload(
                    code="internal_error",
                    message="An unexpected error occurred while computing the trip. Please try again.",
                ),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
