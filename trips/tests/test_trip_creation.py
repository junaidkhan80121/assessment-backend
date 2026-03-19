from unittest.mock import patch

import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
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

    def route_stub(*_args, alternatives=False, **_kwargs):
        if alternatives:
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
    assert len(data["route_options"]) == 2
    assert any(option["is_fastest"] for option in data["route_options"])
    assert len(data["route_instructions"]) == 2
