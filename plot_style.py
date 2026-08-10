"""Shared plot styling for the sweep figures.

Used by sweep_post_process.py and sweep_plot_new.py so the same encoding is
used everywhere:
  color     -> mission horizon (years)
  linestyle -> demand cadence (period_days)
"""

HORIZON_COLORS = {4: "#2a78d6", 6: "#008300", 8: "#eda100", 10: "#4a3aa7"}
HORIZON_MARKERS = {4: "o", 6: "s", 8: "^", 10: "D"}
_FALLBACK_COLORS = ["#e87ba4", "#1baf7a", "#eb6834", "#e34948"]

# one linestyle per cadence; add new periods here so every figure stays in sync
PERIOD_LINESTYLES = {
    30: "-.",
    45: (0, (1, 1)),   # densely dotted
    60: "-",
    90: "--",
    120: ":",
}


def horizon_color(years):
    years = int(years)
    if years in HORIZON_COLORS:
        return HORIZON_COLORS[years]
    return _FALLBACK_COLORS[years % len(_FALLBACK_COLORS)]


def horizon_marker(years):
    return HORIZON_MARKERS.get(int(years), "x")


def period_linestyle(period_days):
    return PERIOD_LINESTYLES.get(int(period_days), "-")


def period_label(period_days):
    return f"{int(period_days)}d"
