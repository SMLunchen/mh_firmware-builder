"""Zeichenbreiten der Meshtastic-Bildschirmschriften.

Erzeugt aus der Sprungtabelle in OLEDDisplayFonts.cpp (4 Byte je Zeichen,
das vierte ist die Breite). Damit laesst sich exakt vorhersagen, ob ein
Splash-Text auf ein Panel passt - drawOEMIconScreen zentriert ihn und
schneidet ihn sonst beidseitig ab.

ScreenFonts.h waehlt bei USERPREFS_OEM_FONT_SIZE=0 FONT_SMALL, und das ist
auf E-Ink ArialMT_Plain_16, sonst ArialMT_Plain_10.
"""

ARIALMT_PLAIN_10: dict[int, int] = {
    32: 3, 33: 3, 34: 4, 35: 6, 36: 6, 37: 9, 38: 7, 39: 2,
    40: 3, 41: 3, 42: 4, 43: 6, 44: 3, 45: 3, 46: 3, 47: 3,
    48: 6, 49: 6, 50: 6, 51: 6, 52: 6, 53: 6, 54: 6, 55: 6,
    56: 6, 57: 6, 58: 3, 59: 3, 60: 6, 61: 6, 62: 6, 63: 6,
    64: 10, 65: 7, 66: 7, 67: 7, 68: 7, 69: 7, 70: 6, 71: 8,
    72: 7, 73: 3, 74: 5, 75: 7, 76: 6, 77: 8, 78: 7, 79: 8,
    80: 7, 81: 8, 82: 7, 83: 7, 84: 6, 85: 7, 86: 7, 87: 9,
    88: 7, 89: 7, 90: 6, 91: 3, 92: 3, 93: 3, 94: 5, 95: 6,
    96: 3, 97: 6, 98: 6, 99: 5, 100: 6, 101: 6, 102: 3, 103: 6,
    104: 6, 105: 2, 106: 2, 107: 5, 108: 2, 109: 8, 110: 6, 111: 6,
    112: 6, 113: 6, 114: 3, 115: 5, 116: 3, 117: 6, 118: 5, 119: 7,
    120: 5, 121: 5, 122: 5, 123: 3, 124: 3, 125: 3, 126: 6, 196: 7,
    214: 8, 220: 7, 223: 6, 228: 6, 246: 6, 252: 6,
}

ARIALMT_PLAIN_16: dict[int, int] = {
    32: 4, 33: 4, 34: 6, 35: 9, 36: 9, 37: 14, 38: 11, 39: 3,
    40: 5, 41: 5, 42: 6, 43: 9, 44: 4, 45: 5, 46: 4, 47: 4,
    48: 9, 49: 9, 50: 9, 51: 9, 52: 9, 53: 9, 54: 9, 55: 9,
    56: 9, 57: 9, 58: 4, 59: 4, 60: 9, 61: 9, 62: 9, 63: 9,
    64: 16, 65: 11, 66: 11, 67: 12, 68: 12, 69: 11, 70: 10, 71: 12,
    72: 12, 73: 4, 74: 8, 75: 11, 76: 9, 77: 13, 78: 12, 79: 12,
    80: 11, 81: 12, 82: 12, 83: 11, 84: 10, 85: 12, 86: 11, 87: 15,
    88: 11, 89: 11, 90: 10, 91: 4, 92: 4, 93: 4, 94: 8, 95: 9,
    96: 5, 97: 9, 98: 9, 99: 8, 100: 9, 101: 9, 102: 4, 103: 9,
    104: 9, 105: 4, 106: 4, 107: 8, 108: 4, 109: 13, 110: 9, 111: 9,
    112: 9, 113: 9, 114: 5, 115: 8, 116: 4, 117: 9, 118: 8, 119: 12,
    120: 8, 121: 8, 122: 8, 123: 5, 124: 4, 125: 5, 126: 9, 196: 11,
    214: 12, 220: 12, 223: 10, 228: 9, 246: 9, 252: 9,
}

FONTS = {"eink": ARIALMT_PLAIN_16, "default": ARIALMT_PLAIN_10}

# Zeilenhoehen aus ScreenFonts.h - bestimmen, wieviel Platz drawOEMIconScreen
# oben (Status) und unten (Titel) fuer sich beansprucht.
FONT_HEIGHTS = {"eink": (19, 28), "default": (13, 19)}


def text_width(text: str, kind: str = "default") -> int:
    """Breite in Pixeln, wie die Firmware sie zeichnen wuerde."""
    table = FONTS.get(kind, ARIALMT_PLAIN_10)
    fallback = table.get(ord("n"), 6)
    return sum(table.get(ord(c), fallback) for c in text)


def fits(text: str, available_px: int, kind: str = "default") -> bool:
    return text_width(text, kind) <= available_px


def max_chars(available_px: int, kind: str = "default") -> int:
    """Konservative Zeichenzahl: rechnet mit einem breiten Kleinbuchstaben."""
    table = FONTS.get(kind, ARIALMT_PLAIN_10)
    per_char = max(table.get(ord(c), 6) for c in "abcdefghijklmnopqrstuvwxyz")
    return max(1, available_px // per_char)
