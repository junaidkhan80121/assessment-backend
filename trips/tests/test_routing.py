from unittest.mock import Mock, patch

import pytest
import requests

from trips.routing import geocode_location


def _response(payload):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


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
