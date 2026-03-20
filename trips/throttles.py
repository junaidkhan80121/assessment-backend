"""
Per-endpoint throttles so trip polling does not consume the same budget as
trip creation requests.
"""
from rest_framework.throttling import AnonRateThrottle


class TripReadAnonThrottle(AnonRateThrottle):
    scope = "trip_read"


class TripWriteAnonThrottle(AnonRateThrottle):
    scope = "trip_write"
