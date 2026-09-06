import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import json
import tempfile
import os

from backend.scripts.fetch_schedule_cache import get_near_stations, fetch_and_cache_station_board


class TestFetchScheduleCache(unittest.TestCase):

    def test_get_near_stations(self):
        sample_gates = [
            {"near_station": "KAD", "far_station": "LNL"},
            {"near_station": "kad", "far_station": "lnl"},  # duplicate lowercase
        ]
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json", encoding="utf-8") as f:
            json.dump(sample_gates, f)
            temp_path = Path(f.name)

        try:
            stations = get_near_stations(temp_path)
            self.assertEqual(stations, ["KAD", "LNL"])
        finally:
            os.remove(temp_path)

    @patch("httpx.Client.get")
    def test_fetch_and_cache_station_board(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "station": {"code": "KAD", "name": "Khandala"},
                "trains": [
                    {
                        "train": {"number": "12701", "name": "Hussain Sagar SF Express"},
                        "stop": {"arrival": "00:02", "departure": "00:02", "stopType": "pass-through"},
                    },
                    {
                        "train": {"number": "01435", "name": "Solapur Special"},
                        "stop": {"arrival": "01:25", "departure": "01:26", "stopType": "halt"},
                    },
                ],
            },
        }
        mock_get.return_value = mock_response

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_data_dir = Path(tmpdir)
            with patch("backend.scripts.fetch_schedule_cache.DATA_DIR", tmp_data_dir):
                success = fetch_and_cache_station_board("KAD", "test_key")
                self.assertTrue(success)

                cache_file = tmp_data_dir / "schedule_cache_KAD.json"
                self.assertTrue(cache_file.exists())

                with open(cache_file, "r", encoding="utf-8") as cf:
                    cached_data = json.load(cf)

                self.assertIn("cached_at", cached_data)
                self.assertEqual(cached_data["station_code"], "KAD")
                self.assertEqual(len(cached_data["data"]["trains"]), 2)


if __name__ == "__main__":
    unittest.main()
