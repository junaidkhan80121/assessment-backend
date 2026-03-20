"""
Tests for the HOS Engine — 70-hour/8-day cycle fix.
"""
import pytest
from trips.hos_engine import plan_trip


@pytest.mark.django_db
class TestHOSEngine:
    """Test suite for HOS engine algorithm."""

    def test_cycle_used_counts_toward_70hr(self):
        """
        If current_cycle_used=65 and the trip needs >5 hrs on-duty,
        total>70 → must raise ValueError.
        """
        with pytest.raises(ValueError, match="70-hour"):
            plan_trip(
                total_distance_miles=500,
                leg1_miles=250,
                leg2_miles=250,
                leg1_duration_hours=4.5,
                leg2_duration_hours=4.5,
                current_cycle_used=65.0,
                pickup_location="St. Louis, MO",
                dropoff_location="Kansas City, MO",
                pickup_lat=38.63,
                pickup_lon=-90.20,
                dropoff_lat=39.10,
                dropoff_lon=-94.58,
            )

    def test_cycle_used_68_short_trip_raises(self):
        """
        current_cycle_used=68, even a short trip needs pickup+dropoff+inspection=2.5 hrs min.
        68+2.5 = 70.5 → raises.
        """
        with pytest.raises(ValueError):
            plan_trip(
                total_distance_miles=100,
                leg1_miles=50,
                leg2_miles=50,
                leg1_duration_hours=0.9,
                leg2_duration_hours=0.9,
                current_cycle_used=68.0,
                pickup_location="A",
                dropoff_location="B",
                pickup_lat=0,
                pickup_lon=0,
                dropoff_lat=0,
                dropoff_lon=0,
            )

    def test_cycle_used_0_full_70hr_available(self):
        """
        current_cycle_used=0 → driver has full 70 hours.
        A reasonable 2-day trip should succeed.
        """
        result = plan_trip(
            total_distance_miles=1200,
            leg1_miles=600,
            leg2_miles=600,
            leg1_duration_hours=11.0,
            leg2_duration_hours=11.0,
            current_cycle_used=0.0,
            pickup_location="Denver, CO",
            dropoff_location="Salt Lake City, UT",
            pickup_lat=39.74,
            pickup_lon=-104.99,
            dropoff_lat=40.76,
            dropoff_lon=-111.89,
        )
        assert result["hos_compliant"] is True
        assert result["weekly_hours_used"] <= 70.0

    def test_recap_values_are_cumulative(self):
        """
        Recap B (on_duty_last_8_days) must equal current_cycle_used + sum of all
        on_duty_today values from Day 1 through current day.
        """
        result = plan_trip(
            total_distance_miles=800,
            leg1_miles=400,
            leg2_miles=400,
            leg1_duration_hours=7.5,
            leg2_duration_hours=7.5,
            current_cycle_used=20.0,
            pickup_location="Memphis, TN",
            dropoff_location="Atlanta, GA",
            pickup_lat=35.15,
            pickup_lon=-90.05,
            dropoff_lat=33.75,
            dropoff_lon=-84.39,
        )
        running = 20.0
        for log in result["daily_logs"]:
            running += log["recap"]["on_duty_today"]
            assert abs(log["recap"]["on_duty_last_8_days"] - running) < 0.1, \
                f"Day {log['day_number']}: recap B={log['recap']['on_duty_last_8_days']} " \
                f"expected {running:.2f}"

    def test_recap_availability_matches_70hr_and_60hr_formulas(self):
        result = plan_trip(
            total_distance_miles=1200,
            leg1_miles=600,
            leg2_miles=600,
            leg1_duration_hours=11.0,
            leg2_duration_hours=11.0,
            current_cycle_used=20.0,
            pickup_location="Denver, CO",
            dropoff_location="Salt Lake City, UT",
            pickup_lat=39.74,
            pickup_lon=-104.99,
            dropoff_lat=40.76,
            dropoff_lon=-111.89,
        )
        first_day = result["daily_logs"][0]["recap"]
        assert first_day["on_duty_last_8_days"] == 33.0
        assert first_day["available_tomorrow_70"] == 37.0
        assert first_day["on_duty_last_7_days"] == 13.0
        assert first_day["available_tomorrow_60"] == 47.0

    def test_not_driving_hours_count_toward_70hr(self):
        """
        Pickup + dropoff (each 1 hr on-duty not-driving) must add to weekly_hours.
        With current_cycle_used=68.5 and pickup+dropoff+inspection=2.5 hrs → raises.
        """
        with pytest.raises(ValueError):
            plan_trip(
                total_distance_miles=10,
                leg1_miles=5,
                leg2_miles=5,
                leg1_duration_hours=0.1,
                leg2_duration_hours=0.1,
                current_cycle_used=68.5,
                pickup_location="A",
                dropoff_location="B",
                pickup_lat=0,
                pickup_lon=0,
                dropoff_lat=0,
                dropoff_lon=0,
            )

    def test_preflight_check_at_70(self):
        """current_cycle_used=70 → raises immediately."""
        with pytest.raises(ValueError, match="all 70 hours"):
            plan_trip(
                total_distance_miles=10,
                leg1_miles=5,
                leg2_miles=5,
                leg1_duration_hours=0.1,
                leg2_duration_hours=0.1,
                current_cycle_used=70.0,
                pickup_location="A",
                dropoff_location="B",
                pickup_lat=0,
                pickup_lon=0,
                dropoff_lat=0,
                dropoff_lon=0,
            )

    def test_daily_logs_sum_to_24_hours(self):
        """Each day's duty entries must sum to exactly 24 hours."""
        result = plan_trip(
            total_distance_miles=500,
            leg1_miles=250,
            leg2_miles=250,
            leg1_duration_hours=4.5,
            leg2_duration_hours=4.5,
            current_cycle_used=0.0,
            pickup_location="Chicago, IL",
            dropoff_location="Detroit, MI",
            pickup_lat=41.88,
            pickup_lon=-87.63,
            dropoff_lat=42.33,
            dropoff_lon=-83.05,
        )
        for log in result["daily_logs"]:
            total = sum(e["hours"] for e in log["duty_entries"])
            assert abs(total - 24.0) < 0.05, \
                f"Day {log['day_number']}: entries sum to {total:.2f}, not 24.0"

    def test_short_trip_succeeds(self):
        """A short trip with low cycle hours should succeed."""
        result = plan_trip(
            total_distance_miles=200,
            leg1_miles=100,
            leg2_miles=100,
            leg1_duration_hours=2.0,
            leg2_duration_hours=2.0,
            current_cycle_used=10.0,
            pickup_location="City A",
            dropoff_location="City B",
            pickup_lat=40.0,
            pickup_lon=-80.0,
            dropoff_lat=41.0,
            dropoff_lon=-81.0,
        )
        assert result["hos_compliant"] is True
        assert len(result["daily_logs"]) >= 1
        assert len(result["stops"]) >= 3  # at least current, pickup, dropoff
