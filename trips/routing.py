"""
Routing utilities: Mapbox Directions for routing, with OpenRouteService
geocoding as an optional fallback and simple math-based fallbacks for
local development without API keys.
"""
import math
import logging
import hashlib
import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def _cache_key(prefix: str, *parts: object) -> str:
    serialized = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"trips:{prefix}:{digest}"


def _decode_polyline(encoded: str) -> list[list[float]]:
    """
    Decode a Google-encoded polyline string into a list of [lat, lon] pairs.
    ORS v2 returns geometry as an encoded polyline with precision 5 by default.
    """
    coords = []
    index = 0
    length = len(encoded)
    lat = 0
    lng = 0

    while index < length:
        # Decode latitude
        result = 0
        shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        # Decode longitude
        result = 0
        shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coords.append([lat / 1e5, lng / 1e5])

    return coords

ORS_BASE_URL = "https://api.openrouteservice.org"


def geocode_location(query: str) -> dict:
    """
    Geocode a location string to lat/lon using a provider chain.
    Returns {"lat": float, "lon": float, "label": str} or raises ValueError.
    """
    normalized_query = query.strip()
    cache_key = _cache_key("geocode", normalized_query.lower())
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    api_key = getattr(settings, "ORS_API_KEY", "")
    if api_key:
        try:
            resp = requests.get(
                f"{ORS_BASE_URL}/geocode/search",
                params={
                    "api_key": api_key,
                    "text": query,
                    "size": 1,
                    "boundary.country": "US",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("features"):
                coords = data["features"][0]["geometry"]["coordinates"]
                label = data["features"][0]["properties"].get("label", query)
                result = {"lat": coords[1], "lon": coords[0], "label": label}
                cache.set(cache_key, result, timeout=getattr(settings, "GEOCODE_CACHE_TIMEOUT", 86400))
                return result
        except requests.RequestException as e:
            logger.warning("ORS geocode failed for %s: %s", query, e)

    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "format": "jsonv2",
                "q": query,
                "countrycodes": "us",
                "limit": 1,
            },
            headers={
                "User-Agent": "eld-trip-planner/1.0",
                "Accept": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data:
            top_result = data[0]
            result = {
                "lat": float(top_result["lat"]),
                "lon": float(top_result["lon"]),
                "label": top_result.get("display_name", query),
            }
            cache.set(cache_key, result, timeout=getattr(settings, "GEOCODE_CACHE_TIMEOUT", 86400))
            return result
    except requests.RequestException as e:
        logger.warning("Nominatim geocode failed for %s: %s", query, e)

    fallback = _fallback_geocode(query)
    if fallback is not None:
        cache.set(cache_key, fallback, timeout=getattr(settings, "GEOCODE_CACHE_TIMEOUT", 86400))
        return fallback

    raise ValueError(f"Could not geocode location: {query}")


def get_route(
    start_lon: float, start_lat: float,
    end_lon: float, end_lat: float,
    alternatives: bool = False,
) -> dict | list[dict]:
    """
    Get driving route between two points using Mapbox Directions API.
    If alternatives=False, returns dict.
    If alternatives=True, returns list of dicts.
    Dict format:
    {
        "distance_miles": float,
        "duration_hours": float,
        "geometry": list,
        "instructions": list,
    }.
    """
    cache_key = _cache_key(
        "route",
        round(start_lon, 5),
        round(start_lat, 5),
        round(end_lon, 5),
        round(end_lat, 5),
        alternatives,
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    access_token = getattr(settings, "MAPBOX_ACCESS_TOKEN", "")

    if access_token:
        try:
            result = _get_mapbox_route(
                start_lon=start_lon,
                start_lat=start_lat,
                end_lon=end_lon,
                end_lat=end_lat,
                alternatives=alternatives,
            )
            cache.set(cache_key, result, timeout=getattr(settings, "ROUTE_CACHE_TIMEOUT", 21600))
            return result
        except (requests.RequestException, ValueError) as e:
            logger.warning("Mapbox route failed, falling back to ORS: %s", e)

    ors_api_key = getattr(settings, "ORS_API_KEY", "")
    if ors_api_key:
        try:
            result = _get_ors_route(
                start_lon=start_lon,
                start_lat=start_lat,
                end_lon=end_lon,
                end_lat=end_lat,
                alternatives=alternatives,
            )
            cache.set(cache_key, result, timeout=getattr(settings, "ROUTE_CACHE_TIMEOUT", 21600))
            return result
        except (requests.RequestException, ValueError) as e:
            logger.warning("ORS route failed, falling back to local estimate: %s", e)

    result = _fallback_route(start_lat, start_lon, end_lat, end_lon, alternatives)
    cache.set(cache_key, result, timeout=getattr(settings, "ROUTE_CACHE_TIMEOUT", 21600))
    return result


def _get_mapbox_route(
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
    alternatives: bool,
) -> dict | list[dict]:
    coordinates = f"{start_lon},{start_lat};{end_lon},{end_lat}"
    url = f"https://api.mapbox.com/directions/v5/mapbox/driving-traffic/{coordinates}"

    params: dict = {
        "access_token": getattr(settings, "MAPBOX_ACCESS_TOKEN", ""),
        "geometries": "geojson",
        "overview": "full",
        "steps": "true",
        "alternatives": "true" if alternatives else "false",
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    routes = data.get("routes", [])
    if not routes:
        raise ValueError("Mapbox Directions returned no routes for the given coordinates.")

    results = []
    for route in routes:
        geometry = route.get("geometry", {})
        raw_coords = geometry.get("coordinates", []) if isinstance(geometry, dict) else []
        results.append(
            {
                "distance_miles": round(route.get("distance", 0.0) / 1609.34, 2),
                "duration_hours": round(route.get("duration", 0.0) / 3600, 2),
                "geometry": [[c[1], c[0]] for c in raw_coords],
                "instructions": _mapbox_instructions(route),
            }
        )

    return results if alternatives else results[0]


def _get_ors_route(
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
    alternatives: bool,
) -> dict | list[dict]:
    headers = {
        "Authorization": getattr(settings, "ORS_API_KEY", ""),
        "Content-Type": "application/json",
    }
    body = {
        "coordinates": [[start_lon, start_lat], [end_lon, end_lat]],
        "instructions": True,
        "instructions_format": "text",
        "geometry": True,
        "elevation": False,
        "alternative_routes": {
            "target_count": 3 if alternatives else 1,
            "weight_factor": 1.4,
            "share_factor": 0.6,
        } if alternatives else None,
    }
    if not alternatives:
        body.pop("alternative_routes")

    resp = requests.post(
        f"{ORS_BASE_URL}/v2/directions/driving-hgv/geojson",
        json=body,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    features = data.get("features", [])
    if not features:
        raise ValueError("OpenRouteService returned no routes for the given coordinates.")

    results = []
    for feature in features:
        props = feature.get("properties", {}) or {}
        summary = props.get("summary", {}) or {}
        geometry = feature.get("geometry", {}) or {}
        raw_coords = geometry.get("coordinates", []) if geometry.get("type") == "LineString" else []
        segments = props.get("segments", []) or []
        results.append(
            {
                "distance_miles": round(summary.get("distance", 0.0) / 1609.34, 2),
                "duration_hours": round(summary.get("duration", 0.0) / 3600, 2),
                "geometry": [[c[1], c[0]] for c in raw_coords],
                "instructions": _ors_instructions(segments, raw_coords),
            }
        )

    return results if alternatives else results[0]


def _mapbox_instructions(route: dict) -> list[dict]:
    instructions: list[dict] = []
    cumulative_distance = 0.0
    cumulative_duration = 0.0

    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            maneuver = step.get("maneuver", {})
            location = maneuver.get("location", [])
            step_distance_miles = round(step.get("distance", 0.0) / 1609.34, 2)
            step_duration_hours = round(step.get("duration", 0.0) / 3600, 2)
            cumulative_distance += step_distance_miles
            cumulative_duration += step_duration_hours
            instructions.append(
                {
                    "text": maneuver.get("instruction") or "Continue on the route",
                    "distance_miles": step_distance_miles,
                    "duration_hours": step_duration_hours,
                    "road_name": step.get("name", ""),
                    "maneuver_type": maneuver.get("type", "continue"),
                    "maneuver_modifier": maneuver.get("modifier", ""),
                    "location": {
                        "lat": location[1] if len(location) > 1 else None,
                        "lon": location[0] if len(location) > 1 else None,
                    },
                    "cumulative_distance_miles": round(cumulative_distance, 2),
                    "cumulative_duration_hours": round(cumulative_duration, 2),
                }
            )

    return instructions


def _ors_instructions(segments: list[dict], raw_coords: list[list[float]]) -> list[dict]:
    instructions: list[dict] = []
    cumulative_distance = 0.0
    cumulative_duration = 0.0

    for segment in segments:
        for step in segment.get("steps", []):
            step_distance_miles = round(step.get("distance", 0.0) / 1609.34, 2)
            step_duration_hours = round(step.get("duration", 0.0) / 3600, 2)
            cumulative_distance += step_distance_miles
            cumulative_duration += step_duration_hours
            waypoint_index = step.get("way_points", [None])[0]
            coord = raw_coords[waypoint_index] if isinstance(waypoint_index, int) and 0 <= waypoint_index < len(raw_coords) else [None, None]
            instructions.append(
                {
                    "text": step.get("instruction") or "Continue on the route",
                    "distance_miles": step_distance_miles,
                    "duration_hours": step_duration_hours,
                    "road_name": step.get("name", ""),
                    "maneuver_type": str(step.get("type", "continue")),
                    "maneuver_modifier": "",
                    "location": {
                        "lat": coord[1] if len(coord) > 1 else None,
                        "lon": coord[0] if len(coord) > 1 else None,
                    },
                    "cumulative_distance_miles": round(cumulative_distance, 2),
                    "cumulative_duration_hours": round(cumulative_duration, 2),
                }
            )

    return instructions


def _fallback_geocode(query: str) -> dict | None:
    """
    Simple fallback geocoding for known US cities.
    Used when ORS API key is not configured.
    """
    KNOWN_CITIES = {
        "new york": {"lat": 40.7128, "lon": -74.0060},
        "los angeles": {"lat": 34.0522, "lon": -118.2437},
        "chicago": {"lat": 41.8781, "lon": -87.6298},
        "houston": {"lat": 29.7604, "lon": -95.3698},
        "dallas": {"lat": 32.7767, "lon": -96.7970},
        "san francisco": {"lat": 37.7749, "lon": -122.4194},
        "denver": {"lat": 39.7392, "lon": -104.9903},
        "atlanta": {"lat": 33.7490, "lon": -84.3880},
        "miami": {"lat": 25.7617, "lon": -80.1918},
        "seattle": {"lat": 47.6062, "lon": -122.3321},
        "portland": {"lat": 45.5155, "lon": -122.6789},
        "boston": {"lat": 42.3601, "lon": -71.0589},
        "phoenix": {"lat": 33.4484, "lon": -112.0740},
        "detroit": {"lat": 42.3314, "lon": -83.0458},
        "minneapolis": {"lat": 44.9778, "lon": -93.2650},
        "st. louis": {"lat": 38.6270, "lon": -90.1994},
        "kansas city": {"lat": 39.0997, "lon": -94.5786},
        "memphis": {"lat": 35.1495, "lon": -90.0490},
        "nashville": {"lat": 36.1627, "lon": -86.7816},
        "salt lake city": {"lat": 40.7608, "lon": -111.8910},
        "omaha": {"lat": 41.2565, "lon": -95.9345},
        "indianapolis": {"lat": 39.7684, "lon": -86.1581},
        "charlotte": {"lat": 35.2271, "lon": -80.8431},
        "milwaukee": {"lat": 43.0389, "lon": -87.9065},
        "las vegas": {"lat": 36.1699, "lon": -115.1398},
        "san antonio": {"lat": 29.4241, "lon": -98.4936},
        "columbus": {"lat": 39.9612, "lon": -82.9988},
        "cleveland": {"lat": 41.4993, "lon": -81.6944},
        "pittsburgh": {"lat": 40.4406, "lon": -79.9959},
        "cincinnati": {"lat": 39.1031, "lon": -84.5120},
        "orlando": {"lat": 28.5383, "lon": -81.3792},
        "tampa": {"lat": 27.9506, "lon": -82.4572},
    }

    query_lower = query.lower().strip()
    for city_name, coords in KNOWN_CITIES.items():
        if city_name in query_lower:
            return {"lat": coords["lat"], "lon": coords["lon"], "label": query}

    return None


def _fallback_route(
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    alternatives: bool = False,
) -> dict | list[dict]:
    """
    Simple fallback route calculation using Haversine distance.
    Used when ORS API key is not configured.
    """
    R = 3958.8  # Earth radius in miles

    lat1, lon1 = math.radians(start_lat), math.radians(start_lon)
    lat2, lon2 = math.radians(end_lat), math.radians(end_lon)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    straight_line = R * c
    road_distance = straight_line * 1.3  # Factor for road vs straight line

    avg_speed = 55.0  # mph average for truck
    duration_hours = road_distance / avg_speed

    # Generate simple geometry (just start and end for fallback)
    geometry = [
        [start_lat, start_lon],
        [end_lat, end_lon],
    ]

    first_leg_distance = round(road_distance * 0.45, 2)
    second_leg_distance = round(road_distance * 0.4, 2)
    final_leg_distance = round(max(road_distance - first_leg_distance - second_leg_distance, 0.01), 2)
    first_leg_duration = round(duration_hours * 0.45, 2)
    second_leg_duration = round(duration_hours * 0.4, 2)
    final_leg_duration = round(max(duration_hours - first_leg_duration - second_leg_duration, 0.01), 2)

    fallback_instructions = [
        {
            "text": "Depart from the current location and follow the planned highway route.",
            "distance_miles": first_leg_distance,
            "duration_hours": first_leg_duration,
            "road_name": "",
            "maneuver_type": "depart",
            "maneuver_modifier": "",
            "location": {"lat": start_lat, "lon": start_lon},
            "cumulative_distance_miles": first_leg_distance,
            "cumulative_duration_hours": first_leg_duration,
        },
        {
            "text": "Continue along the main route toward the destination.",
            "distance_miles": second_leg_distance,
            "duration_hours": second_leg_duration,
            "road_name": "",
            "maneuver_type": "continue",
            "maneuver_modifier": "",
            "location": {
                "lat": round((start_lat + end_lat) / 2, 6),
                "lon": round((start_lon + end_lon) / 2, 6),
            },
            "cumulative_distance_miles": round(first_leg_distance + second_leg_distance, 2),
            "cumulative_duration_hours": round(first_leg_duration + second_leg_duration, 2),
        },
        {
            "text": "Arrive at the destination.",
            "distance_miles": final_leg_distance,
            "duration_hours": final_leg_duration,
            "road_name": "",
            "maneuver_type": "arrive",
            "maneuver_modifier": "",
            "location": {"lat": end_lat, "lon": end_lon},
            "cumulative_distance_miles": round(road_distance, 2),
            "cumulative_duration_hours": round(duration_hours, 2),
        },
    ]

    route_1 = {
        "distance_miles": round(road_distance, 2),
        "duration_hours": round(duration_hours, 2),
        "geometry": geometry,
        "instructions": fallback_instructions,
    }
    
    if not alternatives:
        return route_1
        
    route_2 = {
        "distance_miles": round(road_distance * 1.1, 2),
        "duration_hours": round(duration_hours * 1.2, 2),
        "geometry": [
            [start_lat, start_lon],
            [start_lat + (dlat * 0.5) + 0.1, start_lon + (dlon * 0.5) - 0.1],
            [end_lat, end_lon],
        ],
        "instructions": fallback_instructions,
    }
    
    route_3 = {
        "distance_miles": round(road_distance * 1.15, 2),
        "duration_hours": round(duration_hours * 1.05, 2),
        "geometry": [
            [start_lat, start_lon],
            [start_lat + (dlat * 0.5) - 0.2, start_lon + (dlon * 0.5) + 0.1],
            [end_lat, end_lon],
        ],
        "instructions": fallback_instructions,
    }

    return [route_1, route_2, route_3]
