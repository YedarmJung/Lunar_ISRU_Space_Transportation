import copy
import json
import tempfile
import unittest
from pathlib import Path

from build_dit import build_dit
from data import get_data


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "inputs" / "demand_5yr_50tpy.json"


class FullHorizonDataTests(unittest.TestCase):
    def test_default_profile_builds_five_year_horizon(self):
        data = get_data(DEFAULT_PROFILE)
        self.assertEqual(data.T, 181)
        self.assertEqual(data.mission["mission_steps"], 180)
        self.assertEqual(data.mission["mission_days"], 1800)
        self.assertEqual(len(data.mission["demand_events"]), 37)
        self.assertEqual(
            [year * data.mission["days_per_year"] // data.mission["days_per_step"]
             for year in range(6)],
            [0, 36, 72, 108, 144, 180],
        )

    def test_payload_supply_precedes_demand_by_two_steps(self):
        data = get_data(DEFAULT_PROFILE)
        self.assertEqual(
            {event["demand_step"] - event["supply_step"]
             for event in data.mission["demand_events"]},
            {2},
        )

    def test_sources_and_sinks_match_without_fixed_total_check(self):
        data = get_data(DEFAULT_PROFILE)
        d_it = build_dit(data)
        source = sum(
            value for (commodity, node, _time), value in d_it.items()
            if commodity == "PL" and node == "LEO"
        )
        sink = -sum(
            value for (commodity, node, _time), value in d_it.items()
            if commodity == "PL" and node == "GEO"
        )
        self.assertEqual(source, sink)
        self.assertEqual(source, data.mission["total_payload_kg"])

    def _profile(self):
        with DEFAULT_PROFILE.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _assert_invalid(self, profile, message_pattern):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "invalid.json"
            with path.open("w", encoding="utf-8") as f:
                json.dump(profile, f)
            with self.assertRaisesRegex(ValueError, message_pattern):
                get_data(path)

    def test_rejects_off_grid_day(self):
        profile = self._profile()
        profile["events"][0]["demand_day"] = 31
        self._assert_invalid(profile, "multiple of 10")

    def test_rejects_nonpositive_mass(self):
        profile = self._profile()
        profile["events"][0]["mass_kg"] = 0
        self._assert_invalid(profile, "mass_kg must be positive")

    def test_rejects_duplicate_event_id(self):
        profile = self._profile()
        duplicate = copy.deepcopy(profile["events"][0])
        duplicate["demand_day"] += 10
        profile["events"].append(duplicate)
        self._assert_invalid(profile, "Duplicate demand event id")

    def test_rejects_day_outside_mission(self):
        profile = self._profile()
        profile["events"][0]["demand_day"] = 1810
        self._assert_invalid(profile, "inside 0..1800")


if __name__ == "__main__":
    unittest.main()
