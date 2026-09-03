def build_dit(data, service):
    """Build the selected payload source/sink expressions from satellite events.

    The mass-balance convention in model.py is ``outflow - inflow <= rhs``:
    positive values are sources and negative values are demands.
    """
    d = {}
    for event in data.mission["demand_events"]:
        event_key = (event["year"], event["event_id"])
        selected_mass = event["mass_t"] * service[event_key]
        demand_key = ("PL", "GEO", event["demand_step"])
        supply_key = ("PL", "GTO", event["supply_step"])
        d[demand_key] = d.get(demand_key, 0.0) - selected_mass
        d[supply_key] = d.get(supply_key, 0.0) + selected_mass
    return d
