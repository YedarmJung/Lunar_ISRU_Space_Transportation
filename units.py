"""Canonical units and exact conversions used by the optimization model.

External demand-source files remain in kilograms for traceability. Once data
crosses the loader boundary, every modeled mass is in metric tonnes and every
modeled cost is in millions of US dollars.
"""

KG_PER_TONNE = 1_000.0
USD_PER_MUSD = 1_000_000.0

SOLUTION_SCHEMA_VERSION = 2
MODEL_UNITS = {
    "mass": "t",
    "currency": "MUSD",
    "mass_cost": "MUSD/t",
}


def kg_to_tonnes(value):
    """Convert kilograms to metric tonnes."""
    return float(value) / KG_PER_TONNE


def usd_to_musd(value):
    """Convert US dollars to millions of US dollars."""
    return float(value) / USD_PER_MUSD


def usd_per_kg_to_musd_per_t(value):
    """Convert USD/kg to MUSD/metric-tonne."""
    return float(value) * KG_PER_TONNE / USD_PER_MUSD
