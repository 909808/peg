"""ANSI rendering: the world map, the site map, and the panels around them.

Terminal output, no dependencies. Colour is 256-colour ANSI with a monochrome
fallback, because the thing being drawn -- a planet, and then one hectare of it
-- reads far better with hypsometric tinting than it does in white on black.
"""

from __future__ import annotations

import math
import os
import shutil
import sys

from .. import geo
from ..local import terrain
from ..world import raster as raster_mod

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"


def supports_colour() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("PEG_FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


class Palette:
    """Wraps colour so every call site can stay unconditional."""

    def __init__(self, enabled: bool | None = None):
        self.on = supports_colour() if enabled is None else enabled

    def fg(self, code: int, text: str) -> str:
        return f"\x1b[38;5;{code}m{text}{RESET}" if self.on else text

    def bg(self, code: int, text: str) -> str:
        return f"\x1b[48;5;{code}m{text}{RESET}" if self.on else text

    def both(self, fg: int, bg: int, text: str) -> str:
        if not self.on:
            return text
        return f"\x1b[38;5;{fg};48;5;{bg}m{text}{RESET}"

    def bold(self, text: str) -> str:
        return f"{BOLD}{text}{RESET}" if self.on else text

    def dim(self, text: str) -> str:
        return f"{DIM}{text}{RESET}" if self.on else text


def term_size() -> tuple[int, int]:
    try:
        s = shutil.get_terminal_size((100, 32))
        return max(60, s.columns), max(20, s.lines)
    except OSError:
        return 100, 32


# --------------------------------------------------------------------------
# world map
# --------------------------------------------------------------------------

#: Hypsometric tints, from abyssal ocean to permanent ice. Chosen to read as a
#: physical map rather than a heat map, so the eye picks out coastlines,
#: mountain chains and the desert belt without a legend.
_OCEAN = ((-8000, 17), (-4000, 18), (-2000, 24), (-500, 25), (-80, 31))
_LAND = ((80, 71), (250, 107), (600, 143), (1200, 179), (2000, 178),
         (3200, 137), (4500, 145), (9000, 231))


def _elev_colour(elev: float, land: bool, glacier: bool) -> int:
    if glacier:
        return 255
    table = _LAND if land else _OCEAN
    for limit, code in table:
        if elev <= limit:
            return code
    return table[-1][1]


def world_map(r: raster_mod.WorldRaster, width: int, height: int,
              pal: Palette, factions=None, focus: tuple[float, float] | None = None,
              lat_range: tuple[float, float] = (-60.0, 78.0),
              lon_range: tuple[float, float] = (-180.0, 180.0)) -> list[str]:
    """Render the planet as a coloured character grid.

    Terminal cells are about twice as tall as they are wide, so the latitude
    span per row is doubled relative to longitude per column; without that
    correction Earth comes out looking like it has been sat on.
    """
    lat0, lat1 = lat_range
    lon0, lon1 = lon_range
    rows: list[str] = []

    marks: dict[tuple[int, int], tuple[str, int]] = {}
    if factions:
        for f in factions:
            if getattr(f, "eliminated", False):
                continue
            for s in f.alive_settlements:
                cx = int((s.lon - lon0) / (lon1 - lon0) * width)
                cy = int((lat1 - s.lat) / (lat1 - lat0) * height)
                if 0 <= cx < width and 0 <= cy < height:
                    marks[(cx, cy)] = (f.glyph, f.colour)
    if focus:
        flon, flat = focus
        cx = int((flon - lon0) / (lon1 - lon0) * width)
        cy = int((lat1 - flat) / (lat1 - lat0) * height)
        marks.setdefault((cx, cy), ("+", 15))

    for j in range(height):
        lat = lat1 - (j + 0.5) / height * (lat1 - lat0)
        line = []
        for i in range(width):
            lon = lon0 + (i + 0.5) / width * (lon1 - lon0)
            mark = marks.get((i, j))
            idx = r.index(lon, lat)
            land = r.land[idx] == 1
            elev = r.elev[idx]
            glacier = r.glacier[idx] == 1
            bg = _elev_colour(elev, land, glacier)
            if mark:
                line.append(pal.both(mark[1], bg, mark[0]))
            elif not land:
                # With colour, ocean is a tint and needs no glyph. Without it,
                # the glyph is the only channel there is.
                line.append(pal.bg(bg, " ") if pal.on else "~")
            elif glacier:
                line.append(pal.both(250, bg, "*"))
            else:
                if elev > 2200:
                    ch = "^"
                elif elev > 700:
                    ch = "n"
                else:
                    ch = " " if pal.on else "."
                line.append(pal.both(bg + 6 if bg < 240 else 250, bg, ch))
        rows.append("".join(line))
    return rows


# --------------------------------------------------------------------------
# site map
# --------------------------------------------------------------------------

TERRAIN_COLOUR = {
    "deep_water": 18, "water": 25, "shallows": 39, "marsh": 65, "mud": 94,
    "sand": 186, "gravel": 145, "soil": 137, "rich_soil": 94, "grass": 71,
    "scree": 145, "rock": 244, "cliff": 240, "snow": 255, "ice": 195,
    "packed": 137, "road": 179, "concrete": 250, "floor": 137,
}

SPECIES_COLOUR = {"oak": 28, "beech": 34, "birch": 149, "pine": 22,
                  "spruce": 22, "fir": 23, "willow": 71, "acacia": 100,
                  "baobab": 94, "mahogany": 22, "kapok": 29, "palm": 35,
                  "mangrove": 30, "olive": 108, "juniper": 65,
                  "dwarf_birch": 108, "saxaul": 101, "bunchgrass": 143,
                  "reed": 78, "berry_bush": 132}


def site_map(m: terrain.LocalMap, cx: int, cy: int, width: int, height: int,
             pal: Palette, pawns=None, cursor: tuple[int, int] | None = None
             ) -> list[str]:
    """Render a window of the 1 m tile grid centred on ``(cx, cy)``."""
    x0 = cx - width // 2
    y0 = cy - height // 2
    people = {}
    if pawns:
        for p in pawns:
            if p.alive:
                people[(p.x, p.y)] = p

    rows = []
    for j in range(height):
        y = y0 + j
        line = []
        for i in range(width):
            x = x0 + i
            if not m.in_bounds(x, y):
                line.append(" ")
                continue
            if cursor and (x, y) == cursor:
                line.append(pal.both(0, 226, "X"))
                continue
            person = people.get((x, y))
            t = m.terrain_at(x, y)
            bg = TERRAIN_COLOUR.get(t.key, 137)
            if person is not None:
                fg = 15 if not person.incapacitated else 203
                line.append(pal.both(fg, bg, person.name[0]))
                continue
            obj = m.object_at(x, y)
            if obj is None:
                line.append(pal.both(bg + 4, bg, t.glyph))
            elif isinstance(obj, terrain.Plant):
                sp = obj.species
                fg = SPECIES_COLOUR.get(sp.key, 34)
                glyph = sp.glyph if obj.growth > 0.5 else "."
                line.append(pal.both(fg, bg, glyph))
            elif isinstance(obj, terrain.OreBody):
                line.append(pal.both(214, bg, "*"))
            elif hasattr(obj, "d"):
                line.append(pal.both(15 if obj.done else 244, bg, obj.d.glyph))
            else:
                line.append(pal.both(250, bg, "o"))
        rows.append("".join(line))
    return rows


# --------------------------------------------------------------------------
# panels
# --------------------------------------------------------------------------


def bar(value: float, width: int, pal: Palette, good_high: bool = True) -> str:
    """A proportional bar, coloured by how worrying the value is."""
    v = max(0.0, min(1.0, value))
    filled = int(round(v * width))
    q = v if good_high else 1.0 - v
    code = 196 if q < 0.25 else (214 if q < 0.5 else (148 if q < 0.75 else 46))
    return pal.fg(code, "#" * filled) + pal.dim("." * (width - filled))


def rule(width: int, pal: Palette, title: str = "") -> str:
    if not title:
        return pal.dim("-" * width)
    left = f"-- {title} "
    return pal.dim(left + "-" * max(0, width - len(left)))


def wrap(text: str, width: int, indent: str = "") -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = indent
    for w in words:
        if len(cur) + len(w) + 1 > width and cur.strip():
            lines.append(cur)
            cur = indent + w
        else:
            cur = (cur + " " + w) if cur.strip() else indent + w
    if cur.strip():
        lines.append(cur)
    return lines


def clock(day: int, year: int, minute: int) -> str:
    month_len = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    d = day % 365
    acc = 0
    for m, n in enumerate(month_len):
        if d < acc + n:
            return f"{d - acc + 1:02d} {names[m]} {year}  {minute//60:02d}:{minute%60:02d}"
        acc += n
    return f"31 Dec {year}"


def compass_note(lat: float, lon: float) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.4f}{ns} {abs(lon):.4f}{ew}"


def scale_note(m: terrain.LocalMap) -> str:
    """Remind the player what they are looking at.

    Worth saying out loud: this is the actual planet, and the window in front
    of them is a few dozen metres of it.
    """
    span = m.size
    total = geo.EARTH_AREA_M2
    return (f"{span} x {span} m at 1 m/tile "
            f"({span*span:,} of {total:.3g} tiles on Earth)")
