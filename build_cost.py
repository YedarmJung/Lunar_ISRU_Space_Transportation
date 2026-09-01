# build_cost.py
#
# Cost coefficients.  Values are taken from Gkaravela et al. (Table 3 and the
# ISRU sizing/cost section) wherever available; derived/estimated values are
# tagged in the comments.
#
# Unit conventions assumed by model.py:
#   - q[facility, year]         : cumulative ISRU *structure mass* [kg]
#   - Storage_*[node, year]     : cumulative *tank mass* [kg] (tank ratios live
#                                in model.py: 40 kg H2O/kg tank and 1.478 kg
#                                propellant/kg tank)
#   - first_prop, first_Tank    : propellant / tank *mass* [kg]
#
# transfer_cost[node] approximates the un-modeled cost of delivering 1 kg of
# hardware/propellant from Earth directly to that node. Initial infrastructure
# delivery uses its destination value; later RT-carried infrastructure uses the
# GTO value because GTO-to-destination transport is endogenous in model.py.

BUILD_COST = {
    # ---- Facility build cost (Gkaravela: ISRU manufacturing $10,000/kg mass)
    # NOTE: fixed terms are 0 in this linear phase; the piecewise economies-of-
    # scale fixed intercept comes later (Gkaravela Eq. 40, first (0,1000] block).
    "SWE_fixed": 0.0,
    "SWE_per_capacity": 10_000.0,   # $/kg SWE structure mass  [Gkaravela]

    "DWE_fixed": 0.0,
    "DWE_per_capacity": 10_000.0,   # $/kg DWE structure mass  [Gkaravela]

    # ---- ISRU maintenance (Gkaravela Table 3: spares = 5% plant mass / year,
    # manufactured at $10,000/kg; delivery to the plant node is added in model.py)
    "ISRU_maint_frac_per_yr": 0.05,   # spares fraction of plant mass per year
    "ISRU_spares_cost_per_kg": 10_000.0,

    # ---- Storage tank cost (Gkaravela Table 3)
    "Storage_H2O_per_kg": 800.0,    # $/kg water tank mass      [Gkaravela]
    "Storage_Prop_per_kg": 1_869.0, # $/kg cryo prop tank mass  [Gkaravela]

    # ---- Spacecraft manufacturing cost (Gkaravela Table 3: $150M each)
    "OTV_unit": 110_000_000.0,      # $/OTV   [Gkaravela]
    "OTV_unit": 30_000_000.0,      # $/OTV   [Helios]
    "RT_unit": 150_000_000.0,       # $/RT    [Gkaravela]

    # ---- Initial deployment propellant material cost
    # LO2/LH2 blended at 5.5:1 mixture ratio (Gkaravela Table 3:
    # LO2 $0.15/kg, LH2 $5.97/kg) -> (5.5*0.15 + 5.97)/6.5 = $1.045/kg.
    "Ini_Prop_per_kg": 1.045,       # $/kg propellant material  [Gkaravela]

    # Optional: hardware deployment cost if you want to penalize Hw directly.
    "Ini_Hw_per_kg": 0.0,

    # ---- Delivery cost from Earth to each node [$/kg]
    # GTO/GEO/Moon anchored to Bennett & Dempster (2020) / Kornuta (2019).
    # EML1/NRHO/LLO have no direct literature value; estimated to sit between GEO
    # ($16k) and the lunar surface ($35k), increasing with depth in the well.
    "transfer_cost": {
        "GTO": 8_000.0,     # ~$8k/kg to GTO                   [Bennett/Kornuta]
        "GEO": 16_000.0,    # ~$16k/kg to GEO                  [Bennett/Kornuta]
        "EML1": 12_000.0,   # cislunar hub                     [estimate]
        "NRHO": 12_000.0,   # lunar halo orbit                 [estimate]
        "LLO": 13_500.0,    # low lunar orbit (near surface)   [estimate]
        "Moon": 36_000.0,   # transport to lunar pole          [Bennett/Kornuta]
    },
}
