import unittest
import math
from backend.services.distance import haversine, validate_gate_distance


class TestDistanceService(unittest.TestCase):

    def test_haversine_known_10km_pair(self):
        # Point A: Khandala reference coordinate (18.7548, 73.3769)
        # Point B: Shifted ~10 km directly North (0.0899322 degrees latitude)
        # 10.0 km / (6371.0 * pi / 180) = ~0.0899322 deg
        lat1, lon1 = 18.7548, 73.3769
        lat2, lon2 = 18.7548 + (10.0 / (6371.0 * math.pi / 180.0)), 73.3769

        dist = haversine(lat1, lon1, lat2, lon2)
        # Verify within tolerance +/- 0.1 km
        self.assertAlmostEqual(dist, 10.0, delta=0.1)

    def test_haversine_known_city_pair(self):
        # Mumbai CSMT (18.9400, 72.8354) to Dadar (19.0178, 72.8431) is ~8.7 km
        dist = haversine(18.9400, 72.8354, 19.0178, 72.8431)
        self.assertAlmostEqual(dist, 8.70, delta=0.1)

    def test_validate_gate_distance_valid(self):
        gate = {"id": "gate30_31", "distance_from_near_km": 1.1}
        stations = {"_reference": {"KAD_LNL_segment_km": 3.0}}
        self.assertTrue(validate_gate_distance(gate, stations))

    def test_validate_gate_distance_exceeded_warning(self):
        gate = {"id": "gate30_31", "distance_from_near_km": 4.5}
        stations = {"_reference": {"KAD_LNL_segment_km": 3.0}}
        # Exceeds 3.0 km segment, should log warning and return False
        with self.assertLogs("backend.services.distance", level="WARNING") as log:
            result = validate_gate_distance(gate, stations)
            self.assertFalse(result)
            self.assertTrue(any("exceeds reference segment" in m for m in log.output))


if __name__ == "__main__":
    unittest.main()
