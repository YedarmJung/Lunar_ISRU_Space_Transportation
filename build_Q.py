from math import exp

from data import activity_component


G0_KM_S2 = 9.80665e-3
O2_FROM_H2O = 8.0 / 9.0
H2_FROM_H2O = 1.0 / 9.0
LOX_LH2_MIXTURE_RATIO = 5.5


def build_Q(data):
    Q = {}

    for v in data.arc_type:
        Q[v] = {}
        for a, arc in enumerate(data.arcs):
            Q[v][a] = {}
            for row in data.commodities:
                Q[v][a][row] = {}
                for col in data.commodities+list(data.vehicles):
                    Q[v][a][row][col] = 1.0 if row == col else 0.0


            if (
                v in data.vehicles
                and data.vehicles[v]["isp_s"] is not None
                and arc.delta_v_km_s is not None  # hold arcs: no propellant burn
            ):
                isp = data.vehicles[v]["isp_s"]
                mr = exp(arc.delta_v_km_s / (isp * G0_KM_S2))

                alpha = 1- 1.0 / mr

                if "Prop" in data.commodities:
                    Q[v][a]["Prop"]["Prop"] = 1-alpha

                    for col in data.commodities:
                        if col != "Prop":
                            Q[v][a]["Prop"][col] = -alpha

                    Q[v][a]["Prop"][v] = -alpha * data.vehicles[v]["dry_mass"]

    return Q
