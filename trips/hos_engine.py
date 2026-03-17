"""
HOS (Hours of Service) Engine — FMCSA-compliant trip planning algorithm.

Implements the 70-hour/8-day cycle with correct tracking of ALL on-duty time
(driving + on-duty-not-driving) against the weekly limit.
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class DutyEntry:
    """A single entry in the driver's daily log."""
    status: str          # OFF_DUTY, SLEEPER, DRIVING, ON_DUTY_NOT_DRIVING
    start_hour: float    # Absolute hour from trip start (0 = midnight Day 1)
    end_hour: float
    location: str


@dataclass
class Stop:
    """A stop during the trip (fuel, rest, pickup, dropoff, break)."""
    type: str            # CURRENT, PICKUP, DROPOFF, FUEL, REST, BREAK
    location: str
    lat: float
    lon: float
    arrival_hour: float  # Absolute hour from trip start
    duration_minutes: int
    description: str


@dataclass
class HOSState:
    """Mutable state tracking for HOS compliance during trip planning."""
    current_hour: float = 8.0       # Trip starts at 08:00 Day 1

    # Daily counters — reset after each 10-hr off-duty block
    daily_drive_hours: float = 0.0   # Max 11 hrs
    duty_period_start: float = 8.0   # Start of current 14-hr window
    hours_since_break: float = 0.0   # Cumulative driving since last 30-min break

    # ── WEEKLY COUNTER — THE CRITICAL ONE ──────────────────────────────────
    # Initialized to current_cycle_used BEFORE the trip starts.
    # Incremented by EVERY on-duty event (driving + not-driving).
    # Checked against 70.0 after EVERY increment.
    weekly_hours: float = 0.0        # Set to current_cycle_used at init

    entries: List[DutyEntry] = field(default_factory=list)
    stops: List[Stop] = field(default_factory=list)


def plan_trip(
    total_distance_miles: float,
    leg1_miles: float,
    leg2_miles: float,
    leg1_duration_hours: float,
    leg2_duration_hours: float,
    current_cycle_used: float,
    pickup_location: str,
    dropoff_location: str,
    pickup_lat: float,
    pickup_lon: float,
    dropoff_lat: float,
    dropoff_lon: float,
    current_location: str = "Current Location",
    current_lat: float = 0.0,
    current_lon: float = 0.0,
) -> dict:
    """
    Plan a trip with full FMCSA HOS compliance.

    Returns a dict with stops, daily_logs, and HOS summary.
    Raises ValueError if the trip violates any HOS rule.
    """

    # ── PRE-FLIGHT CHECK ────────────────────────────────────────────────────
    if current_cycle_used >= 70.0:
        raise ValueError(
            f"You have used all 70 hours in your current 8-day cycle "
            f"({current_cycle_used} hrs). You must take a 34-hour restart "
            f"before driving again."
        )

    hours_remaining_at_start = 70.0 - current_cycle_used

    # ── STATE INIT ───────────────────────────────────────────────────────────
    state = HOSState(weekly_hours=current_cycle_used)

    # ── HELPER: CHECK 70-HR AFTER EVERY ON-DUTY EVENT ───────────────────────
    def check_70hr_limit(about_to_add: float = 0.0) -> None:
        projected = state.weekly_hours + about_to_add
        if projected > 70.0:
            overage = projected - 70.0
            raise ValueError(
                f"Trip exceeds the 70-hour/8-day limit by {overage:.1f} hours. "
                f"Current cycle used: {current_cycle_used:.1f} hrs. "
                f"Hours available for this trip: {hours_remaining_at_start:.1f} hrs. "
                f"Reduce trip distance, lower current cycle hours, or plan a "
                f"34-hour restart before departing."
            )

    # ── HELPERS ──────────────────────────────────────────────────────────────
    def add_off_duty(hours: float, location: str) -> None:
        """Off-duty time does NOT count toward the 70-hr limit."""
        state.entries.append(DutyEntry(
            "OFF_DUTY", state.current_hour, state.current_hour + hours, location
        ))
        state.current_hour += hours
        state.daily_drive_hours = 0.0
        state.duty_period_start = state.current_hour
        state.hours_since_break = 0.0

    def add_on_duty_not_driving(hours: float, location: str) -> None:
        """On-duty not driving COUNTS toward the 70-hr weekly limit."""
        check_70hr_limit(about_to_add=hours)
        state.entries.append(DutyEntry(
            "ON_DUTY_NOT_DRIVING",
            state.current_hour, state.current_hour + hours, location
        ))
        state.current_hour += hours
        state.weekly_hours += hours

    def drive_segment(miles: float, hours: float, location: str) -> None:
        """
        Drives a segment, splitting across all HOS limits.
        Driving COUNTS toward the 70-hr weekly limit.
        """
        remaining_hours = hours
        remaining_miles = miles

        while remaining_hours > 0.001:
            until_11hr = 11.0 - state.daily_drive_hours
            until_14hr = 14.0 - (state.current_hour - state.duty_period_start)
            until_break = 8.0 - state.hours_since_break
            until_70hr = 70.0 - state.weekly_hours

            # Insert 30-min break if we've hit 8 cumulative drive hours
            if until_break <= 0.001:
                state.stops.append(Stop(
                    "BREAK", location, 0, 0, state.current_hour, 30, "30-min rest break"
                ))
                add_on_duty_not_driving(0.5, location)
                state.hours_since_break = 0.0
                until_break = 8.0
                until_70hr = 70.0 - state.weekly_hours
                continue

            max_drive = min(
                remaining_hours,
                max(until_11hr, 0.0),
                max(until_14hr, 0.0),
                max(until_break, 0.0),
                max(until_70hr, 0.0),
            )

            if max_drive <= 0.001:
                if until_70hr <= 0.001:
                    raise ValueError(
                        f"Trip cannot be completed within the 70-hour/8-day limit. "
                        f"Cycle already used: {current_cycle_used:.1f} hrs. "
                        f"On-duty hours scheduled in this trip so far: "
                        f"{state.weekly_hours - current_cycle_used:.1f} hrs. "
                        f"Total: {state.weekly_hours:.1f} / 70.0 hrs. "
                        f"Please take a 34-hour restart before beginning this trip, "
                        f"or reduce your current cycle used input."
                    )
                # Hit 11-hr or 14-hr window — mandatory 10-hr rest
                state.stops.append(Stop(
                    "REST", location, 0, 0, state.current_hour, 600,
                    "10-hr mandatory off-duty rest"
                ))
                add_off_duty(10.0, location)
                continue

            fraction = max_drive / remaining_hours
            driven_miles = remaining_miles * fraction

            check_70hr_limit(about_to_add=max_drive)
            state.entries.append(DutyEntry(
                "DRIVING", state.current_hour, state.current_hour + max_drive, location
            ))
            state.current_hour += max_drive
            state.daily_drive_hours += max_drive
            state.hours_since_break += max_drive
            state.weekly_hours += max_drive

            remaining_hours -= max_drive
            remaining_miles -= driven_miles

    # ── TRIP EXECUTION ────────────────────────────────────────────────────────

    # Pre-duty off-duty (midnight to 08:00)
    state.entries.append(DutyEntry("OFF_DUTY", 0.0, 8.0, current_location))

    # Pre-trip inspection: 30 min on-duty not-driving
    state.stops.append(Stop(
        "CURRENT", current_location, current_lat, current_lon,
        state.current_hour, 30, "Pre-trip inspection"
    ))
    add_on_duty_not_driving(0.5, current_location)

    # Drive Leg 1 with fuel stops every 1,000 miles
    miles_per_hr_1 = leg1_miles / leg1_duration_hours if leg1_duration_hours > 0 else 55.0
    fuel_odometer = 0.0
    leg1_driven = 0.0

    while leg1_driven < leg1_miles - 0.001:
        to_next_fuel = 1000.0 - fuel_odometer
        segment_miles = min(leg1_miles - leg1_driven, to_next_fuel)
        segment_hours = segment_miles / miles_per_hr_1
        drive_segment(segment_miles, segment_hours, "En route (Leg 1)")
        leg1_driven += segment_miles
        fuel_odometer += segment_miles
        if fuel_odometer >= 1000.0 and leg1_driven < leg1_miles - 0.001:
            state.stops.append(Stop(
                "FUEL", "En route – Fuel Stop", 0.0, 0.0,
                state.current_hour, 30, "Fuel stop (1,000-mi interval)"
            ))
            add_on_duty_not_driving(0.5, "Fuel Stop")
            fuel_odometer = 0.0

    # Pickup: 1 hr on-duty not-driving
    state.stops.append(Stop(
        "PICKUP", pickup_location, pickup_lat, pickup_lon,
        state.current_hour, 60, "Pickup – 1 hr on-duty"
    ))
    add_on_duty_not_driving(1.0, pickup_location)

    # Drive Leg 2 with fuel stops
    miles_per_hr_2 = leg2_miles / leg2_duration_hours if leg2_duration_hours > 0 else 55.0
    leg2_driven = 0.0

    while leg2_driven < leg2_miles - 0.001:
        to_next_fuel = 1000.0 - fuel_odometer
        segment_miles = min(leg2_miles - leg2_driven, to_next_fuel)
        segment_hours = segment_miles / miles_per_hr_2
        drive_segment(segment_miles, segment_hours, "En route (Leg 2)")
        leg2_driven += segment_miles
        fuel_odometer += segment_miles
        if fuel_odometer >= 1000.0 and leg2_driven < leg2_miles - 0.001:
            state.stops.append(Stop(
                "FUEL", "En route – Fuel Stop", 0.0, 0.0,
                state.current_hour, 30, "Fuel stop (1,000-mi interval)"
            ))
            add_on_duty_not_driving(0.5, "Fuel Stop")
            fuel_odometer = 0.0

    # Dropoff: 1 hr on-duty not-driving
    state.stops.append(Stop(
        "DROPOFF", dropoff_location, dropoff_lat, dropoff_lon,
        state.current_hour, 60, "Dropoff – 1 hr on-duty"
    ))
    add_on_duty_not_driving(1.0, dropoff_location)

    # Fill remaining hours of last day with off-duty
    day_end = (int(state.current_hour / 24) + 1) * 24.0
    state.entries.append(DutyEntry("OFF_DUTY", state.current_hour, day_end, dropoff_location))

    # Build per-day log sheets
    trip_on_duty_hours = state.weekly_hours - current_cycle_used

    # Calculate total drive hours
    total_drive_hrs = sum(
        e.end_hour - e.start_hour
        for e in state.entries
        if e.status == "DRIVING"
    )

    daily_logs = build_daily_logs(state.entries, state.stops, current_cycle_used)

    return {
        "stops": [_stop_to_dict(s) for s in state.stops],
        "daily_logs": daily_logs,
        "total_on_duty_hours": round(trip_on_duty_hours, 2),
        "total_drive_hours": round(total_drive_hrs, 2),
        "hos_compliant": True,
        "weekly_hours_used": round(state.weekly_hours, 2),
        "weekly_hours_remaining": round(max(0.0, 70.0 - state.weekly_hours), 2),
        "total_distance_miles": round(total_distance_miles, 2),
    }


def _stop_to_dict(stop: Stop) -> dict:
    return {
        "type": stop.type,
        "location": stop.location,
        "lat": stop.lat,
        "lon": stop.lon,
        "arrival_hour": stop.arrival_hour,
        "duration_minutes": stop.duration_minutes,
        "description": stop.description,
    }


def build_daily_logs(
    entries: List[DutyEntry],
    stops: List[Stop],
    current_cycle_used: float,
) -> list:
    """
    Slice flat entry list into calendar-day logs.
    Each day covers exactly 24 hours (gaps filled with OFF_DUTY).
    """
    if not entries:
        return []

    total_days = int(max(e.end_hour for e in entries) / 24) + 1
    logs = []
    trip_on_duty_so_far = 0.0

    for day_idx in range(total_days):
        day_start = float(day_idx * 24)
        day_end = day_start + 24.0

        # Clip entries to this calendar day
        day_entries: list = []
        for e in entries:
            if e.end_hour <= day_start or e.start_hour >= day_end:
                continue
            cs = max(e.start_hour, day_start) - day_start
            ce = min(e.end_hour, day_end) - day_start
            if ce - cs < 0.0001:
                continue  # Skip zero-duration entries
            day_entries.append({
                "status": e.status,
                "start": hours_to_hhmm_24(cs),
                "end": hours_to_hhmm_24(ce),
                "hours": round(ce - cs, 4),
                "location": e.location,
            })

        # Sort chronologically and fill any gaps
        day_entries.sort(key=lambda x: time_to_minutes(x["start"]))
        day_entries = fill_gaps(day_entries)

        # Compute status totals
        totals: dict = {
            "OFF_DUTY": 0.0, "SLEEPER": 0.0,
            "DRIVING": 0.0, "ON_DUTY_NOT_DRIVING": 0.0,
        }
        for e in day_entries:
            if e["status"] in totals:
                totals[e["status"]] = round(totals[e["status"]] + e["hours"], 4)

        today_on_duty = totals["DRIVING"] + totals["ON_DUTY_NOT_DRIVING"]
        trip_on_duty_so_far += today_on_duty

        on_duty_last_8_days = round(current_cycle_used + trip_on_duty_so_far, 2)
        available_tomorrow = round(max(0.0, 70.0 - on_duty_last_8_days), 2)

        # Build remarks from stops on this day
        remarks = []
        for s in stops:
            if day_start <= s.arrival_hour < day_end:
                rel_hour = s.arrival_hour - day_start
                remarks.append({
                    "time": hours_to_hhmm_24(rel_hour),
                    "location": s.location,
                    "note": s.description,
                })

        # Validate: entries must sum to 24 hours
        total_hours = sum(e["hours"] for e in day_entries)
        assert abs(total_hours - 24.0) < 0.05, \
            f"Day {day_idx + 1} entries sum to {total_hours:.2f}, not 24.0"

        logs.append({
            "date": f"Day {day_idx + 1}",
            "day_number": day_idx + 1,
            "duty_entries": day_entries,
            "totals": {k: round(v, 2) for k, v in totals.items()},
            "remarks": remarks,
            "recap": {
                "on_duty_today": round(today_on_duty, 2),
                "on_duty_last_8_days": on_duty_last_8_days,
                "available_tomorrow": available_tomorrow,
                "hours_warning": on_duty_last_8_days > 60.0,
                "hours_critical": on_duty_last_8_days > 65.0,
            },
        })

    return logs


def fill_gaps(entries: list) -> list:
    """
    Given a sorted list of duty entries for one 24-hour day,
    insert OFF_DUTY entries to fill any gaps and ensure total == 24h.
    Handles overlapping entries by advancing cursor past them.
    """
    result: list = []
    cursor = 0.0  # minutes from midnight

    for e in entries:
        e_start_min = time_to_minutes(e["start"])
        e_end_min = time_to_minutes(e["end"])

        # Skip entries that are fully behind the cursor (overlapping)
        if e_end_min <= cursor + 0.5:
            continue

        # If entry starts before cursor but ends after, trim it
        if e_start_min < cursor - 0.5:
            trimmed_start = cursor
            trimmed_hours = (e_end_min - cursor) / 60.0
            if trimmed_hours < 0.001:
                continue
            result.append({
                "status": e["status"],
                "start": minutes_to_hhmm(cursor),
                "end": e["end"],
                "hours": round(trimmed_hours, 4),
                "location": e.get("location", ""),
            })
            cursor = e_end_min
            continue

        if e_start_min > cursor + 0.5:
            gap_hours = (e_start_min - cursor) / 60.0
            result.append({
                "status": "OFF_DUTY",
                "start": minutes_to_hhmm(cursor),
                "end": e["start"],
                "hours": round(gap_hours, 4),
                "location": "",
            })
        result.append(e)
        cursor = e_end_min

    # Fill gap at end of day (cursor → 24:00 = 1440 min)
    if cursor < 1439.5:
        gap_hours = (1440.0 - cursor) / 60.0
        result.append({
            "status": "OFF_DUTY",
            "start": minutes_to_hhmm(cursor),
            "end": "24:00",
            "hours": round(gap_hours, 4),
            "location": "",
        })

    return result


def hours_to_hhmm(h: float) -> str:
    """Convert decimal hours to HH:MM, wrapping at 24."""
    total_minutes = round(h * 60)
    hh = (total_minutes // 60) % 24
    mm = total_minutes % 60
    return f"{hh:02d}:{mm:02d}"


def hours_to_hhmm_24(h: float) -> str:
    """Convert decimal hours to HH:MM for a 24-hour day (0-24, no wrap)."""
    total_minutes = round(h * 60)
    hh = total_minutes // 60
    mm = total_minutes % 60
    if hh >= 24:
        return "24:00"
    return f"{hh:02d}:{mm:02d}"


def minutes_to_hhmm(m: float) -> str:
    total = round(m)
    hh = total // 60
    mm = total % 60
    if hh >= 24:
        return "24:00"
    return f"{hh:02d}:{mm:02d}"


def time_to_minutes(t: str) -> float:
    parts = t.split(":")
    return int(parts[0]) * 60.0 + int(parts[1])
