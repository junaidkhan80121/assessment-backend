from unittest.mock import Mock, patch

import pytest
import requests
from django.core.cache import cache

from trips.routing import geocode_location, get_route, find_nearby_stop_poi


def _response(payload):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


@pytest.fixture(autouse=True)
def clear_trip_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
@patch("trips.routing.requests.get")
def test_geocode_location_uses_nominatim_when_ors_is_unavailable(mock_get, settings):
    settings.ORS_API_KEY = "ors-key"
    mock_get.side_effect = [
        requests.RequestException("ors down"),
        _response(
            [
                {
                    "lat": "41.8781",
                    "lon": "-87.6298",
                    "display_name": "Chicago, Illinois, United States",
                }
            ]
        ),
    ]

    result = geocode_location("Chicago, IL")

    assert result == {
        "lat": 41.8781,
        "lon": -87.6298,
        "label": "Chicago, Illinois, United States",
    }


@pytest.mark.django_db
@patch("trips.routing.requests.get")
def test_geocode_location_raises_for_unknown_location(mock_get, settings):
    settings.ORS_API_KEY = ""
    mock_get.return_value = _response([])

    with pytest.raises(ValueError, match="Could not geocode location"):
        geocode_location("Definitely Not A Real Supported Place 12345")


@pytest.mark.django_db
@patch("trips.routing.requests.get")
def test_geocode_location_uses_known_city_fallback_before_failing(mock_get, settings):
    settings.ORS_API_KEY = ""
    mock_get.side_effect = requests.RequestException("nominatim down")

    result = geocode_location("Chicago, IL")

    assert result["lat"] == 41.8781
    assert result["lon"] == -87.6298


@pytest.mark.django_db
@patch("trips.routing.requests.get")
def test_geocode_location_uses_cache_for_repeat_queries(mock_get, settings):
    settings.ORS_API_KEY = ""
    mock_get.return_value = _response(
        [
            {
                "lat": "41.8781",
                "lon": "-87.6298",
                "display_name": "Chicago, Illinois, United States",
            }
        ]
    )

    first = geocode_location("Chicago, IL")
    second = geocode_location("Chicago, IL")

    assert first == second
    assert mock_get.call_count == 1


@pytest.mark.django_db
@patch("trips.routing._get_mapbox_route")
def test_get_route_uses_cache_for_repeat_queries(mock_mapbox_route, settings):
    settings.MAPBOX_ACCESS_TOKEN = "mapbox-token"
    mock_mapbox_route.return_value = {
        "distance_miles": 100.0,
        "duration_hours": 2.0,
        "geometry": [[41.0, -87.0], [40.0, -86.0]],
        "instructions": [],
    }

    first = get_route(-87.0, 41.0, -86.0, 40.0, alternatives=False)
    second = get_route(-87.0, 41.0, -86.0, 40.0, alternatives=False)

    assert first == second
    assert mock_mapbox_route.call_count == 1


@pytest.mark.django_db
@patch("trips.routing.requests.post")
def test_find_nearby_stop_poi_returns_nearest_real_facility(mock_post):
    mock_post.return_value = _response(
        {
            "elements": [
                {
                    "type": "node",
                    "lat": 39.801,
                    "lon": -86.145,
                    "tags": {"name": "Pilot Travel Center", "amenity": "fuel"},
                },
                {
                    "type": "node",
                    "lat": 39.91,
                    "lon": -86.20,
                    "tags": {"name": "Far Fuel", "amenity": "fuel"},
                },
            ]
        }
    )

    poi = find_nearby_stop_poi(39.8005, -86.1448, "FUEL")

    assert poi is not None
    assert poi["name"] == "Pilot Travel Center"
    assert poi["category"] == "Fuel station"


@pytest.mark.django_db
@patch("trips.routing.requests.post")
def test_find_nearby_stop_poi_returns_none_when_lookup_fails(mock_post):
    mock_post.side_effect = requests.RequestException("overpass down")

    poi = find_nearby_stop_poi(39.8, -86.14, "REST")

    assert poi is None
