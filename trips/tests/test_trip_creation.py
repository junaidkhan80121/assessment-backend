from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient


@pytest.mark.django_db
@override_settings(TRIP_COMPUTE_ASYNC=False, ROUTE_INCLUDE_ALTERNATIVES=True)
def test_create_trip_reuses_client_coordinates_and_returns_alternatives():
    client = APIClient()

    payload = {
        "current_location": "Chicago, IL",
        "current_location_lat": 41.8781,
        "current_location_lon": -87.6298,
        "pickup_location": "Indianapolis, IN",
        "pickup_location_lat": 39.7684,
        "pickup_location_lon": -86.1581,
        "dropoff_location": "Nashville, TN",
        "dropoff_location_lat": 36.1627,
        "dropoff_location_lon": -86.7816,
        "current_cycle_used": 12.0,
    }

    def route_stub(*args, **kwargs):
        # views calls get_route(lon1, lat1, lon2, lat2, alternatives) positionally
        alternatives = bool(args[4]) if len(args) >= 5 else bool(kwargs.get("alternatives", False))
        start_lat = args[1] if len(args) >= 2 else kwargs["start_lat"]
        end_lat = args[3] if len(args) >= 4 else kwargs["end_lat"]
        if alternatives:
            if start_lat == 41.8781 and end_lat == 39.7684:
                return [
                    {
                        "distance_miles": 180.0,
                        "duration_hours": 3.2,
                        "geometry": [[41.8781, -87.6298], [39.7684, -86.1581]],
                        "instructions": [
                            {"text": "Leave Chicago", "distance_miles": 180.0, "duration_hours": 3.2, "road_name": "I-90", "maneuver_type": "depart", "maneuver_modifier": "", "location": {"lat": 41.8781, "lon": -87.6298}, "cumulative_distance_miles": 180.0, "cumulative_duration_hours": 3.2},
                        ],
                    },
                    {
                        "distance_miles": 210.0,
                        "duration_hours": 3.8,
                        "geometry": [[41.8781, -87.6298], [40.8, -86.9], [39.7684, -86.1581]],
                        "instructions": [
                            {"text": "Take the alternate pickup route", "distance_miles": 210.0, "duration_hours": 3.8, "road_name": "I-94 Alt", "maneuver_type": "continue", "maneuver_modifier": "", "location": {"lat": 41.8781, "lon": -87.6298}, "cumulative_distance_miles": 210.0, "cumulative_duration_hours": 3.8},
                        ],
                    },
                ]

            return [
                {
                    "distance_miles": 300.0,
                    "duration_hours": 5.5,
                    "geometry": [[39.7684, -86.1581], [36.1627, -86.7816]],
                    "instructions": [
                        {"text": "Head south", "distance_miles": 300.0, "duration_hours": 5.5, "road_name": "I-65", "maneuver_type": "continue", "maneuver_modifier": "", "location": {"lat": 39.7684, "lon": -86.1581}, "cumulative_distance_miles": 300.0, "cumulative_duration_hours": 5.5},
                    ],
                },
                {
                    "distance_miles": 330.0,
                    "duration_hours": 5.9,
                    "geometry": [[39.7684, -86.1581], [36.45, -87.1], [36.1627, -86.7816]],
                    "instructions": [
                        {"text": "Take the alternate route", "distance_miles": 330.0, "duration_hours": 5.9, "road_name": "I-65 Alt", "maneuver_type": "continue", "maneuver_modifier": "", "location": {"lat": 39.7684, "lon": -86.1581}, "cumulative_distance_miles": 330.0, "cumulative_duration_hours": 5.9},
                    ],
                },
            ]

        return {
            "distance_miles": 180.0,
            "duration_hours": 3.2,
            "geometry": [[41.8781, -87.6298], [39.7684, -86.1581]],
            "instructions": [
                {"text": "Leave Chicago", "distance_miles": 180.0, "duration_hours": 3.2, "road_name": "I-90", "maneuver_type": "depart", "maneuver_modifier": "", "location": {"lat": 41.8781, "lon": -87.6298}, "cumulative_distance_miles": 180.0, "cumulative_duration_hours": 3.2},
            ],
        }

    def plan_trip_stub(**kwargs):
        return {
            "stops": [
                {
                    "type": "CURRENT",
                    "location": kwargs["current_location"],
                    "lat": kwargs["current_lat"],
                    "lon": kwargs["current_lon"],
                    "arrival_hour": 0,
                    "duration_minutes": 0,
                    "description": "Start",
                },
                {
                    "type": "PICKUP",
                    "location": kwargs["pickup_location"],
                    "lat": kwargs["pickup_lat"],
                    "lon": kwargs["pickup_lon"],
                    "arrival_hour": 3.2,
                    "duration_minutes": 60,
                    "description": "Pickup",
                },
                {
                    "type": "DROPOFF",
                    "location": kwargs["dropoff_location"],
                    "lat": kwargs["dropoff_lat"],
                    "lon": kwargs["dropoff_lon"],
                    "arrival_hour": 8.7,
                    "duration_minutes": 60,
                    "description": "Dropoff",
                },
            ],
            "daily_logs": [
                {
                    "date": "2026-03-18",
                    "day_number": 1,
                    "duty_entries": [],
                    "totals": {
                        "OFF_DUTY": 10,
                        "SLEEPER": 0,
                        "DRIVING": 8,
                        "ON_DUTY_NOT_DRIVING": 6,
                    },
                    "total_miles_driving_today": kwargs["total_distance_miles"],
                    "remarks": [],
                    "recap": {
                        "on_duty_today": 14,
                        "on_duty_last_8_days": 26,
                        "available_tomorrow": 44,
                        "hours_warning": False,
                        "hours_critical": False,
                    },
                }
            ],
            "total_on_duty_hours": 14.0,
            "total_drive_hours": round(kwargs["leg1_duration_hours"] + kwargs["leg2_duration_hours"], 2),
            "hos_compliant": True,
            "weekly_hours_used": 26.0,
            "weekly_hours_remaining": 44.0,
        }

    with patch("trips.views.geocode_location") as geocode_mock, patch(
        "trips.views.get_route", side_effect=route_stub
    ), patch("trips.views.plan_trip", side_effect=plan_trip_stub):
        response = client.post("/api/trips/", payload, format="json")

    assert response.status_code == 201
    geocode_mock.assert_not_called()

    data = response.json()
    assert len(data["route_options"]) == 4
    assert any(option["is_fastest"] for option in data["route_options"])
    assert len(data["route_instructions"]) == 2


@pytest.mark.django_db
@override_settings(TRIP_COMPUTE_ASYNC=False)
def test_create_trip_defaults_to_single_route_option_for_faster_compute():
    client = APIClient()

    payload = {
        "current_location": "Chicago, IL",
        "current_location_lat": 41.8781,
        "current_location_lon": -87.6298,
        "pickup_location": "Indianapolis, IN",
        "pickup_location_lat": 39.7684,
        "pickup_location_lon": -86.1581,
        "dropoff_location": "Nashville, TN",
        "dropoff_location_lat": 36.1627,
        "dropoff_location_lon": -86.7816,
        "current_cycle_used": 12.0,
    }

    def route_stub(*args, **kwargs):
        alternatives = bool(args[4]) if len(args) >= 5 else bool(kwargs.get("alternatives", False))
        route = {
            "distance_miles": 180.0,
            "duration_hours": 3.2,
            "geometry": [[41.8781, -87.6298], [39.7684, -86.1581]],
            "instructions": [],
        }
        if alternatives:
            raise AssertionError("Alternative routes should be disabled by default")
        return route

    def plan_trip_stub(**kwargs):
        return {
            "stops": [],
            "daily_logs": [],
            "total_on_duty_hours": 14.0,
            "total_drive_hours": round(kwargs["leg1_duration_hours"] + kwargs["leg2_duration_hours"], 2),
            "hos_compliant": True,
            "weekly_hours_used": 26.0,
            "weekly_hours_remaining": 44.0,
        }

    with patch("trips.views.geocode_location") as geocode_mock, patch(
        "trips.views.get_route", side_effect=route_stub
    ), patch("trips.views.plan_trip", side_effect=plan_trip_stub):
        response = client.post("/api/trips/", payload, format="json")

    assert response.status_code == 201
    geocode_mock.assert_not_called()
    data = response.json()
    assert len(data["route_options"]) == 1


@pytest.mark.django_db
@override_settings(
    TRIP_COMPUTE_ASYNC=False,
    ROUTE_INCLUDE_ALTERNATIVES=True,
    ROUTE_MAX_VARIANTS_PER_LEG=3,
    ROUTE_MAX_COMBINED_CANDIDATES=4,
)
def test_create_trip_caps_combined_alternative_candidates_for_faster_compute():
    client = APIClient()

    payload = {
        "current_location": "Chicago, IL",
        "current_location_lat": 41.8781,
        "current_location_lon": -87.6298,
        "pickup_location": "Indianapolis, IN",
        "pickup_location_lat": 39.7684,
        "pickup_location_lon": -86.1581,
        "dropoff_location": "Nashville, TN",
        "dropoff_location_lat": 36.1627,
        "dropoff_location_lon": -86.7816,
        "current_cycle_used": 12.0,
    }

    def route_stub(*args, **kwargs):
        alternatives = bool(args[4]) if len(args) >= 5 else bool(kwargs.get("alternatives", False))
        start_lat = args[1] if len(args) >= 2 else kwargs["start_lat"]
        if not alternatives:
            return {
                "distance_miles": 180.0,
                "duration_hours": 3.2,
                "geometry": [[41.8781, -87.6298], [39.7684, -86.1581]],
                "instructions": [],
            }

        if start_lat == 41.8781:
            return [
                {"distance_miles": 180.0, "duration_hours": 3.2, "geometry": [[41.8781, -87.6298], [39.7684, -86.1581]], "instructions": []},
                {"distance_miles": 195.0, "duration_hours": 3.4, "geometry": [[41.8781, -87.6298], [40.9, -86.7], [39.7684, -86.1581]], "instructions": []},
                {"distance_miles": 210.0, "duration_hours": 3.7, "geometry": [[41.8781, -87.6298], [40.8, -86.9], [39.7684, -86.1581]], "instructions": []},
            ]

        return [
            {"distance_miles": 300.0, "duration_hours": 5.5, "geometry": [[39.7684, -86.1581], [36.1627, -86.7816]], "instructions": []},
            {"distance_miles": 320.0, "duration_hours": 5.7, "geometry": [[39.7684, -86.1581], [36.3, -86.95], [36.1627, -86.7816]], "instructions": []},
            {"distance_miles": 340.0, "duration_hours": 6.0, "geometry": [[39.7684, -86.1581], [36.45, -87.1], [36.1627, -86.7816]], "instructions": []},
        ]

    def plan_trip_stub(**kwargs):
        return {
            "stops": [],
            "daily_logs": [],
            "total_on_duty_hours": 14.0,
            "total_drive_hours": round(kwargs["leg1_duration_hours"] + kwargs["leg2_duration_hours"], 2),
            "hos_compliant": True,
            "weekly_hours_used": 26.0,
            "weekly_hours_remaining": 44.0,
        }

    with patch("trips.views.geocode_location"), patch(
        "trips.views.get_route", side_effect=route_stub
    ), patch("trips.views.plan_trip", side_effect=plan_trip_stub) as plan_trip_mock:
        response = client.post("/api/trips/", payload, format="json")

    assert response.status_code == 201
    data = response.json()
    assert len(data["route_options"]) == 4
    assert plan_trip_mock.call_count == 4


@pytest.mark.django_db
@override_settings(TRIP_COMPUTE_ASYNC=False)
def test_create_trip_projects_generated_stop_markers_onto_route():
    client = APIClient()

    payload = {
        "current_location": "Chicago, IL",
        "current_location_lat": 41.8781,
        "current_location_lon": -87.6298,
        "pickup_location": "Indianapolis, IN",
        "pickup_location_lat": 39.7684,
        "pickup_location_lon": -86.1581,
        "dropoff_location": "Nashville, TN",
        "dropoff_location_lat": 36.1627,
        "dropoff_location_lon": -86.7816,
        "current_cycle_used": 12.0,
    }

    def route_stub(*args, **kwargs):
        alternatives = bool(args[4]) if len(args) >= 5 else bool(kwargs.get("alternatives", False))
        route = {
            "distance_miles": 200.0,
            "duration_hours": 4.0,
            "geometry": [
                [41.8781, -87.6298],
                [40.9, -87.1],
                [39.7684, -86.1581],
                [38.3, -86.5],
                [36.1627, -86.7816],
            ],
            "instructions": [],
        }
        return [route] if alternatives else route

    def plan_trip_stub(**_kwargs):
        return {
            "stops": [
                {
                    "type": "CURRENT",
                    "location": "Chicago, IL",
                    "lat": 41.8781,
                    "lon": -87.6298,
                    "arrival_hour": 8.0,
                    "duration_minutes": 30,
                    "description": "Pre-trip inspection",
                    "progress_miles": 0.0,
                },
                {
                    "type": "BREAK",
                    "location": "En route (Leg 1)",
                    "lat": 0.0,
                    "lon": 0.0,
                    "arrival_hour": 12.0,
                    "duration_minutes": 30,
                    "description": "30-min rest break",
                    "progress_miles": 120.0,
                },
            ],
            "daily_logs": [],
            "total_on_duty_hours": 8.0,
            "total_drive_hours": 7.0,
            "hos_compliant": True,
            "weekly_hours_used": 20.0,
            "weekly_hours_remaining": 50.0,
        }

    with patch("trips.views.geocode_location"), patch(
        "trips.views.get_route", side_effect=route_stub
    ), patch("trips.views.plan_trip", side_effect=plan_trip_stub), patch(
        "trips.views.find_nearby_stop_poi",
        return_value={
            "name": "Greenfield Rest Area",
            "lat": 39.9,
            "lon": -86.0,
            "distance_miles": 2.4,
            "category": "Rest area",
        },
    ):
        response = client.post("/api/trips/", payload, format="json")

    assert response.status_code == 201
    data = response.json()
    projected_stop = next(stop for stop in data["stops"] if stop["type"] == "BREAK")
    assert projected_stop["lat"] != 0.0
    assert projected_stop["lon"] != 0.0
    assert projected_stop["location"].startswith("Approx.")


@pytest.mark.django_db
@override_settings(TRIP_COMPUTE_ASYNC=False, RESOLVE_STOP_POIS_DURING_COMPUTE=True)
def test_create_trip_can_resolve_real_stop_pois_when_enabled():
    client = APIClient()

    payload = {
        "current_location": "Chicago, IL",
        "current_location_lat": 41.8781,
        "current_location_lon": -87.6298,
        "pickup_location": "Indianapolis, IN",
        "pickup_location_lat": 39.7684,
        "pickup_location_lon": -86.1581,
        "dropoff_location": "Nashville, TN",
        "dropoff_location_lat": 36.1627,
        "dropoff_location_lon": -86.7816,
        "current_cycle_used": 12.0,
    }

    def route_stub(*args, **kwargs):
        alternatives = bool(args[4]) if len(args) >= 5 else bool(kwargs.get("alternatives", False))
        route = {
            "distance_miles": 200.0,
            "duration_hours": 4.0,
            "geometry": [
                [41.8781, -87.6298],
                [40.9, -87.1],
                [39.7684, -86.1581],
                [38.3, -86.5],
                [36.1627, -86.7816],
            ],
            "instructions": [],
        }
        return [route] if alternatives else route

    def plan_trip_stub(**_kwargs):
        return {
            "stops": [
                {
                    "type": "BREAK",
                    "location": "En route (Leg 1)",
                    "lat": 0.0,
                    "lon": 0.0,
                    "arrival_hour": 12.0,
                    "duration_minutes": 30,
                    "description": "30-min rest break",
                    "progress_miles": 120.0,
                },
            ],
            "daily_logs": [],
            "total_on_duty_hours": 8.0,
            "total_drive_hours": 7.0,
            "hos_compliant": True,
            "weekly_hours_used": 20.0,
            "weekly_hours_remaining": 50.0,
        }

    with patch("trips.views.geocode_location"), patch(
        "trips.views.get_route", side_effect=route_stub
    ), patch("trips.views.plan_trip", side_effect=plan_trip_stub), patch(
        "trips.views.find_nearby_stop_poi",
        return_value={
            "name": "Greenfield Rest Area",
            "lat": 39.9,
            "lon": -86.0,
            "distance_miles": 2.4,
            "category": "Rest area",
        },
    ):
        response = client.post("/api/trips/", payload, format="json")

    assert response.status_code == 201
    data = response.json()
    projected_stop = next(stop for stop in data["stops"] if stop["type"] == "BREAK")
    assert projected_stop["lat"] == 39.9
    assert projected_stop["lon"] == -86.0
    assert projected_stop["location"] == "Greenfield Rest Area"


@pytest.mark.django_db
@override_settings(TRIP_COMPUTE_ASYNC=False)
def test_create_trip_reuses_identical_computed_trip_without_recomputing():
    client = APIClient()

    payload = {
        "current_location": "Chicago, IL",
        "current_location_lat": 41.8781,
        "current_location_lon": -87.6298,
        "pickup_location": "Indianapolis, IN",
        "pickup_location_lat": 39.7684,
        "pickup_location_lon": -86.1581,
        "dropoff_location": "Nashville, TN",
        "dropoff_location_lat": 36.1627,
        "dropoff_location_lon": -86.7816,
        "current_cycle_used": 12.0,
    }

    route = {
        "distance_miles": 180.0,
        "duration_hours": 3.2,
        "geometry": [[41.8781, -87.6298], [39.7684, -86.1581]],
        "instructions": [],
    }

    def plan_trip_stub(**kwargs):
        return {
            "stops": [],
            "daily_logs": [],
            "total_on_duty_hours": 14.0,
            "total_drive_hours": round(kwargs["leg1_duration_hours"] + kwargs["leg2_duration_hours"], 2),
            "hos_compliant": True,
            "weekly_hours_used": 26.0,
            "weekly_hours_remaining": 44.0,
        }

    with patch("trips.views.geocode_location") as geocode_mock, patch(
        "trips.views.get_route", return_value=route
    ) as route_mock, patch("trips.views.plan_trip", side_effect=plan_trip_stub) as plan_trip_mock:
        first_response = client.post("/api/trips/", payload, format="json")
        second_response = client.post("/api/trips/", payload, format="json")

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    geocode_mock.assert_not_called()
    assert route_mock.call_count == 2
    assert plan_trip_mock.call_count == 1
    assert first_response.json()["id"] == second_response.json()["id"]


@pytest.mark.django_db
@override_settings(TRIP_COMPUTE_ASYNC=False)
def test_create_trip_surfaces_variant_failure_reason():
    client = APIClient()

    payload = {
        "current_location": "Chicago, IL",
        "current_location_lat": 41.8781,
        "current_location_lon": -87.6298,
        "pickup_location": "Indianapolis, IN",
        "pickup_location_lat": 39.7684,
        "pickup_location_lon": -86.1581,
        "dropoff_location": "Nashville, TN",
        "dropoff_location_lat": 36.1627,
        "dropoff_location_lon": -86.7816,
        "current_cycle_used": 68.5,
    }

    def route_stub(*args, **kwargs):
        alternatives = bool(args[4]) if len(args) >= 5 else bool(kwargs.get("alternatives", False))
        route = {
            "distance_miles": 200.0,
            "duration_hours": 4.0,
            "geometry": [
                [41.8781, -87.6298],
                [39.7684, -86.1581],
                [36.1627, -86.7816],
            ],
            "instructions": [],
        }
        return [route] if alternatives else route

    with patch("trips.views.geocode_location"), patch(
        "trips.views.get_route", side_effect=route_stub
    ):
        response = client.post("/api/trips/", payload, format="json")

    assert response.status_code == 422
    assert "70-hour" in response.json()["message"]


@pytest.mark.django_db
@override_settings(TRIP_COMPUTE_ASYNC=False)
def test_create_trip_rejects_non_us_manual_coordinates():
    client = APIClient()

    payload = {
        "current_location": "Toronto, ON",
        "current_location_lat": 43.6532,
        "current_location_lon": -79.3832,
        "pickup_location": "Indianapolis, IN",
        "pickup_location_lat": 39.7684,
        "pickup_location_lon": -86.1581,
        "dropoff_location": "Nashville, TN",
        "dropoff_location_lat": 36.1627,
        "dropoff_location_lon": -86.7816,
        "current_cycle_used": 10.0,
    }

    response = client.post("/api/trips/", payload, format="json")

    assert response.status_code == 400
    assert "United States" in str(response.json())
