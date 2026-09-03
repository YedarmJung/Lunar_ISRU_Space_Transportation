import json
import math
import unittest
from pathlib import Path

from build_Q import G0_KM_S2, build_Q
from build_cost import BUILD_COST
from data import get_data
from units import (
    KG_PER_TONNE,
    USD_PER_MUSD,
    kg_to_tonnes,
    usd_per_kg_to_musd_per_t,
    usd_to_musd,
)


ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "inputs"


class UnitConversionTests(unittest.TestCase):
    def test_exact_conversion_factors(self):
        self.assertEqual(KG_PER_TONNE, 1_000.0)
        self.assertEqual(USD_PER_MUSD, 1_000_000.0)
        self.assertEqual(kg_to_tonnes(18_500.0), 18.5)
        self.assertEqual(usd_to_musd(150_000_000.0), 150.0)
        self.assertEqual(usd_per_kg_to_musd_per_t(8_000.0), 8.0)

    def test_every_demand_profile_is_converted_once(self):
        for profile_path in sorted(INPUT_DIR.glob("*.json")):
            with self.subTest(profile=profile_path.name):
                with profile_path.open(encoding="utf-8") as handle:
                    raw = json.load(handle)
                data = get_data(profile_path)
                raw_total_kg = sum(float(event["mass_kg"]) for event in raw["events"])

                self.assertAlmostEqual(
                    data.mission["total_payload_t"], raw_total_kg / 1_000.0, places=12
                )
                self.assertNotIn("total_payload_kg", data.mission)
                self.assertNotIn("demand_by_year_kg", data.mission)
                self.assertTrue(
                    all(
                        "mass_t" in event and "mass_kg" not in event
                        for event in data.mission["demand_events"]
                    )
                )

    def test_vehicle_masses_and_capacities_are_tonnes(self):
        data = get_data(INPUT_DIR / "demand_hist_7yr_2019_2025.json")
        self.assertEqual(data.vehicles["OTV"]["payload_cap_t"], 18.5)
        self.assertEqual(data.vehicles["OTV"]["propellant_cap_t"], 14.0)
        self.assertEqual(data.vehicles["OTV"]["dry_mass_t"], 2.5)
        self.assertEqual(data.vehicles["RT"]["payload_cap_t"], 30.0)
        self.assertEqual(data.vehicles["RT"]["propellant_cap_t"], 20.0)
        self.assertEqual(data.vehicles["RT"]["dry_mass_t"], 4.0)

    def test_cost_coefficients_are_musd_and_musd_per_t(self):
        expected = {
            "SWE_musd_per_t": 10.0,
            "DWE_musd_per_t": 10.0,
            "ISRU_spares_musd_per_t": 10.0,
            "Storage_H2O_musd_per_t": 0.8,
            "Storage_Prop_musd_per_t": 1.869,
            "OTV_unit_musd": 30.0,
            "RT_unit_musd": 150.0,
            "initial_prop_musd_per_t": 0.001045,
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertAlmostEqual(BUILD_COST[key], value, places=12)

        self.assertEqual(
            BUILD_COST["transfer_musd_per_t"],
            {
                "GTO": 8.0,
                "GEO": 16.0,
                "EML1": 12.0,
                "NRHO": 12.0,
                "LLO": 13.5,
                "Moon": 36.0,
            },
        )

    def test_rocket_equation_uses_tonne_dry_mass(self):
        data = get_data(INPUT_DIR / "demand_hist_7yr_2019_2025.json")
        q_matrix = build_Q(data)
        arc_id = next(
            index
            for index, arc in enumerate(data.arcs)
            if arc.kind == "move" and arc.tail == "GTO" and arc.head == "GEO"
        )
        arc = data.arcs[arc_id]
        alpha = 1.0 - 1.0 / math.exp(
            arc.delta_v_km_s / (data.vehicles["OTV"]["isp_s"] * G0_KM_S2)
        )
        self.assertAlmostEqual(
            q_matrix["OTV"][arc_id]["Prop"]["OTV"], -alpha * 2.5, places=12
        )


if __name__ == "__main__":
    unittest.main()
