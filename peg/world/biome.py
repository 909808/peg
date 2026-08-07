"""Biomes and soils, derived from climate and terrain.

Two things matter for play and both are computed, never stored:

**Biome** decides what grows wild, how much wood is standing, how hard the
ground is to cross and how much cover it gives in a firefight.

**Soil** decides whether farming works. This is where PEG diverges from the
usual colony sim, which treats "fertile" as a property of pretty green tiles.
Real soil fertility is close to inverted from lushness: the Amazon has some of
the poorest farmland on Earth because a century of rain has leached the
nutrients straight out of it, while the treeless Ukrainian steppe has the
best, because grass dies back every year and builds two metres of black
mollisol. A colony that clears rainforest for cropland gets two good harvests
and then nothing, which is exactly what happens to people who try it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import rng
from .climate import Climate


@dataclass(frozen=True)
class Biome:
    key: str
    name: str
    glyph: str
    #: Standing trees per hectare. Drives timber yield and line of sight.
    trees_per_ha: float
    #: Wild edible calories per square metre per year. Foraging a hectare of
    #: savanna feeds nobody; a hectare of oak forest in autumn feeds a few.
    forage_kcal_m2: float
    #: Fraction of ground covered by vegetation, 0-1.
    cover: float
    #: Multiplier on the time it takes to walk a metre.
    move_cost: float
    #: Rough share of days per year with usable surface water.
    water_reliability: float


BIOMES: dict[str, Biome] = {
    b.key: b for b in (
        #      key                  name                    gl  trees  forage cover  move  water
        Biome("ice",           "ice cap",                   "*",   0.0,   0.0, 0.00, 1.60, 0.05),
        Biome("tundra",        "tundra",                    ".",   2.0,   9.0, 0.55, 1.20, 0.40),
        Biome("alpine",        "alpine",                    "^",   1.0,   4.0, 0.30, 1.90, 0.55),
        Biome("boreal",        "boreal forest",             "T", 800.0,  16.0, 0.85, 1.45, 0.75),
        Biome("temp_conifer",  "temperate conifer forest",  "T", 620.0,  22.0, 0.85, 1.35, 0.80),
        Biome("temp_broadleaf", "temperate broadleaf forest", "t", 450.0,  44.0, 0.90, 1.30, 0.80),
        Biome("temp_rain",     "temperate rainforest",      "T", 700.0,  38.0, 0.95, 1.55, 0.95),
        Biome("grassland",     "temperate grassland",       ",",   3.0,  13.0, 0.75, 1.05, 0.45),
        Biome("shrubland",     "mediterranean shrubland",   "\"", 60.0,  20.0, 0.60, 1.25, 0.30),
        Biome("steppe",        "steppe",                    "'",   1.0,   7.0, 0.45, 1.05, 0.25),
        Biome("desert",        "desert",                    "~",   0.2,   1.2, 0.06, 1.10, 0.05),
        Biome("semidesert",    "semi-desert",               ":",   4.0,   4.0, 0.22, 1.08, 0.12),
        Biome("savanna",       "savanna",                   ";",  35.0,  15.0, 0.70, 1.10, 0.35),
        Biome("trop_dry",      "tropical dry forest",       "t", 320.0,  30.0, 0.85, 1.30, 0.45),
        Biome("trop_rain",     "tropical rainforest",       "T", 600.0,  34.0, 0.98, 1.70, 0.98),
        Biome("wetland",       "wetland",                   "w",  40.0,  26.0, 0.90, 2.10, 1.00),
        Biome("mangrove",      "mangrove",                  "w", 300.0,  20.0, 0.95, 2.40, 1.00),
        Biome("ocean",         "ocean",                     "≈",   0.0,   0.0, 0.00, 9.90, 0.00),
        Biome("lake",          "lake",                      "≈",   0.0,   2.0, 0.00, 9.90, 1.00),
    )
}


def classify(c: Climate, elev_m: float, relief_m: float,
             is_lake: bool = False, is_glacier: bool = False,
             is_ocean: bool = False) -> Biome:
    """Pick a biome from climate normals and terrain."""
    if is_ocean:
        return BIOMES["ocean"]
    if is_lake:
        return BIOMES["lake"]
    if is_glacier or c.koppen == "EF":
        return BIOMES["ice"]

    k = c.koppen
    group = k[0]

    if group == "E":
        # Above the treeline but below the snowline. Whether that reads as
        # "tundra" or "alpine" depends on whether you are high or far north.
        return BIOMES["alpine"] if elev_m > 2200 else BIOMES["tundra"]

    if group == "B":
        return BIOMES["desert"] if k[1] == "W" else (
            BIOMES["steppe"] if k[2] == "k" else BIOMES["semidesert"]
        )

    if group == "A":
        if k == "Af":
            return BIOMES["trop_rain"]
        if k == "Am":
            return BIOMES["trop_rain"] if c.annual_precip > 2000 else BIOMES["trop_dry"]
        # Aw: savanna if the dry season really bites, dry forest otherwise.
        return BIOMES["savanna"] if min(c.precip_mm) < 20.0 else BIOMES["trop_dry"]

    # C and D: temperate and continental.
    if k[1] == "s":
        return BIOMES["shrubland"]

    p = c.annual_precip
    if group == "D":
        if k[2] in ("c", "d"):
            return BIOMES["boreal"]
        return BIOMES["temp_broadleaf"] if p > 500 else BIOMES["grassland"]

    # C group.
    if p > 1800 and c.temp_range < 20:
        return BIOMES["temp_rain"]
    if p < 500:
        return BIOMES["grassland"]
    if c.coldest < 4.0:
        return BIOMES["temp_conifer"]
    return BIOMES["temp_broadleaf"]


# --------------------------------------------------------------------------
# soil
# --------------------------------------------------------------------------

#: Soil orders, roughly following USDA taxonomy. ``fertility`` is the fraction
#: of a theoretical maximum yield the soil supports without amendment.
@dataclass(frozen=True)
class SoilOrder:
    key: str
    name: str
    fertility: float
    depth_cm: float
    ph: float
    #: 0 waterlogged, 1 free-draining to the point of drought.
    drainage: float
    note: str


SOIL_ORDERS: dict[str, SoilOrder] = {
    s.key: s for s in (
        SoilOrder("mollisol", "mollisol", 0.92, 90, 6.9, 0.55,
                  "deep black grassland soil; the best cropland on Earth"),
        SoilOrder("alluvial", "alluvial", 0.88, 120, 6.8, 0.45,
                  "river-deposited silt, renewed by flooding"),
        SoilOrder("andisol", "andisol", 0.85, 70, 6.2, 0.60,
                  "volcanic ash; very fertile, holds water well"),
        SoilOrder("alfisol", "alfisol", 0.70, 70, 6.2, 0.55,
                  "temperate forest soil, moderately leached"),
        SoilOrder("vertisol", "vertisol", 0.62, 100, 7.4, 0.25,
                  "swelling clay; fertile but cracks and waterlogs"),
        SoilOrder("inceptisol", "inceptisol", 0.55, 45, 6.4, 0.55,
                  "young soil, little horizon development"),
        SoilOrder("histosol", "peat", 0.48, 150, 4.6, 0.05,
                  "waterlogged organic peat; needs drainage"),
        SoilOrder("ultisol", "ultisol", 0.40, 80, 5.2, 0.60,
                  "weathered subtropical soil, acid and low in bases"),
        SoilOrder("aridisol", "aridisol", 0.34, 40, 8.1, 0.85,
                  "desert soil; fertile if irrigated, salts up if done badly"),
        SoilOrder("spodosol", "podzol", 0.28, 40, 4.4, 0.70,
                  "acid, leached, iron-panned; poor cropland"),
        SoilOrder("oxisol", "oxisol", 0.22, 200, 4.8, 0.70,
                  "deeply weathered tropical soil; nutrients are in the trees, "
                  "not the ground"),
        SoilOrder("gelisol", "gelisol", 0.14, 25, 5.4, 0.15,
                  "permafrost beneath; thaws to mud, freezes to rock"),
        SoilOrder("lithosol", "lithosol", 0.08, 8, 6.5, 0.95,
                  "bare rock with a skin of grit"),
    )
}


@dataclass
class Soil:
    order: SoilOrder
    #: Effective fertility after slope, stoniness and climate are accounted
    #: for. This is the number farming actually multiplies by.
    fertility: float
    depth_cm: float
    stoniness: float
    #: True where the ground stays frozen below a shallow active layer.
    permafrost: bool

    @property
    def name(self) -> str:
        return self.order.name

    @property
    def arable(self) -> bool:
        return self.fertility >= 0.25 and self.depth_cm >= 20


def soil_at(c: Climate, b: Biome, slope_deg: float, relief_m: float,
            floodplain: bool, volcanic: bool = False,
            seed: int = 0, x: int = 0, y: int = 0) -> Soil:
    """Derive soil from the climate that weathered it and the land it sits on.

    Order of precedence matters: a steep slope has no soil regardless of
    climate, and a floodplain is alluvial regardless of what the surrounding
    biome is. Only after those does climate get a say.
    """
    if slope_deg > 32.0 or (relief_m > 900 and slope_deg > 20.0):
        order = SOIL_ORDERS["lithosol"]
    elif floodplain:
        order = SOIL_ORDERS["alluvial"]
    elif volcanic:
        order = SOIL_ORDERS["andisol"]
    elif b.key in ("wetland", "mangrove"):
        order = SOIL_ORDERS["histosol"]
    elif b.key in ("tundra", "alpine", "ice"):
        order = SOIL_ORDERS["gelisol"]
    elif b.key == "boreal":
        order = SOIL_ORDERS["spodosol"]
    elif b.key == "trop_rain":
        order = SOIL_ORDERS["oxisol"]
    elif b.key in ("trop_dry", "savanna"):
        # Seasonal wetting and drying makes swelling clays.
        order = SOIL_ORDERS["vertisol"] if c.annual_precip > 700 else SOIL_ORDERS["aridisol"]
    elif b.key in ("desert", "semidesert", "steppe"):
        order = SOIL_ORDERS["aridisol"] if c.annual_precip < 250 else SOIL_ORDERS["mollisol"]
    elif b.key == "grassland":
        order = SOIL_ORDERS["mollisol"]
    elif b.key == "shrubland":
        order = SOIL_ORDERS["inceptisol"]
    elif b.key in ("temp_broadleaf", "temp_conifer", "temp_rain"):
        # Warm and wet enough for long enough and a temperate soil leaches
        # into an ultisol instead of staying an alfisol.
        order = (SOIL_ORDERS["ultisol"]
                 if c.mean_temp > 14.0 and c.annual_precip > 1200
                 else SOIL_ORDERS["alfisol"])
    else:
        order = SOIL_ORDERS["inceptisol"]

    # Local variation: soils are patchy at field scale.
    h = rng.mix(seed, rng.tag("soil"), x, y)
    jitter = 1.0 + rng.sunit(h) * 0.16

    fert = order.fertility * jitter
    # Slope costs topsoil, both to erosion and to the plough.
    fert *= max(0.05, 1.0 - (slope_deg / 30.0) ** 1.6)
    # Cold shortens the season a plant has to use the nutrients.
    if c.growing_days < 120:
        fert *= 0.45 + 0.55 * (c.growing_days / 120.0)

    stoniness = rng.clamp01(relief_m / 1400.0 + slope_deg / 45.0
                            + rng.sunit(rng.splitmix64(h)) * 0.1)
    fert *= 1.0 - 0.55 * stoniness

    depth = order.depth_cm * max(0.12, 1.0 - slope_deg / 38.0) * jitter
    permafrost = c.mean_temp < -2.0

    return Soil(order=order, fertility=rng.clamp01(fert), depth_cm=depth,
                stoniness=stoniness, permafrost=permafrost)
