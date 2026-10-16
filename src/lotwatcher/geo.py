from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_MILES = 3958.8


def miles_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance over the Earth's surface (haversine formula)."""
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * asin(sqrt(a))
