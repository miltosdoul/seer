"""
k9s-inspired look for the whole app.

k9s reads as calm because it is disciplined about color: near-black
background, aqua as the single accent for anything interactive (cursor,
borders, section keys), steel blue for chrome (column headers, key hints),
orange reserved for the title, and a warm off-white for data values.
Everything here exists to enforce that same small set.
"""

from __future__ import annotations

from textual.theme import Theme

AQUA = "#00d7d7"
STEEL_BLUE = "#5f87d7"
ORANGE = "#ff8700"
SILVER = "#c6c6c6"
GRAY = "#8a8a8a"

# rich styles for the describe view, matching k9s' YAML rendering:
# section keys in the accent, sub-keys in steel blue, values warm off-white.
KEY_STYLE = f"bold {AQUA}"
SUBKEY_STYLE = STEEL_BLUE
VALUE_STYLE = "#ffdfaf"

K9S_THEME = Theme(
    name="k9s",
    primary=AQUA,
    secondary=STEEL_BLUE,
    accent=ORANGE,
    foreground=SILVER,
    background="#000000",
    surface="#080808",
    panel="#121212",
    success="#5fd75f",
    warning="#d7af5f",
    error="#d75f5f",
    dark=True,
    variables={
        "footer-key-foreground": STEEL_BLUE,
        "footer-description-foreground": GRAY,
    },
)
