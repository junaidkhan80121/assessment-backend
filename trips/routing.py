"""
OpenRouteService integration for route geocoding and directions.
"""
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

ORS_BASE_URL = "https://api.openrouteservice.org"


def geocode_location(query: str) -> dict:
    """
    Geocode a location string to lat/lon using OpenRouteService.
    Returns {"lat": float, "lon": float, "label": str} or raises ValueError.
    """
    api_key = getattr(settings, "ORS_API_KEY", "")
    if not api_key:
        # Fallback: use a simple estimation for development
        return _fallback_geocode(query)

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

        if not data.get("features"):
            raise ValueError(f"Could not geocode location: {query}")

        coords = data["features"][0]["geometry"]["coordinates"]
        label = data["features"][0]["properties"].get("label", query)
        return {"lat": coords[1], "lon": coords[0], "label": label}

    except requests.RequestException as e:
        logger.warning("ORS geocode failed for %s: %s", query, e)
        return _fallback_geocode(query)


def get_route(
    start_lon: float, start_lat: float,
    end_lon: float, end_lat: float,
    alternatives: bool = False,
) -> dict | list[dict]:
    """
    Get driving route between two points using OpenRouteService.
    If alternatives=False, returns dict.
    If alternatives=True, returns list of dicts.
    Dict format: {"distance_miles": float, "duration_hours": float, "geometry": list}.
    """
    api_key = getattr(settings, "ORS_API_KEY", "")
    if not api_key:
        return _fallback_route(start_lat, start_lon, end_lat, end_lon, alternatives)

    try:
        payload: dict = {
            "coordinates": [
                [start_lon, start_lat],
                [end_lon, end_lat],
            ],
            # Ask ORS to return GeoJSON geometry so we can
            # directly extract coordinate arrays for the map.
            "geometry_format": "geojson",
            "geometry_simplify": "false",
            "instructions": "false",
        }
        if alternatives:
            payload["alternative_routes"] = {
                "share_factor": 0.6,
                "target_count": 3
            }

        resp = requests.post(
            f"{ORS_BASE_URL}/v2/directions/driving-hgv",
            json=payload,
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for route in data.get("routes", []):
            distance_meters = route["summary"]["distance"]
            duration_seconds = route["summary"]["duration"]

            # Decode geometry (GeoJSON format)
            geometry = route.get("geometry", {})
            if isinstance(geometry, dict):
                coords = geometry.get("coordinates", [])
            else:
                coords = []

            results.append({
                "distance_miles": round(distance_meters / 1609.34, 2),
                "duration_hours": round(duration_seconds / 3600, 2),
                "geometry": [[c[1], c[0]] for c in coords],  # [lat, lon] format
            })

        if not results:
            return [] if alternatives else _fallback_route(start_lat, start_lon, end_lat, end_lon, alternatives)
            
        return results if alternatives else results[0]

    except requests.RequestException as e:
        logger.warning("ORS route failed: %s", e)
        return _fallback_route(start_lat, start_lon, end_lat, end_lon, alternatives)


def _fallback_geocode(query: str) -> dict:
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

    # Default to geographic center of US
    return {"lat": 39.8283, "lon": -98.5795, "label": query}


def _fallback_route(
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    alternatives: bool = False,
) -> dict | list[dict]:
    """
    Simple fallback route calculation using Haversine distance.
    Used when ORS API key is not configured.
    """
    import math

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

    route_1 = {
        "distance_miles": round(road_distance, 2),
        "duration_hours": round(duration_hours, 2),
        "geometry": geometry,
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
    }
    
    route_3 = {
        "distance_miles": round(road_distance * 1.15, 2),
        "duration_hours": round(duration_hours * 1.05, 2),
        "geometry": [
            [start_lat, start_lon],
            [start_lat + (dlat * 0.5) - 0.2, start_lon + (dlon * 0.5) + 0.1],
            [end_lat, end_lon],
        ],
    }

    return [route_1, route_2, route_3]
