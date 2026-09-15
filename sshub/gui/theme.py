"""Design tokens for the Smart Shutdown Hub UI (v3 redesign).

Every color, font and spacing constant used by the GUI lives here so the
whole cockpit can be re-skinned by touching a single file. The palette is
a deep "cockpit" scheme with a mint accent; status colors follow a fixed
traffic-light semantic used across dashboard, badge, overlay and logs.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Palette: base surfaces, borders, text
# --------------------------------------------------------------------------- #
BG_ROOT = "#0d1017"        # window background (deepest layer)
BG_CARD = "#141824"        # cards / accordion sections
BG_CARD_INNER = "#10131c"  # nested inputs inside a card
BG_ELEVATED = "#1a1f2e"    # buttons, option menus, entries
BG_HOVER = "#232a3e"       # hover state for elevated surfaces
BG_CONTROL = "#232a3e"     # segmented buttons, sliders trough
BORDER = "#252d42"         # card borders (subtle blue-grey)
BORDER_ACTIVE = "#2f3952"  # focused/emphasized borders

TEXT_PRIMARY = "#eef1f7"   # headings, values
TEXT_SECONDARY = "#9aa4bd"  # labels, captions
TEXT_MUTED = "#5c6478"     # hints, footer version

# --------------------------------------------------------------------------- #
# Accent + status colors (traffic-light semantic, used everywhere)
# --------------------------------------------------------------------------- #
ACCENT = "#00e5a0"         # mint: armed / positive / primary actions
ACCENT_HOVER = "#00c08a"
ACCENT_TEXT = "#0b0d12"    # dark text on accent fills
WARN = "#ffd166"           # amber: countdown, dry-run, caution
DANGER = "#ff5c5c"         # red: emergency, disarm, hot CPU
DANGER_HOVER = "#e04848"
ON_DANGER = "#ffffff"     # text placed on danger fills
INFO = "#7f9cf5"           # periwinkle: AI advice, hub
GHOST = "#2d3446"          # neutral secondary buttons
ARMED_PILL_BG = "#10241d"     # status pill while armed (mint-tinted)
ARMED_PILL_BORDER = "#1f4a3a"

FONT = "Segoe UI"
MONO = "Consolas"

F_HERO = (FONT, 18, "bold")
F_TITLE = (FONT, 15, "bold")
F_SECTION = (FONT, 12, "bold")
F_LABEL = (FONT, 11)
F_LABEL_BOLD = (FONT, 11, "bold")
F_SMALL = (FONT, 10)
F_SMALL_BOLD = (FONT, 10, "bold")
F_META = (FONT, 9)
F_VALUE = (MONO, 11, "bold")
F_BIG_VALUE = (MONO, 12, "bold")
F_COUNTDOWN = (FONT, 44, "bold")


def score_color(score: float) -> str:
    """Traffic-light color for a 0-100 risk score (dashboard + smart)."""
    if score < 40:
        return ACCENT
    if score < 70:
        return WARN
    return DANGER


def live_color(pct: float, max_pct: float = 100.0) -> str:
    """Color for a live metric: nominal mint, hot amber, critical red."""
    try:
        frac = float(pct) / float(max_pct)
    except (TypeError, ValueError, ZeroDivisionError):
        return TEXT_SECONDARY
    if frac < 0.7:
        return ACCENT
    if frac < 0.9:
        return WARN
    return DANGER
