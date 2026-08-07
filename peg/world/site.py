"""Site survey: everything you need to judge a square kilometre of Earth.

This is the bridge between the two scales. Above it, the strategic layer picks
*where* on the planet to be; below it, the local generator builds the 1 m tiles
you actually play on. Both read the same survey, so a site that looks good on
the world map really is good when you land on it.

The survey is also the rival AIs' entire perception of the planet. They have no
privileged information -- a Steward evaluating a valley calls exactly this
function with exactly the same arguments you do, and gets the same numbers.
Competition comes from different priorities, not different eyesight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import geo
from .. import rng
from . import biome as biome_mod
from . import climate as climate_mod
from . import earth
from . import raster

#: Minerals PEG tracks. Values in a survey are *potential*, 0-1: the chance
#: that prospecting a site finds a workable deposit, times its richness.
MINERALS = ("iron", "copper", "coal", "oil", "stone", "clay", "salt")


@dataclass
class Survey:
    lon: float
    lat: float
    elev_m: float
    slope_deg: float
    relief_m: float
    coast_km: float

    clim: climate_mod.Climate
    biome: biome_mod.Biome
    soil: biome_mod.Soil

    river_km: float
    river_name: str
    river_rank: int
    lake_km: float
    fresh_water: float          # 0-1 reliability of year-round fresh water

    minerals: dict[str, float] = field(default_factory=dict)

    volcanic: bool = False
    floodplain: bool = False

    # ---- derived ----

    @property
    def is_ocean(self) -> bool:
        return self.coast_km < 0

    @property
    def arable_fraction(self) -> float:
        """Share of the surrounding land that could be cropped.

        Four gates, and water is the one people forget. The Sahara has soil
        with a respectable nutrient rating and a 365-day growing season, and is
        of course not farmland, because 157 mm of rain will not raise a crop.
        Beside a river it is some of the best farmland on Earth -- which is the
        entire history of Egypt, and falls straight out of the same rule.
        """
        if not self.soil.arable:
            return 0.0
        gd = self.clim.growing_days
        if gd < 90:
            return 0.0
        f = self.soil.fertility
        f *= rng.clamp01(1.0 - self.slope_deg / 25.0)
        f *= rng.clamp01(gd / 200.0)

        # A cereal crop wants roughly 420 mm over its season. Surface water
        # within a few kilometres means it can be irrigated instead.
        water = rng.clamp01(self.clim.annual_precip / 420.0)
        if self.river_km < 5.0 or self.lake_km < 5.0:
            water = max(water, 0.85)
        elif self.river_km < 15.0:
            water = max(water, 0.55)
        f *= water
        return rng.clamp01(f)

    @property
    def timber_m3_ha(self) -> float:
        """Standing timber volume. About 0.7 m3 per mature stem."""
        return self.biome.trees_per_ha * 0.7

    @property
    def defensibility(self) -> float:
        """0-1. Broken ground and water on one side are worth a great deal
        when the neighbours are hostile."""
        d = rng.clamp01(self.relief_m / 700.0) * 0.6
        d += rng.clamp01(self.slope_deg / 25.0) * 0.2
        if self.coast_km < 3.0 or self.river_km < 1.0:
            d += 0.2      # a flank you do not have to hold
        return rng.clamp01(d)

    @property
    def heating_degree_days(self) -> float:
        """Annual heating demand, degree-days below 18 C. Drives fuel burn,
        which is what actually kills colonies in the first winter."""
        total = 0.0
        for d in range(0, 365, 5):
            t = self.clim.temp_on_day(d)
            if t < 18.0:
                total += (18.0 - t) * 5
        return total

    def summary(self) -> str:
        return (
            f"{self.lat:+.3f}, {self.lon:+.3f}  "
            f"{self.elev_m:.0f} m  {self.biome.name}  "
            f"{self.clim.koppen} ({climate_mod.describe(self.clim.koppen)})\n"
            f"  soil     {self.soil.name}, fertility {self.soil.fertility:.2f} "
            f"({self.soil.order.note})\n"
            f"  climate  {self.clim.mean_temp:.1f} C mean, "
            f"{self.clim.coldest:.1f} to {self.clim.warmest:.1f}, "
            f"{self.clim.annual_precip:.0f} mm/yr, "
            f"{self.clim.growing_days} growing days\n"
            f"  water    {self._water_text()}\n"
            f"  land     slope {self.slope_deg:.1f} deg, relief {self.relief_m:.0f} m, "
            f"arable {self.arable_fraction*100:.0f}%, "
            f"timber {self.timber_m3_ha:.0f} m3/ha\n"
            f"  minerals {self._mineral_text()}"
        )

    def _water_text(self) -> str:
        bits = []
        if self.river_km < 60:
            nm = self.river_name or "unnamed river"
            bits.append(f"{nm} {self.river_km:.1f} km")
        if self.lake_km < 60:
            bits.append(f"lake {self.lake_km:.1f} km")
        if 0 <= self.coast_km < 200:
            bits.append(f"coast {self.coast_km:.0f} km")
        bits.append(f"reliability {self.fresh_water:.0%}")
        return ", ".join(bits)

    def _mineral_text(self) -> str:
        good = [(k, v) for k, v in sorted(self.minerals.items(),
                                          key=lambda kv: -kv[1]) if v > 0.25]
        if not good:
            return "nothing workable"
        return ", ".join(f"{k} {v:.2f}" for k, v in good[:4])


# --------------------------------------------------------------------------
# terrain measurements
# --------------------------------------------------------------------------


def slope_at(r: raster.WorldRaster, lon: float, lat: float) -> float:
    """Mean ground slope in degrees.

    Measured over 8 km rather than one raster cell: a 55 km cell's own
    gradient understates the slope you actually build on, because it has
    already averaged the hills away. The local relief term puts the roughness
    back.
    """
    d = 0.04
    dz_dx = (r.elevation(lon + d, lat) - r.elevation(lon - d, lat))
    dz_dy = (r.elevation(lon, lat + d) - r.elevation(lon, lat - d))
    dx_m = 2 * d * geo.meters_per_degree_lon(lat)
    dy_m = 2 * d * geo.meters_per_degree_lat(lat)
    if dx_m < 1 or dy_m < 1:
        return 0.0
    grad = math.hypot(dz_dx / dx_m, dz_dy / dy_m)
    broad = math.degrees(math.atan(grad))
    # Sub-cell roughness: a cell with 600 m of internal relief is not flat
    # even if its neighbours are at the same height.
    rough = math.degrees(math.atan(r.local_relief(lon, lat) / 12000.0))
    return broad + rough


def nearest_river(data: earth.EarthData, lon: float, lat: float
                  ) -> tuple[float, str, int]:
    """Distance in km to the nearest mapped river, plus its name and rank."""
    best = 1e9
    best_name = ""
    best_rank = 9
    for riv in data.rivers_near(lon, lat, pad=2):
        pts = riv.line
        for i in range(len(pts) - 1):
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            d = earth.point_segment_km(lon, lat, ax, ay, bx, by)
            if d < best:
                best = d
                best_name = riv.name
                best_rank = riv.discharge_class
    return (best if best < 1e8 else 999.0), best_name, best_rank


def nearest_lake(data: earth.EarthData, lon: float, lat: float) -> float:
    best = 1e9
    for ring in data.lakes:
        # Cheap bounding-box reject before touching the edges.
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        if lon < min(xs) - 6 or lon > max(xs) + 6:
            continue
        if lat < min(ys) - 6 or lat > max(ys) + 6:
            continue
        if earth.point_in_ring(lon, lat, ring):
            return 0.0
        for i in range(len(ring)):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % len(ring)]
            d = earth.point_segment_km(lon, lat, ax, ay, bx, by)
            if d < best:
                best = d
    return best if best < 1e8 else 999.0


# --------------------------------------------------------------------------
# geology
# --------------------------------------------------------------------------


def mineral_potential(lon: float, lat: float, r: raster.WorldRaster,
                      data: earth.EarthData, seed: int) -> tuple[dict[str, float], bool]:
    """Prospecting potential per mineral, plus whether the ground is volcanic.

    Ore is not scattered uniformly, and pretending otherwise removes the
    entire point of prospecting. Real distribution follows tectonics:

    * metals concentrate in orogenic belts, where hydrothermal fluids moved
      through fractured rock -- so, mountain ranges
    * coal and hydrocarbons concentrate in sedimentary basins and old
      continental margins, where organic matter was buried
    * clay collects on floodplains, salt in closed arid basins

    On top of that, deposits are *discrete*. Worley noise supplies scattered
    ore bodies, so two sites in the same range can differ completely and
    finding the good one is worth doing.
    """
    regions = data.regions_at(lon, lat)
    kinds = {reg.kind for reg in regions}
    relief = r.local_relief(lon, lat)
    elev = r.elevation(lon, lat)
    coast = r.coast_distance_km(lon, lat)

    orogenic = 1.0 if ("Range/mtn" in kinds or "Foothills" in kinds) else 0.0
    orogenic = max(orogenic, rng.clamp01(relief / 900.0))
    sedimentary = 1.0 if kinds & {"Basin", "Plain", "Lowland", "Valley"} else 0.0
    sedimentary = max(sedimentary, rng.clamp01(1.0 - relief / 500.0))

    out: dict[str, float] = {}
    for mineral in MINERALS:
        # Discrete deposit field: distance to the nearest ore body centre.
        scale = {"iron": 3.0, "copper": 4.5, "coal": 3.5, "oil": 2.5,
                 "stone": 1.5, "clay": 2.0, "salt": 3.0}[mineral]
        d, cell = rng.worley2(rng.mix(seed, rng.tag("ore:" + mineral)),
                              lon / scale, lat / scale)
        # Only some cells contain anything at all.
        present = rng.unit(cell) < {"iron": 0.42, "copper": 0.32, "coal": 0.34,
                                    "oil": 0.26, "stone": 0.9, "clay": 0.7,
                                    "salt": 0.3}[mineral]
        body = max(0.0, 1.0 - d / 0.55) if present else 0.0

        if mineral == "iron":
            host = 0.35 + 0.45 * orogenic + 0.25 * rng.clamp01(1.0 - relief / 1400.0)
        elif mineral == "copper":
            host = 0.15 + 0.85 * orogenic
        elif mineral == "coal":
            host = 0.10 + 0.80 * sedimentary
        elif mineral == "oil":
            host = 0.05 + 0.70 * sedimentary + (0.25 if -300 < coast < 400 else 0.0)
        elif mineral == "stone":
            host = 0.45 + 0.55 * rng.clamp01(relief / 500.0)
        elif mineral == "clay":
            host = 0.30 + 0.60 * sedimentary
        else:  # salt
            arid = 1.0 if elev > 0 and coast > 300 else 0.4
            host = 0.15 + 0.55 * sedimentary * arid + (0.4 if 0 <= coast < 15 else 0.0)

        out[mineral] = rng.clamp01(host * (0.30 + 0.70 * body))

    # Volcanic ground: young ranges near plate margins. Approximated by high
    # relief close to a coast, which catches the Pacific ring and the great
    # rift volcanoes without needing a plate model.
    vh = rng.mix(seed, rng.tag("volcanic"), int(lon * 4), int(lat * 4))
    volcanic = (relief > 700 and abs(coast) < 500 and rng.unit(vh) < 0.22)
    return out, volcanic


# --------------------------------------------------------------------------
# the survey
# --------------------------------------------------------------------------


def survey(lon: float, lat: float, seed: int = 0,
           res: float = raster.DEFAULT_RES) -> Survey:
    """Survey a point on Earth. This is the game's core world query."""
    r = raster.get(res, seed)
    data = earth.load()

    lon = geo.wrap_lon(lon)
    lat = geo.clamp_lat(lat)

    coast_km = r.coast_distance_km(lon, lat)
    elev = climate_mod.settlement_elevation(r, lon, lat)
    relief = r.local_relief(lon, lat)
    slope = slope_at(r, lon, lat)

    clim = climate_mod._compute(r, lon, lat, elev, coast_km)

    river_km, river_name, river_rank = nearest_river(data, lon, lat)
    lake_km = nearest_lake(data, lon, lat)

    is_ocean = not r.is_land(lon, lat)
    is_lake = r.is_lake(lon, lat)
    is_glacier = r.is_glacier(lon, lat)

    # A floodplain is low, flat and close to a substantial river.
    floodplain = (river_km < 3.0 and river_rank <= 5 and slope < 4.0)

    minerals, volcanic = mineral_potential(lon, lat, r, data, seed)

    b = biome_mod.classify(clim, elev, relief, is_lake=is_lake,
                           is_glacier=is_glacier, is_ocean=is_ocean)

    # Wetlands are not a climate class; they are a drainage accident. Flat,
    # wet and beside a river is a marsh whatever the Koppen code says.
    if (not is_ocean and not is_lake and slope < 1.5 and river_km < 4.0
            and clim.aridity_index > 0.9 and elev < 400):
        b = biome_mod.BIOMES["mangrove"] if (clim.coldest > 16 and coast_km < 10) \
            else biome_mod.BIOMES["wetland"]

    soil = biome_mod.soil_at(clim, b, slope, relief, floodplain, volcanic,
                             seed=seed, x=int(lon * 60), y=int(lat * 60))

    # Reliability of fresh water, blending mapped surface water with what the
    # climate alone would supply.
    fw = b.water_reliability
    if river_km < 1.0:
        fw = max(fw, 1.0 - 0.06 * river_rank)
    elif river_km < 12.0:
        fw = max(fw, (1.0 - 0.06 * river_rank) * (1.0 - river_km / 24.0))
    if lake_km < 2.0:
        fw = max(fw, 0.95)
    if clim.annual_precip > 900:
        fw = max(fw, 0.8)
    if is_glacier or clim.koppen == "EF":
        fw = min(fw, 0.2)

    return Survey(
        lon=lon, lat=lat, elev_m=elev, slope_deg=slope, relief_m=relief,
        coast_km=coast_km, clim=clim, biome=b, soil=soil,
        river_km=river_km, river_name=river_name, river_rank=river_rank,
        lake_km=lake_km, fresh_water=rng.clamp01(fw),
        minerals=minerals, volcanic=volcanic, floodplain=floodplain,
    )


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

#: What a Steward weighs when choosing where to settle. Each doctrine is a
#: different weighting of the same survey -- which is the whole competitive
#: dynamic: rivals want overlapping but not identical land, so some valleys
#: are contested and others are conceded.
DOCTRINE_WEIGHTS: dict[str, dict[str, float]] = {
    "agrarian":    {"food": 3.0, "water": 2.0, "wood": 1.0, "ore": 0.6,
                    "energy": 0.6, "defence": 0.7, "climate": 1.4},
    "industrial":  {"food": 1.2, "water": 1.4, "wood": 1.1, "ore": 3.0,
                    "energy": 1.8, "defence": 0.8, "climate": 0.8},
    "militarist":  {"food": 1.3, "water": 1.3, "wood": 0.9, "ore": 1.7,
                    "energy": 1.0, "defence": 2.6, "climate": 0.8},
    "mercantile":  {"food": 1.5, "water": 1.5, "wood": 0.9, "ore": 1.2,
                    "energy": 1.0, "defence": 0.8, "climate": 1.0},
    "survivalist": {"food": 2.0, "water": 2.2, "wood": 1.6, "ore": 0.8,
                    "energy": 0.7, "defence": 1.9, "climate": 1.6},
}


def score(s: Survey, doctrine: str = "agrarian") -> tuple[float, dict[str, float]]:
    """Score a site 0-100 for a given doctrine, with the breakdown.

    Returning the components is deliberate: the AI report shows players *why*
    a rival wanted a particular valley, which turns an opaque expansion into a
    readable move you can anticipate and contest.
    """
    if s.is_ocean or s.biome.key in ("ocean", "lake", "ice"):
        return 0.0, {}

    parts = {
        "food": s.arable_fraction * 0.75 + min(1.0, s.biome.forage_kcal_m2 / 40.0) * 0.25,
        "water": s.fresh_water,
        "wood": min(1.0, s.timber_m3_ha / 350.0),
        "ore": max(s.minerals.get("iron", 0), s.minerals.get("copper", 0)) * 0.6
               + s.minerals.get("coal", 0) * 0.25 + s.minerals.get("stone", 0) * 0.15,
        "energy": s.minerals.get("coal", 0) * 0.4 + s.minerals.get("oil", 0) * 0.6,
        "defence": s.defensibility,
        "climate": _climate_livability(s),
    }
    w = DOCTRINE_WEIGHTS.get(doctrine, DOCTRINE_WEIGHTS["agrarian"])
    total = sum(parts[k] * w[k] for k in parts)
    denom = sum(w.values())
    score_out = 100.0 * total / denom

    # Food is not just another weighted component -- it is a precondition.
    # Weighted alone, an industrial doctrine will happily settle a mineral-rich
    # site that cannot feed a single person, and then starve on top of the ore.
    # So the food term also multiplies the whole score, and a site with nothing
    # edible is worth a fraction of what its other virtues suggest.
    viability = 0.22 + 0.78 * min(1.0, parts["food"] / 0.30)
    parts["viability"] = viability
    return score_out * viability, parts


def _climate_livability(s: Survey) -> float:
    """How hard the weather makes it to simply stay alive here."""
    c = s.clim
    v = 1.0
    # Every degree-day of heating is fuel you have to cut, haul and burn.
    v *= math.exp(-s.heating_degree_days / 5500.0)
    if c.warmest > 34:
        v *= math.exp(-(c.warmest - 34) / 9.0)
    if c.coldest < -25:
        v *= math.exp(-(-25 - c.coldest) / 18.0)
    if c.growing_days < 100:
        v *= 0.35 + 0.65 * (c.growing_days / 100.0)
    if s.slope_deg > 15:
        v *= max(0.2, 1.0 - (s.slope_deg - 15) / 25.0)
    return rng.clamp01(v)


def find_sites(lon0: float, lat0: float, radius_km: float,
               doctrine: str = "agrarian", samples: int = 220,
               seed: int = 0, avoid: list[tuple[float, float]] | None = None,
               min_separation_km: float = 60.0) -> list[tuple[float, Survey, dict]]:
    """Search a region for good settlement sites, best first.

    Uses a deterministic spiral rather than a grid so that adding one sample
    refines the whole search area rather than extending one edge of it.
    """
    out = []
    avoid = avoid or []
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(samples):
        # Sunflower spiral: uniform area coverage, no lattice artefacts.
        rr = radius_km * math.sqrt((i + 0.5) / samples)
        th = i * golden
        bearing = math.degrees(th) % 360.0
        lat, lon = geo.offset_deg(lat0, lon0, bearing, rr * 1000.0)

        too_close = False
        for alat, alon in avoid:
            if geo.haversine_m(lat, lon, alat, alon) < min_separation_km * 1000.0:
                too_close = True
                break
        if too_close:
            continue

        s = survey(lon, lat, seed)
        sc, parts = score(s, doctrine)
        if sc > 0:
            out.append((sc, s, parts))
    out.sort(key=lambda x: -x[0])
    return out
