import math
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Mean Earth radius in kilometers
EARTH_RADIUS_KM = 6371.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance between two points on the Earth
    given their latitude and longitude in decimal degrees.

    Returns:
        Distance in kilometers.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return EARTH_RADIUS_KM * c


def validate_gate_distance(gate: Dict[str, Any], stations: Dict[str, Any]) -> bool:
    """
    Validates that a gate's estimated distance from its near station does not
    exceed the total reference segment length between KAD and LNL.

    Logs a warning (not an error) if violated.

    Returns:
        True if within reference bound, False if bound is exceeded.
    """
    ref = stations.get("_reference", {})
    segment_km = ref.get("KAD_LNL_segment_km")
    distance_km = gate.get("distance_from_near_km")

    if segment_km is None or distance_km is None:
        logger.warning(
            "Missing reference segment or gate distance for validation: segment=%s, distance=%s",
            segment_km,
            distance_km,
        )
        return False

    if distance_km > segment_km:
        logger.warning(
            "Gate distance sanity check failed for '%s': distance_from_near_km (%.2f km) "
            "exceeds reference segment length KAD_LNL_segment_km (%.2f km).",
            gate.get("id", "unknown"),
            distance_km,
            segment_km,
        )
        return False

    return True
