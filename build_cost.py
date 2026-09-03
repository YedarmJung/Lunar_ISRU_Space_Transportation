# build_cost.py
#
# Model cost coefficients. Source values are kept visibly in their published
# USD or USD/kg form and converted here to the model's canonical MUSD and
# MUSD/metric-tonne units. This avoids hand-conversion errors while keeping the
# literature values auditable.
#
# Unit conventions assumed by model.py:
#   - q[facility, year]         : cumulative ISRU structure mass [t]
#   - Storage_*[node, year]     : cumulative tank mass [t]
#   - first_Tank                : tank mass [t]
#   - transfer_musd_per_t[node] : Earth-to-node delivery cost [MUSD/t]

from units import usd_per_kg_to_musd_per_t, usd_to_musd


BUILD_COST = {
    # Facility build cost (Gkaravela: ISRU manufacturing 10,000 USD/kg).
    # Fixed terms are zero in this linear phase.
    "SWE_fixed_musd": usd_to_musd(0.0),
    "SWE_musd_per_t": usd_per_kg_to_musd_per_t(10_000.0),

    "DWE_fixed_musd": usd_to_musd(0.0),
    "DWE_musd_per_t": usd_per_kg_to_musd_per_t(10_000.0),

    # Spares = 5% of plant mass/year, manufactured at 10,000 USD/kg.
    "ISRU_maint_frac_per_yr": 0.05,
    "ISRU_spares_musd_per_t": usd_per_kg_to_musd_per_t(10_000.0),

    # Storage tank manufacturing costs (Gkaravela Table 3).
    "Storage_H2O_musd_per_t": usd_per_kg_to_musd_per_t(800.0),
    "Storage_Prop_musd_per_t": usd_per_kg_to_musd_per_t(1_869.0),

    # Spacecraft manufacturing costs. The effective pre-migration OTV value
    # was 30M USD (Helios); the duplicate 110M USD entry was shadowed.
    "OTV_unit_musd": usd_to_musd(30_000_000.0),
    "RT_unit_musd": usd_to_musd(150_000_000.0),

    # LO2/LH2 blended material cost: 1.045 USD/kg.
    "initial_prop_musd_per_t": usd_per_kg_to_musd_per_t(1.045),

    # Optional hardware material cost; currently inactive.
    "initial_hw_musd_per_t": usd_per_kg_to_musd_per_t(0.0),

    # Earth-to-node delivery cost [MUSD/t].
    "transfer_musd_per_t": {
        "GTO": usd_per_kg_to_musd_per_t(8_000.0),
        "GEO": usd_per_kg_to_musd_per_t(16_000.0),
        "EML1": usd_per_kg_to_musd_per_t(12_000.0),
        "NRHO": usd_per_kg_to_musd_per_t(12_000.0),
        "LLO": usd_per_kg_to_musd_per_t(13_500.0),
        "Moon": usd_per_kg_to_musd_per_t(36_000.0),
    },
}
