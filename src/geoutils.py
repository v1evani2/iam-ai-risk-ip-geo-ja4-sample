from typing import Optional, Tuple, Dict
import math

# Minimal demo coordinates for cities used in the simulator
# (lat, lon) degrees
CITY_COORDS: Dict[str, Tuple[float, float]] = {
    "San Jose": (37.3382, -121.8863),
    "Seattle": (47.6062, -122.3321),
    "New York": (40.7128, -74.0060),
    "Austin": (30.2672, -97.7431),
    "Boston": (42.3601, -71.0589),
    "Toronto": (43.6532, -79.3832),
    "Vancouver": (49.2827, -123.1207),
    "Mumbai": (19.0760, 72.8777),
    "Berlin": (52.5200, 13.4050),
}

def lookup_city_coords(city: Optional[str]) -> Optional[Tuple[float, float]]:
    if not city:
        return None
    return CITY_COORDS.get(city)

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0088  # Earth mean radius in km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c
