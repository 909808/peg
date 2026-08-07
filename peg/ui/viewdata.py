"""Payload builders for the graphical client.

Pure data, no rendering and no server: this module turns a slice of the world
into the arrays the browser draws, and can therefore be tested headlessly.

## Axes

PEG stores local tiles as ``(x, y)`` on a horizontal grid with elevation kept
separately. The GUI uses the usual three-dimensional game convention instead,
and this module is where the two are reconciled once and for all:

    world X  = east        = local tile x
    world Z  = north       = local tile y
    world Y  = up          = elevation

So the top-down view is the **X/Z plane**, and the two cross-sections are
**X/Y** (looking north) and **Z/Y** (looking east). Every field name below uses
the world convention; the local ``(x, y)`` naming stops at this boundary.

## Wire format

Tile data goes over as base64-packed parallel arrays rather than JSON objects.
A 192 x 192 site is 36,864 tiles; as objects that is several megabytes of
punctuation, and as five packed bytes per tile it is 184 KiB.
"""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass

from .. import geo
from ..local import terrain
from ..sim import colony as colony_mod

#: Plane identifiers understood by the client.
PLANE_XZ = "xz"     # top-down, the ground
PLANE_XY = "xy"     # elevation, looking north
PLANE_ZY = "zy"     # elevation, looking east
PLANES = (PLANE_XZ, PLANE_XY, PLANE_ZY)

PLANE_LABELS = {
    PLANE_XZ: "X / Z  top-down",
    PLANE_XY: "X / Y  elevation, looking north",
    PLANE_ZY: "Z / Y  elevation, looking east",
}

#: Terrain colours as RGB. Kept here rather than in the client so that content
#: lives in Python and the browser stays a dumb renderer.
#: Widened deliberately. The first pass gave soil, mud and rich soil three
#: near-identical browns, and a temperate forest rendered as an undifferentiated
#: smear. Ground types now separate by hue as well as value, so you can read the
#: drainage of a site at a glance -- which is what you are actually looking for.
TERRAIN_RGB: dict[str, tuple[int, int, int]] = {
    "deep_water": (16, 38, 78),
    "water":      (30, 76, 132),
    "shallows":   (78, 138, 176),
    "marsh":      (86, 104, 62),
    "mud":        (86, 68, 46),
    "sand":       (214, 194, 140),
    "gravel":     (158, 152, 138),
    "soil":       (126, 98, 68),
    "rich_soil":  (72, 52, 36),
    "grass":      (104, 142, 70),
    "scree":      (150, 144, 134),
    "rock":       (134, 130, 126),
    "cliff":      (78, 74, 72),
    "snow":       (240, 244, 250),
    "ice":        (200, 226, 238),
    "packed":     (138, 116, 88),
    "road":       (158, 142, 112),
    "concrete":   (176, 174, 170),
    "floor":      (152, 116, 78),
}

#: Object classes, as sent in the ``obj`` array.
OBJ_NONE = 0
OBJ_TREE = 1          # broadleaf: a rounded crown
OBJ_SHRUB = 2
OBJ_BOULDER = 3
OBJ_ORE = 4
OBJ_BUILDING = 5
OBJ_CONIFER = 6       # drawn as a spire, which is most of what a forest looks like

OBJ_RGB = {
    OBJ_TREE:     (74, 122, 52),
    OBJ_SHRUB:    (104, 132, 66),
    OBJ_BOULDER:  (146, 142, 136),
    OBJ_ORE:      (208, 156, 58),
    OBJ_BUILDING: (188, 158, 112),
    OBJ_CONIFER:  (48, 92, 60),
}

#: Species that are needle-leaved. Only affects how they are drawn.
CONIFERS = frozenset({"pine", "spruce", "fir", "juniper"})


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@dataclass
class Viewport:
    """A rectangle of the site, in world axes."""

    x0: int
    z0: int
    w: int
    h: int

    def clamp(self, size: int) -> "Viewport":
        w = max(1, min(self.w, size))
        h = max(1, min(self.h, size))
        x0 = max(0, min(self.x0, size - w))
        z0 = max(0, min(self.z0, size - h))
        return Viewport(x0, z0, w, h)


def legend() -> dict:
    """Static colour and label tables. Fetched once by the client."""
    return {
        "terrain": {t.key: {"rgb": TERRAIN_RGB.get(t.key, (120, 100, 80)),
                            "name": t.name,
                            "water": t.water}
                    for t in terrain.TERRAIN_BY_ID},
        "terrain_order": [t.key for t in terrain.TERRAIN_BY_ID],
        "objects": {str(k): {"rgb": v} for k, v in OBJ_RGB.items()},
        "planes": [{"id": p, "label": PLANE_LABELS[p]} for p in PLANES],
    }


def _object_code_and_height(m: terrain.LocalMap, x: int, z: int
                            ) -> tuple[int, float]:
    """Classify what stands on a tile, and how tall it is in metres.

    Height matters because the cross-section views draw it to scale: that is
    the whole point of them. A mature oak is 22 m and a person is 1.7 m, and
    seeing those side by side is what makes "one metre per tile" mean
    something.
    """
    obj = m.object_at(x, z)
    if obj is None:
        return OBJ_NONE, 0.0
    if isinstance(obj, terrain.Plant):
        h = obj.species.height_m * obj.growth
        if obj.species.height_m <= 4.0:
            return OBJ_SHRUB, h
        return (OBJ_CONIFER if obj.species.key in CONIFERS else OBJ_TREE), h
    if isinstance(obj, terrain.OreBody):
        return OBJ_ORE, 0.4
    if hasattr(obj, "d"):                      # a Building
        return OBJ_BUILDING, 2.6 if obj.done else 0.8
    return OBJ_BOULDER, 0.9


def tiles(m: terrain.LocalMap, view: Viewport) -> dict:
    """Pack a rectangle of tiles for the client.

    Five bytes per tile:
      ``terrain``  1 byte, index into ``legend()["terrain_order"]``
      ``obj``      1 byte, one of the OBJ_* codes
      ``objh``     1 byte, object height in decimetres, saturating at 25.5 m
      ``y``        2 bytes, signed elevation in centimetres above the datum
    """
    view = view.clamp(m.size)
    n = view.w * view.h
    terr = bytearray(n)
    objs = bytearray(n)
    objh = bytearray(n)
    ys = bytearray(2 * n)

    i = 0
    for dz in range(view.h):
        z = view.z0 + dz
        for dx in range(view.w):
            x = view.x0 + dx
            m.ensure(x, z)
            idx = m.idx(x, z)
            terr[i] = m.terrain[idx]
            code, height = _object_code_and_height(m, x, z)
            objs[i] = code
            objh[i] = min(255, int(height * 10.0))
            struct.pack_into("<h", ys, i * 2, m.elev_cm[idx])
            i += 1

    return {
        "x0": view.x0, "z0": view.z0, "w": view.w, "h": view.h,
        "size": m.size,
        "datum_m": m.datum_m,
        # Soil depth is a real quantity here -- it gates whether ground is
        # arable at all -- so the cross-section draws the horizon rather than
        # fading the ground into an undifferentiated dark.
        "soil_depth_cm": round(m.survey.soil.depth_cm, 1),
        "soil_name": m.survey.soil.name,
        "terrain": _b64(bytes(terr)),
        "obj": _b64(bytes(objs)),
        "objh": _b64(bytes(objh)),
        "y": _b64(bytes(ys)),
    }


def actors(col: colony_mod.Colony | None) -> list[dict]:
    """People on the map, in world axes."""
    if col is None:
        return []
    out = []
    for p in col.pawns:
        if not p.alive:
            continue
        out.append({
            "name": p.name,
            "initial": p.name[0],
            "x": p.x, "z": p.y,
            "job": p.job or "idle",
            "down": p.incapacitated,
            "hurt": bool(p.injuries),
            # Standing height in metres, for the cross-section.
            "height_m": round(p.height_cm / 100.0, 2),
        })
    return out


def structures(col: colony_mod.Colony | None) -> list[dict]:
    if col is None:
        return []
    return [{
        "name": b.d.name, "x": b.x, "z": b.y,
        "done": b.done,
        "progress": 0.0 if b.d.work_min <= 0 else
                    max(0.0, 1.0 - b.work_left / b.d.work_min),
    } for b in col.buildings]


def fields(col: colony_mod.Colony | None) -> list[dict]:
    if col is None:
        return []
    return [{
        "x": f.x, "z": f.y, "w": f.w, "h": f.h,
        "crop": f.crop.name if f.crop else "",
        "progress": f.progress,
        "ripe": f.ripe,
    } for f in col.fields]


def tile_info(m: terrain.LocalMap, x: int, z: int, col=None) -> dict:
    """Everything worth knowing about one square metre.

    Includes its real latitude and longitude, because that is the claim the
    whole project rests on: this is a place on Earth, not a cell in a level.
    """
    if not m.in_bounds(x, z):
        return {}
    m.ensure(x, z)
    t = m.terrain_at(x, z)
    lat, lon = m.geo_of(x, z)
    code, height = _object_code_and_height(m, x, z)
    obj = m.object_at(x, z)
    what = ""
    if isinstance(obj, terrain.Plant):
        what = f"{obj.species.name} ({obj.growth:.0%} grown, {height:.1f} m)"
    elif isinstance(obj, terrain.OreBody):
        what = f"{obj.mineral} ore, {obj.kg:.0f} kg at {obj.grade:.0%} grade"
    elif hasattr(obj, "d"):
        what = obj.d.name + ("" if obj.done else " (unfinished)")
    elif code == OBJ_BOULDER:
        what = "boulder"

    sv = m.survey
    return {
        "x": x, "z": z,
        "lat": round(lat, 6), "lon": round(lon, 6),
        "elevation_m": round(m.elevation_at(x, z), 2),
        "terrain": t.name,
        "walk_s": round(m.walk_cost(x, z), 2),
        "cover": round(m.cover_at(x, z), 2),
        "object": what,
        "fertility": round(m.fertility[m.idx(x, z)] / 255.0, 2),
        "soil": sv.soil.name,
        "biome": sv.biome.name,
    }


def status(game) -> dict:
    """The header panel: clock, weather, stores, standings."""
    c = game.colony
    s = c.stats
    wx = c.weather
    return {
        "site": {
            "lat": round(c.survey.lat, 4), "lon": round(c.survey.lon, 4),
            "biome": c.survey.biome.name,
            "koppen": c.survey.clim.koppen,
            "soil": c.survey.soil.name,
            "size_m": c.map.size,
            "earth_tiles": f"{geo.EARTH_AREA_M2:.4g}",
        },
        "clock": {
            "year": c.year, "day": c.day,
            "minute": c.minute_of_day,
            "text": _clock_text(c.day, c.year, c.minute_of_day),
        },
        "weather": {
            "temp_c": round(wx.temp_c, 1),
            "indoor_c": round(c.indoor_temp(), 1),
            "wind_ms": round(wx.wind_ms, 1),
            "precip_mm": round(wx.precip_mm, 1),
            "raining": wx.raining, "snowing": wx.snowing,
        },
        "colony": {
            "population": c.population,
            "dead": c.dead_count,
            "food_days": round(s.get("food_days", 0.0), 1),
            "fuel_days": round(s.get("fuel_days", 0.0), 1),
            "water_days": round(c.water_days, 1),
            "morale": round(s.get("morale", 0.0), 3),
            "health": round(s.get("health", 0.0), 3),
            "beds": c.beds,
            "stores": c.store.summary(),
        },
        "place": place_name(c.survey.lon, c.survey.lat),
        "objectives": objectives(game),
        "over": game.over,
        "outcome": game.outcome,
        "log": c.log[-8:],
        "standings": [
            {
                "glyph": f.glyph, "name": f.name,
                "doctrine": f.doctrine.name,
                "settlements": len(f.alive_settlements),
                "population": round(f.population),
                "score": round(score),
                "player": f.is_player,
                "relation": None if f.is_player else round(f.relation("player"), 2),
            }
            for f, score in game.world.standings() if f.alive_settlements
        ],
        "intel": game.intel_text(),
    }


# --------------------------------------------------------------------------
# orientation
# --------------------------------------------------------------------------


def place_name(lon: float, lat: float) -> dict:
    """Say where on Earth this is, in words a person can hold on to.

    "57.000N 4.500W" is precise and tells you nothing. The dataset has 711
    named summits, 895 named river reaches and 225 named physical regions, so
    the game can say "in the Grampian Mountains, 14 km from the River Spey"
    instead -- which is the difference between a coordinate and a place.
    """
    from ..world import earth as earth_mod
    data = earth_mod.load()

    regions = [r.name.title() for r in data.regions_at(lon, lat)
               if r.name and r.kind in ("Range/mtn", "Plateau", "Basin",
                                        "Plain", "Lowland", "Foothills")]

    peak = None
    best = 1e9
    for pk in data.peaks:
        if not pk.name:
            continue
        d = geo.haversine_m(lat, lon, pk.lat, pk.lon) / 1000.0
        if d < best:
            best, peak = d, pk
    peak_txt = (f"{peak.name} ({peak.elev_m} m) {best:.0f} km away"
                if peak and best < 400 else "")

    river = None
    rbest = 1e9
    for riv in data.rivers_near(lon, lat, pad=3):
        if not riv.name:
            continue
        pts = riv.line
        for i in range(len(pts) - 1):
            d = earth_mod.point_segment_km(lon, lat, pts[i][0], pts[i][1],
                                           pts[i + 1][0], pts[i + 1][1])
            if d < rbest:
                rbest, river = d, riv
    river_txt = (f"{river.name}, {rbest:.0f} km away"
                 if river and rbest < 300 else "")

    return {
        "region": regions[0] if regions else "",
        "peak": peak_txt,
        "river": river_txt,
        "hemisphere": ("northern" if lat >= 0 else "southern"),
    }


def objectives(game) -> list[dict]:
    """What to do next, and why.

    A colony sim is opaque until you know what is about to kill you. These are
    generated from the actual state rather than a scripted tutorial, so they
    stay useful long after the first hour, and they are ordered by how soon
    the thing they warn about arrives.
    """
    c = game.colony
    st = c.stats
    out: list[dict] = []

    def add(urgency, title, why):
        out.append({"urgency": urgency, "title": title, "why": why})

    if c.water_days < 2:
        add("now", "Get water",
            f"{c.water_days:.1f} days on hand. People die of thirst in three.")
    if c.beds < c.population:
        add("soon", "Build shelter",
            f"{c.beds} beds for {c.population} people. Sleeping out costs "
            f"calories and morale.")
    if not c.fields:
        add("now", "Mark a field",
            f"Nothing is planted. This site supports "
            f"{c.survey.arable_fraction:.0%} arable ground, and about "
            f"{c.population * 2400:,} m2 feeds this many people for a year.")
    food = st.get("food_days", 0)
    if food < 60:
        add("now" if food < 25 else "soon", "Find food",
            f"{food:.0f} days of stores. The next harvest is not close.")
    hdd = c.survey.heating_degree_days
    fuel = st.get("fuel_days", 0)
    if hdd > 1200 and fuel < 60:
        add("soon", "Cut firewood",
            f"{fuel:.0f} days of fuel, and this site needs "
            f"{hdd:,.0f} degree-days of heating a year.")
    hurt = [p for p in c.alive if any(i.tended < 0 for i in p.injuries)]
    if hurt:
        add("now", "Treat the wounded",
            f"{len(hurt)} untreated. Untended wounds go septic in days.")
    scurvy = [p for p in c.alive if p.vit_c_debt_days > 40]
    if scurvy:
        add("soon", "Get something fresh",
            f"{len(scurvy)} people are short of vitamin C. Scurvy arrives "
            f"about 75 days in, and stored grain will not stop it.")
    if not out:
        add("later", "Grow",
            "Nothing is urgent. Expand the fields, build up, and watch the "
            "neighbours.")
    order = {"now": 0, "soon": 1, "later": 2}
    out.sort(key=lambda o: order[o["urgency"]])
    return out[:4]


def world_minimap(game, width: int = 300, height: int = 150) -> dict:
    """A thumbnail of the planet with everyone on it.

    Answers "where am I" at the only scale that really answers it.
    """
    from ..world import raster as raster_mod
    r = raster_mod.get()
    lat0, lat1 = -58.0, 78.0
    px = bytearray(width * height * 3)
    for j in range(height):
        lat = lat1 - (j + 0.5) / height * (lat1 - lat0)
        for i in range(width):
            lon = -180.0 + (i + 0.5) / width * 360.0
            idx = r.index(lon, lat)
            k = (j * width + i) * 3
            if r.glacier[idx]:
                px[k:k+3] = bytes((228, 234, 240))
            elif r.land[idx]:
                e = r.elev[idx]
                if e > 2200:   c = (150, 130, 108)
                elif e > 900:  c = (122, 112, 82)
                else:          c = (86, 100, 62)
                px[k:k+3] = bytes(c)
            else:
                d = min(1.0, max(0.0, -r.elev[idx] / 5000.0))
                px[k:k+3] = bytes((int(28 - 12*d), int(52 - 20*d), int(96 - 30*d)))

    def to_px(lon, lat):
        return [round((lon + 180.0) / 360.0 * width, 1),
                round((lat1 - lat) / (lat1 - lat0) * height, 1)]

    marks = []
    for f in game.world.factions:
        if f.eliminated:
            continue
        for st in f.alive_settlements:
            xy = to_px(st.lon, st.lat)
            marks.append({"x": xy[0], "y": xy[1], "player": f.is_player,
                          "name": f"{f.name} - {st.name}"})
    return {
        "w": width, "h": height,
        "rgb": base64.b64encode(bytes(px)).decode("ascii"),
        "marks": marks,
        "you": to_px(game.colony.survey.lon, game.colony.survey.lat),
    }


def _clock_text(day: int, year: int, minute: int) -> str:
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
