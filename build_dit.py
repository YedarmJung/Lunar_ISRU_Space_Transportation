def build_dit(data):
    """Build the exogenous payload source/sink vector from demand events.

    The mass-balance convention in model.py is ``outflow - inflow <= rhs``:
    positive values are sources and negative values are demands.
    """
    d = {}
    for event in data.mission["demand_events"]:
        mass = event["mass_kg"]
        demand_key = ("PL", "GEO", event["demand_step"])
        supply_key = ("PL", "LEO", event["supply_step"])
        d[demand_key] = d.get(demand_key, 0.0) - mass
        d[supply_key] = d.get(supply_key, 0.0) + mass
    return d
