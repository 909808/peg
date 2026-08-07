"""A colony: people, ground, stores, and the work that connects them.

The simulation runs on one-minute ticks. Every minute, each conscious pawn is
doing exactly one thing, chosen by urgency, and that thing consumes their time
at a rate set by their skill, their injuries and how far they had to walk to
reach it. Work is denominated in person-minutes throughout, so a colony's
capacity is a number you can reason about: fourteen able adults working a
ten-hour day is 8,400 person-minutes, and a winter's firewood costs about
1,200 of them.

The thing that kills colonies here is not raids. It is arithmetic -- a
shortfall in stored calories against heating demand, discovered in November,
when the growing season is eight months away.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from .. import rng
from ..local import terrain
from ..world import climate as climate_mod
from ..world import site as site_mod
from . import items
from .pawn import Pawn

MINUTES_PER_DAY = 1440


# --------------------------------------------------------------------------
# buildings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BuildingDef:
    key: str
    name: str
    glyph: str
    #: Materials required, as ``(item, kg)``.
    cost: tuple[tuple[str, float], ...]
    #: Person-minutes at skill 5.
    work_min: float
    #: Tiles occupied.
    footprint: int = 1
    blocks: bool = True
    #: Insulation this contributes to the settlement, in clo-equivalent.
    insulation: float = 0.0
    #: Sleeping places provided.
    beds: int = 0
    #: Workshop identity, matching Recipe.station.
    station: str = ""
    #: Litres of water per day it makes available.
    water_l_day: float = 0.0
    #: Continuous electrical output, kW.
    kw: float = 0.0
    #: Storage capacity in kg, and whether it is cooled.
    storage_kg: float = 0.0
    cooled: bool = False
    #: Fraction of incoming fire it stops.
    cover: float = 0.0


BUILDINGS: dict[str, BuildingDef] = {b.key: b for b in (
    BuildingDef("shelter", "brush shelter", "n", (("wood", 40),), 180,
                insulation=0.4, beds=2, cover=0.4),
    BuildingDef("cabin", "log cabin", "H", (("wood", 900), ("stone", 60)), 2600,
                footprint=25, insulation=1.6, beds=6, cover=0.75, storage_kg=400),
    BuildingDef("longhouse", "longhouse", "H",
                (("plank", 1400), ("stone", 300)), 5200,
                footprint=60, insulation=2.3, beds=16, cover=0.8, storage_kg=1200),
    BuildingDef("hearth", "hearth", "f", (("stone", 120),), 240,
                insulation=0.5, blocks=False),
    BuildingDef("kitchen", "kitchen", "k", (("wood", 120), ("stone", 80)), 420,
                station="kitchen", blocks=False),
    BuildingDef("sawpit", "sawpit", "s", (("wood", 60),), 260,
                station="sawpit", blocks=False),
    BuildingDef("kiln", "kiln", "o", (("stone", 260), ("clay", 120)), 700,
                station="kiln", blocks=False),
    BuildingDef("smelter", "bloomery", "O", (("stone", 340), ("clay", 200)), 1100,
                station="smelter", blocks=False),
    BuildingDef("workshop", "workshop", "w", (("plank", 300), ("stone", 90)), 1400,
                footprint=16, station="workshop", cover=0.7, blocks=True),
    BuildingDef("machine_shop", "machine shop", "M",
                (("plank", 400), ("steel", 180), ("component", 6)), 4200,
                footprint=25, station="machine_shop", cover=0.8),
    BuildingDef("well", "well", "u", (("stone", 90),), 900, water_l_day=900,
                blocks=False),
    BuildingDef("cistern", "cistern", "u", (("stone", 200), ("clay", 90)), 700,
                water_l_day=400, blocks=False),
    BuildingDef("granary", "granary", "G", (("plank", 260), ("stone", 60)), 900,
                footprint=9, storage_kg=6000, cover=0.6),
    BuildingDef("cold_store", "cold store", "C",
                (("stone", 400), ("plank", 120)), 1600,
                footprint=9, storage_kg=2500, cooled=True, cover=0.7),
    BuildingDef("palisade", "palisade", "#", (("wood", 22),), 42,
                cover=0.85),
    BuildingDef("watchtower", "watchtower", "A", (("plank", 180),), 800,
                cover=0.7),
    BuildingDef("windmill", "windmill", "%", (("plank", 500), ("iron", 60)), 2600,
                footprint=9, kw=4.0),
    BuildingDef("generator", "fuel generator", "%",
                (("steel", 220), ("component", 8)), 1800, kw=18.0),
)}


@dataclass
class Building:
    d: BuildingDef
    x: int
    y: int
    #: Person-minutes still required. Zero means finished.
    work_left: float = 0.0
    hp: float = 100.0

    @property
    def done(self) -> bool:
        return self.work_left <= 0

    @property
    def blocks(self) -> bool:
        return self.d.blocks and self.done


# --------------------------------------------------------------------------
# fields
# --------------------------------------------------------------------------


@dataclass
class Field:
    x: int
    y: int
    w: int
    h: int
    crop: items.Crop | None = None
    #: Accumulated growing degree-days since sowing.
    gdd: float = 0.0
    sown_day: int = -1
    #: 0-1, how much of the field is actually planted and tended.
    tended: float = 0.0
    water_deficit_mm: float = 0.0
    dead: bool = False
    #: Square metres already reaped from the standing crop, and the running
    #: total brought in. Reaping a hectare takes days, so it has to be able to
    #: stop and resume without losing the rest of the field.
    harvested_m2: float = 0.0
    harvest_kg: float = 0.0

    @property
    def area_m2(self) -> int:
        return self.w * self.h

    def tiles(self):
        for yy in range(self.y, self.y + self.h):
            for xx in range(self.x, self.x + self.w):
                yield xx, yy

    @property
    def ripe(self) -> bool:
        return (self.crop is not None and not self.dead
                and self.gdd >= self.crop.gdd_needed)

    @property
    def progress(self) -> float:
        if not self.crop:
            return 0.0
        return min(1.0, self.gdd / self.crop.gdd_needed)


# --------------------------------------------------------------------------
# weather
# --------------------------------------------------------------------------


@dataclass
class Weather:
    temp_c: float = 15.0
    precip_mm: float = 0.0
    wind_ms: float = 3.0
    humidity: float = 0.6
    snow_cover_mm: float = 0.0

    @property
    def raining(self) -> bool:
        return self.precip_mm > 0.4 and self.temp_c > 1.0

    @property
    def snowing(self) -> bool:
        return self.precip_mm > 0.4 and self.temp_c <= 1.0


def daily_weather(clim: climate_mod.Climate, day: int, seed: int) -> Weather:
    """Roll one day's weather from the climate normals.

    Weather is normals plus correlated noise, not an independent draw each
    day: real weather persists, so a cold snap lasts a week and a drought
    lasts a season. The correlation is what makes stores matter.
    """
    base_t = clim.temp_on_day(day)
    # Two noise scales: synoptic (about a week) and seasonal anomaly.
    synoptic = rng.fbm2(rng.mix(seed, rng.tag("wx")), day / 6.0, 0.0, 2)
    seasonal = rng.fbm2(rng.mix(seed, rng.tag("wxs")), day / 70.0, 11.0, 2)
    swing = 3.0 + 0.45 * clim.temp_range
    temp = base_t + synoptic * swing + seasonal * swing * 0.35

    expect = clim.precip_on_day(day)
    wet = rng.fbm2(rng.mix(seed, rng.tag("wxp")), day / 4.0, 3.0, 2)
    # Rainfall is bursty: most days are dry and a few carry the month's total.
    if wet > 0.12:
        precip = expect * (1.0 + wet * 5.0) * 2.2
    else:
        precip = expect * 0.15
    wind = 2.0 + abs(rng.fbm2(rng.mix(seed, rng.tag("wxw")), day / 5.0, 7.0, 2)) * 9.0
    hum = rng.clamp01(0.45 + wet * 0.5 + (0.2 if precip > 1 else 0.0))
    return Weather(temp_c=temp, precip_mm=max(0.0, precip),
                   wind_ms=wind, humidity=hum)


# --------------------------------------------------------------------------
# pathfinding
# --------------------------------------------------------------------------


def astar(m: terrain.LocalMap, start: tuple[int, int], goal: tuple[int, int],
          limit: int = 20000) -> list[tuple[int, int]] | None:
    """Shortest path by traversal time. Returns tiles, or None."""
    if start == goal:
        return [start]
    sx, sy = start
    gx, gy = goal
    if not m.in_bounds(gx, gy):
        return None

    open_set: list[tuple[float, tuple[int, int]]] = [(0.0, start)]
    came: dict[tuple[int, int], tuple[int, int]] = {}
    g: dict[tuple[int, int], float] = {start: 0.0}
    seen = 0
    while open_set:
        _, cur = heapq.heappop(open_set)
        if cur == goal:
            path = [cur]
            while cur in came:
                cur = came[cur]
                path.append(cur)
            path.reverse()
            return path
        seen += 1
        if seen > limit:
            return None
        cx, cy = cur
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx, ny = cx + dx, cy + dy
            if not m.in_bounds(nx, ny):
                continue
            cost = m.walk_cost(nx, ny)
            if cost >= 20.0:
                continue
            if dx and dy:
                cost *= 1.41421
            # Climbing costs extra; descending a little.
            dz = m.elevation_at(nx, ny) - m.elevation_at(cx, cy)
            cost += max(0.0, dz) * 3.0 + max(0.0, -dz) * 0.4
            ng = g[cur] + cost
            if ng < g.get((nx, ny), 1e18):
                g[(nx, ny)] = ng
                came[(nx, ny)] = cur
                h = math.hypot(gx - nx, gy - ny) * 0.85
                heapq.heappush(open_set, (ng + h, (nx, ny)))
    return None


def path_minutes(m: terrain.LocalMap, path: list[tuple[int, int]],
                 p: Pawn) -> float:
    """How long a pawn takes to walk a path, in minutes."""
    if not path or len(path) < 2:
        return 0.0
    speed = max(0.15, p.move_speed_ms)
    seconds = 0.0
    for i in range(1, len(path)):
        x, y = path[i]
        dx = abs(path[i][0] - path[i - 1][0])
        dy = abs(path[i][1] - path[i - 1][1])
        step = 1.41421 if (dx and dy) else 1.0
        seconds += m.walk_cost(x, y) * step / speed
    return seconds / 60.0


# --------------------------------------------------------------------------
# the colony
# --------------------------------------------------------------------------

WORK_TYPES = ("doctor", "firefight", "haul", "cook", "build", "farm", "forage",
              "chop", "mine", "craft", "water", "rest")


@dataclass
class Colony:
    name: str
    faction: str
    survey: site_mod.Survey
    map: terrain.LocalMap
    seed: int

    pawns: list[Pawn] = field(default_factory=list)
    store: items.Store = field(default_factory=items.Store)
    buildings: list[Building] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)

    #: Wild food currently standing within foraging range, in kilocalories.
    forage_stock_kcal: float = -1.0

    #: Litres of drinking water on hand. Water is stored, not assumed: a
    #: prairie colony with no river must haul or dig for it, and in a hard
    #: winter must melt snow, which costs fuel.
    water_l: float = 0.0

    day: int = 80
    minute_of_day: int = 6 * 60
    year: int = 2041
    weather: Weather = field(default_factory=Weather)
    log: list[str] = field(default_factory=list)

    #: Rolling record for the AI and the UI.
    stats: dict[str, float] = field(default_factory=dict)
    #: Person-minutes spent on each work type today.
    work_today: dict[str, float] = field(default_factory=dict)
    dead_count: int = 0

    # ---- derived ----

    @property
    def alive(self) -> list[Pawn]:
        return [p for p in self.pawns if p.alive]

    @property
    def able(self) -> list[Pawn]:
        return [p for p in self.pawns if p.alive and not p.incapacitated]

    @property
    def population(self) -> int:
        return len(self.alive)

    @property
    def insulation(self) -> float:
        """Effective clothing-equivalent insulation from shelter. Sharing one
        cabin between forty people does not insulate forty people."""
        total = sum(b.d.insulation for b in self.buildings if b.done)
        beds = max(1, sum(b.d.beds for b in self.buildings if b.done))
        crowd = min(1.0, beds / max(1, self.population))
        return 0.8 + total * crowd

    @property
    def beds(self) -> int:
        return sum(b.d.beds for b in self.buildings if b.done)

    @property
    def water_l_day(self) -> float:
        """Litres per day arriving without anyone carrying them."""
        built = sum(b.d.water_l_day for b in self.buildings if b.done)
        # Surface water, if the site has any and it is not frozen over.
        natural = 0.0
        if self.survey.fresh_water > 0.2 and self.weather.temp_c > -4:
            natural = 1400.0 * self.survey.fresh_water
        return built + natural

    @property
    def water_capacity_l(self) -> float:
        return 240.0 + 60.0 * self.population + sum(
            b.d.water_l_day for b in self.buildings if b.done)

    @property
    def water_days(self) -> float:
        need = max(1.0, self.population * 3.0)
        return self.water_l / need

    @property
    def stations(self) -> set[str]:
        return {b.d.station for b in self.buildings if b.done and b.d.station}

    @property
    def power_kw(self) -> float:
        return sum(b.d.kw for b in self.buildings if b.done)

    def indoor_temp(self) -> float:
        """Temperature people actually experience.

        Shelter plus a burning hearth lifts the interior well above ambient,
        which is the entire reason to build either.
        """
        out = self.weather.temp_c
        ins = self.insulation
        heated = any(b.done and b.d.insulation > 0 for b in self.buildings)
        if not heated:
            return out
        lift = min(22.0, ins * 7.0)
        if self.store.fuel_mj() <= 0:
            lift *= 0.25
        return out + lift * max(0.0, min(1.0, (18.0 - out) / 30.0 + 0.25))

    def note(self, msg: str) -> None:
        self.log.append(f"[Y{self.year} D{self.day:03d}] {msg}")
        if len(self.log) > 400:
            del self.log[:100]

    # ---- construction helpers ----

    def can_afford(self, d: BuildingDef) -> bool:
        return all(self.store.has(k, v) for k, v in d.cost)

    def order_building(self, key: str, x: int, y: int) -> Building | None:
        d = BUILDINGS[key]
        if not self.can_afford(d):
            return None
        for k, v in d.cost:
            self.store.take(k, v)
        b = Building(d, x, y, work_left=d.work_min)
        self.buildings.append(b)
        i = self.map.idx(x, y)
        self.map.obj_kind[i] = terrain.OBJ_BUILDING
        self.map.objects[i] = b
        return b

    def add_field(self, x: int, y: int, w: int, h: int) -> Field:
        f = Field(x, y, w, h)
        self.fields.append(f)
        return f

    # ---- the tick ----

    def tick(self, minutes: int = 10) -> None:
        """Advance the colony. Ten-minute steps keep work granular enough for
        travel time to matter without simulating every second of a day."""
        r = rng.Rng(rng.mix(self.seed, self.day, self.minute_of_day))

        new_day = False
        self.minute_of_day += minutes
        while self.minute_of_day >= MINUTES_PER_DAY:
            self.minute_of_day -= MINUTES_PER_DAY
            self.day += 1
            new_day = True
            if self.day >= 365:
                self.day = 0
                self.year += 1

        if new_day:
            self._start_day(r)

        hour = self.minute_of_day / 60.0
        is_night = hour < 6.0 or hour > 20.5
        temp = self.indoor_temp()
        self._issue_clothing()

        # Assign and perform work.
        self.work_today.setdefault("_", 0.0)
        for p in list(self.pawns):
            if not p.alive:
                continue
            self._run_pawn(p, minutes, is_night, r)

        # Needs and injuries.
        for p in list(self.pawns):
            if not p.alive:
                continue
            work_frac = 0.0 if (p.asleep or not p.job or p.job == "rest") else 0.75
            outside = p.job in ("chop", "farm", "forage", "mine", "water")
            body_temp = self.weather.temp_c if outside else temp
            if outside and self.weather.wind_ms > 2:
                body_temp = items.wind_chill_c(body_temp, self.weather.wind_ms)
            evs = p.tick(minutes, body_temp, work_frac, is_night, r,
                         humidity=self.weather.humidity)
            for e in evs:
                self.note(e)
            if p.dead:
                self.dead_count += 1

        self._tick_water(minutes)
        self._tick_regrowth(minutes)
        self._tick_crops(minutes)
        self._tick_stores(minutes, temp)
        self._burn_fuel(minutes)

    def _issue_clothing(self) -> None:
        """Dress the colony out of its own stores.

        Insulation is measured in clo: 1 is an office suit, 2 a winter coat,
        4 polar gear. A colony with cloth and leather in the store puts it on
        people, which is the difference between a survivable January and a
        fatal one -- and is why cloth is worth weaving before it is worth
        trading.
        """
        pop = max(1, self.population)
        stock = self.store.amount("cloth") + self.store.amount("leather") * 1.4
        clo = 1.0 + min(2.1, stock / (pop * 4.0))
        for p in self.alive:
            p.clothing_clo = clo

    def _start_day(self, r: rng.Rng) -> None:
        self.weather = daily_weather(self.survey.clim, self.day, self.seed)
        self.work_today.clear()
        # Rain and snowmelt recharge surface water; frozen ground does not.
        if self.weather.snowing:
            self.weather.snow_cover_mm += self.weather.precip_mm

    def _run_pawn(self, p: Pawn, minutes: int, is_night: bool,
                  r: rng.Rng) -> None:
        # Sleep first: an exhausted pawn is not making decisions.
        if p.asleep:
            if p.sleep_debt_h <= 0.5 or (not is_night and p.sleep_debt_h < 4):
                p.asleep = False
            else:
                p.job = "rest"
                return
        if p.incapacitated:
            p.job = "rest"
            return
        if is_night and p.sleep_debt_h > 3 and not self._emergency():
            p.asleep = True
            p.job = "rest"
            return

        # Survival needs pre-empt work.
        if p.water_debt_l > 0.8:
            got = p.drink(self.water_l)
            self.water_l -= got
            if got <= 0 and p.water_debt_l > 2.0:
                p.job = "water"
                self._do_work(p, "water", minutes, r)
                self.work_today["water"] = self.work_today.get("water", 0.0) + minutes
                return
        if p.energy_debt_kcal > 600:
            if p.eat(self.store, r) is not None:
                p.job = "eat"
                return

        job = self._choose_job(p, r)
        p.job = job
        self._do_work(p, job, minutes, r)
        self.work_today[job] = self.work_today.get(job, 0.0) + minutes

    def _emergency(self) -> bool:
        return any(pp.bleeding_ml_min > 3 for pp in self.pawns if pp.alive)

    def _choose_job(self, p: Pawn, r: rng.Rng) -> str:
        """Utility-based work selection.

        Urgency beats aptitude: a bleeding colonist gets the nearest pair of
        hands, not the best doctor, because the best doctor is four hundred
        metres away and the clock is in minutes.
        """
        best = "haul"
        best_u = 0.0
        for job in WORK_TYPES:
            u = self._urgency(job, p)
            if u > best_u:
                best_u = u
                best = job
        return best

    #: Below this apparent temperature, outdoor work stops. People go inside;
    #: colonies that did not have this froze to death cutting firewood they
    #: were about to burn.
    OUTDOOR_WORK_FLOOR_C = -18.0

    #: And an upper limit. Apparent temperature, so humidity counts.
    OUTDOOR_WORK_CEILING_C = 44.0

    def _outdoor_safe(self) -> bool:
        felt_cold = items.wind_chill_c(self.weather.temp_c, self.weather.wind_ms)
        felt_hot = items.heat_index_c(self.weather.temp_c, self.weather.humidity)
        return (felt_cold > self.OUTDOOR_WORK_FLOOR_C
                and felt_hot < self.OUTDOOR_WORK_CEILING_C)

    def _urgency(self, job: str, p: Pawn) -> float:
        s = self.stats
        if job in ("chop", "farm", "forage", "mine") and not self._outdoor_safe():
            return 0.0
        if job == "doctor":
            bleeding = sum(1 for q in self.alive
                           if q.bleeding_ml_min > 0 or
                           any(i.tended < 0 for i in q.injuries))
            if not bleeding:
                return 0.0
            return 9.0 * min(1.0, bleeding) * (0.4 + 0.6 * p.skill_factor("medicine"))
        if job == "cook":
            if "kitchen" not in self.stations:
                return 0.0
            raw = sum(self.store.amount(k) for k in ("grain", "vegetables", "meat"))
            if raw < 1.0:
                return 0.0
            # Anything about to spoil is an emergency: a harvest turning to
            # compost in the barn is the single largest avoidable loss a
            # colony suffers, and preserving it beats almost any other work.
            at_risk = 0.0
            for st in self.store.stacks.values():
                if "food" in st.item.tags and 0 < st.item.shelf_days < 60:
                    if st.freshness < 0.5:
                        at_risk += st.amount * st.item.kcal_kg
            if at_risk > 40000:
                return 6.5 * p.skill_factor("cooking")
            if self.store.amount("meal") > self.population * 2:
                return 0.0
            return 3.2 * p.skill_factor("cooking")
        if job == "farm":
            if not self.fields:
                return 0.0
            need = any(f.crop is None or f.ripe or f.tended < 1.0
                       for f in self.fields)
            if not need:
                return 0.0
            season = 1.0 if self.survey.clim.temp_on_day(self.day) > 4 else 0.1
            return 4.0 * season * p.skill_factor("farming")
        if job == "forage":
            hungry = s.get("food_days", 9.0) < 12.0
            if not hungry or self.forage_stock_kcal < self.forage_annual_kcal * 0.05:
                return 0.0
            return 3.6
        if job == "chop":
            fuel_days = s.get("fuel_days", 9.0)
            wood = self.store.amount("wood")
            if self.survey.timber_m3_ha < 5:
                return 0.0
            if fuel_days < 20:
                return 5.0
            return 2.0 if wood < 2000 else 0.4
        if job == "build":
            pending = [b for b in self.buildings if not b.done]
            return (3.8 if pending else 0.0) * p.skill_factor("construction")
        if job == "mine":
            if not any(o for o in self.map.objects.values()
                       if isinstance(o, terrain.OreBody)):
                return 0.0
            return 1.6 * p.skill_factor("mining")
        if job == "craft":
            if not self.stations:
                return 0.0
            return 1.5 * p.skill_factor("crafting")
        if job == "water":
            days = self.water_days
            if days < 0.5:
                return 8.0
            if days < 2.0:
                return 3.4
            return 0.2 if days > 4.0 else 1.0
        if job == "haul":
            return 0.8
        if job == "rest":
            return 0.3 if p.sleep_debt_h > 12 else 0.0
        return 0.0

    def _do_work(self, p: Pawn, job: str, minutes: int, r: rng.Rng) -> None:
        eff = p.work_speed * minutes
        if eff <= 0:
            return

        if job == "doctor":
            self._work_doctor(p, eff, r)
        elif job == "build":
            self._work_build(p, eff)
        elif job == "chop":
            self._work_chop(p, eff, r)
        elif job == "farm":
            self._work_farm(p, eff, r)
        elif job == "forage":
            self._work_forage(p, eff, r)
        elif job == "mine":
            self._work_mine(p, eff, r)
        elif job == "cook":
            self._work_cook(p, eff)
        elif job == "craft":
            self._work_craft(p, eff)
        elif job == "water":
            self._work_water(p, eff)

    # ---- individual work types ----

    def _work_doctor(self, p: Pawn, eff: float, r: rng.Rng) -> None:
        skill = p.skill_factor("medicine")
        has_med = self.store.has("medicine", 0.2)
        has_herb = self.store.has("herbs", 0.5)
        quality = min(0.95, 0.18 + 0.06 * p.skill("medicine"))
        if has_med:
            quality = min(0.98, quality + 0.35)
        elif has_herb:
            quality = min(0.85, quality + 0.15)

        for q in self.alive:
            if any(i.tended < quality for i in q.injuries):
                cost = 12.0 / max(0.3, skill)
                if eff < cost:
                    break
                eff -= cost
                n = q.tend(quality, r)
                if n:
                    if has_med:
                        self.store.take("medicine", 0.2)
                    elif has_herb:
                        self.store.take("herbs", 0.5)
                    p.learn("medicine", cost)
                    if q.bleeding_ml_min <= 0:
                        self.note(f"{p.name} treated {q.name} ({n} injuries, "
                                  f"quality {quality:.0%})")

    def _work_build(self, p: Pawn, eff: float) -> None:
        for b in self.buildings:
            if b.done:
                continue
            work = eff * p.skill_factor("construction")
            b.work_left -= work
            p.learn("construction", eff)
            if b.done:
                b.work_left = 0.0
                self.note(f"{p.name} finished the {b.d.name}")
            return

    #: Person-minutes to fell, limb and buck one kilogram of firewood by hand.
    #: A tonne of wood is a solid week's work for one person, which is why
    #: heating a colony through a continental winter dominates its labour
    #: budget more than anything else it does.
    CHOP_MIN_PER_KG = 0.055

    def _work_chop(self, p: Pawn, eff: float, r: rng.Rng) -> None:
        budget = eff * p.skill_factor("construction")
        got = 0.0
        for i, obj in list(self.map.objects.items()):
            if budget <= 0:
                break
            if not isinstance(obj, terrain.Plant) or obj.species.height_m < 4:
                continue
            if obj.growth < 0.3:
                continue
            kg = obj.wood_kg
            cost = kg * self.CHOP_MIN_PER_KG
            if cost > budget:
                # Part-processed: take what the time allows and leave the rest
                # standing rather than teleporting a tonne of timber home.
                share = budget / cost
                got += kg * share
                obj.growth *= (1.0 - share) ** 0.5
                budget = 0.0
                break
            got += kg
            budget -= cost
            self.map.obj_kind[i] = terrain.OBJ_NONE
            del self.map.objects[i]
        if got > 0:
            self.store.add("wood", got)
            p.learn("construction", eff * 0.5)

    def _work_farm(self, p: Pawn, eff: float, r: rng.Rng) -> None:
        skill = p.skill_factor("farming")
        clim = self.survey.clim
        for f in self.fields:
            if f.ripe:
                # Reaping by hand runs about 0.9 person-minutes per square
                # metre, so a hectare is roughly 150 person-hours -- which is
                # why harvest is the labour bottleneck of the farming year.
                area = min(f.area_m2 - f.harvested_m2, eff * skill / 0.9)
                if area < 1:
                    return
                yield_kg = self._harvest_yield(f) * area / f.area_m2
                if f.crop:
                    self.store.add(f.crop.yields,
                                   yield_kg * (1 - f.crop.seed_fraction))
                f.harvested_m2 += area
                f.harvest_kg += yield_kg
                p.learn("farming", eff)
                if f.harvested_m2 >= f.area_m2 - 0.5:
                    if f.crop:
                        self.note(f"{f.crop.name} harvest in: "
                                  f"{f.harvest_kg:.0f} kg from {f.area_m2} m2")
                    f.crop = None
                    f.gdd = 0.0
                    f.tended = 0.0
                    f.harvested_m2 = 0.0
                    f.harvest_kg = 0.0
                return
            if f.crop is None:
                # Sow, if anything will ripen in the time left this year.
                # How much of the year's heat is still ahead of us decides
                # whether sowing now is worth the seed.
                fraction = self._season_remaining()
                options = items.best_crops(clim, season_fraction=fraction)
                if not options:
                    continue
                crop = options[0][0]
                f.crop = crop
                f.sown_day = self.day
                f.gdd = 0.0
                f.tended = 0.2
                self.note(f"{p.name} sowed {crop.name}")
                p.learn("farming", eff)
                return
            if f.tended < 1.0:
                f.tended = min(1.0, f.tended + eff * skill / (f.area_m2 * 0.35))
                p.learn("farming", eff)
                return

    def _season_remaining(self) -> float:
        """Fraction of the year's growing heat still ahead of us.

        Decides whether sowing in July is worth the seed -- above the tropics
        it usually is not.
        """
        clim = self.survey.clim
        ahead = 0.0
        for d in range(self.day, self.day + 365):
            t = clim.temp_on_day(d % 365)
            if t < 0 and d > self.day + 30:
                break
            if t > 5:
                ahead += min(30.0, t) - 5.0
        whole = max(1.0, clim.growing_degree_days(base=5.0))
        return min(1.0, ahead / whole)

    def _harvest_yield(self, f: Field) -> float:
        if not f.crop:
            return 0.0
        base = f.crop.kg_per_m2 * f.area_m2
        base *= self.survey.soil.fertility
        base *= 0.35 + 0.65 * f.tended
        if f.water_deficit_mm > 0:
            base *= max(0.1, 1.0 - f.water_deficit_mm / f.crop.water_mm)
        return max(0.0, base)

    #: Foragers range about 2 km from the settlement.
    FORAGE_RADIUS_M = 2000.0
    #: Share of a biome's wild edible production that people can actually
    #: capture. One percent sounds brutal but matches observed forager
    #: densities: a rainforest supports roughly 0.3 people per square
    #: kilometre, not thirty.
    FORAGE_CAPTURE = 0.01

    @property
    def forage_annual_kcal(self) -> float:
        area = math.pi * self.FORAGE_RADIUS_M ** 2
        return self.survey.biome.forage_kcal_m2 * area * self.FORAGE_CAPTURE

    def _work_forage(self, p: Pawn, eff: float, r: rng.Rng) -> None:
        """Gather wild food.

        Bounded twice over: by how fast a person can gather (about 1400 kcal
        an hour in good country) and by how much wild food is standing. The
        second bound is the important one -- forage supplements a colony, it
        never feeds one, and a settlement that tries to live on it strips its
        own territory and then starves.
        """
        if self.forage_stock_kcal <= 0:
            return
        season = 1.0
        t = self.survey.clim.temp_on_day(self.day)
        if t < 4:
            season = 0.10
        elif 240 < self.day % 365 < 300:
            season = 1.5      # autumn glut
        rate = (120.0 + 42.0 * self.survey.biome.forage_kcal_m2) * season
        kcal = min(eff / 60.0 * rate, self.forage_stock_kcal)
        if kcal <= 1.0:
            return
        self.forage_stock_kcal -= kcal
        self.store.add("vegetables", kcal * 0.55 / items.ITEMS["vegetables"].kcal_kg)
        self.store.add("berries", kcal * 0.45 / items.ITEMS["berries"].kcal_kg)
        p.learn("farming", eff * 0.3)

    def _work_mine(self, p: Pawn, eff: float, r: rng.Rng) -> None:
        skill = p.skill_factor("mining")
        # Hand mining moves roughly 0.6 kg of rock per person-minute.
        moved = eff * skill * 0.6
        for i, obj in list(self.map.objects.items()):
            if not isinstance(obj, terrain.OreBody):
                continue
            take = min(obj.kg, moved)
            obj.kg -= take
            key = {"iron": "iron_ore", "copper": "copper_ore",
                   "coal": "coal"}.get(obj.mineral, "stone")
            self.store.add(key, take * (obj.grade if key != "coal" else 1.0))
            self.store.add("stone", take * 0.25)
            if obj.kg <= 0:
                self.map.obj_kind[i] = terrain.OBJ_NONE
                del self.map.objects[i]
            p.learn("mining", eff)
            return
        # No ore: quarry stone instead.
        self.store.add("stone", eff * skill * 0.35)
        p.learn("mining", eff * 0.5)

    def _work_cook(self, p: Pawn, eff: float) -> None:
        # Preserve first when the larder is turning, cook fresh otherwise.
        order = ["cook_meat", "cook_meal"]
        for st in self.store.stacks.values():
            if "food" in st.item.tags and 0 < st.item.shelf_days < 60 \
                    and st.freshness < 0.5:
                order = ["dry_produce", "dry_berries", "preserve"] + order
                break
        for key in order:
            rec = items.RECIPES.get(key)
            if rec is None or not all(self.store.has(k, v) for k, v in rec.inputs):
                continue
            n = eff * p.skill_factor("cooking") / rec.work_min
            if n < 0.05:
                return
            n = min(n, min(self.store.amount(k) / v for k, v in rec.inputs))
            if n <= 0:
                continue
            if rec.heat_mj > 0 and not self._consume_fuel(rec.heat_mj * n):
                return
            for k, v in rec.inputs:
                self.store.take(k, v * n)
            for k, v in rec.outputs:
                self.store.add(k, v * n)
            p.learn("cooking", eff)
            return

    def _work_craft(self, p: Pawn, eff: float) -> None:
        stations = self.stations
        for rec in items.RECIPES.values():
            if rec.station and rec.station not in stations:
                continue
            if rec.skill != "crafting":
                continue
            if not all(self.store.has(k, v) for k, v in rec.inputs):
                continue
            n = eff * p.skill_factor("crafting") / rec.work_min
            if n < 0.02:
                return
            n = min(n, min(self.store.amount(k) / v for k, v in rec.inputs))
            if rec.heat_mj > 0 and not self._consume_fuel(rec.heat_mj * n):
                continue
            for k, v in rec.inputs:
                self.store.take(k, v * n)
            for k, v in rec.outputs:
                self.store.add(k, v * n)
            p.learn("crafting", eff)
            return

    def _work_water(self, p: Pawn, eff: float) -> None:
        """Fetch water. How hard this is depends entirely on the site.

        Beside a river it is carrying: about 2 litres per person-minute, since
        a full yoke is 20 kg and the round trip takes ten minutes. Off water in
        winter it is melting snow, which costs roughly 0.42 MJ a litre -- so a
        colony in a frozen prairie burns firewood to drink. With neither, it
        is digging seeps, and it is slow.
        """
        surface = (self.survey.fresh_water > 0.2 and self.weather.temp_c > -4) \
            or any(b.done and b.d.water_l_day > 0 for b in self.buildings)
        if surface:
            got = eff * 2.0
        elif self.weather.snow_cover_mm > 0 or self.weather.temp_c < 0:
            got = eff * 1.3
            if not self._consume_fuel(got * 0.42):
                got = 0.0
        else:
            got = eff * 0.3
        self.water_l = min(self.water_capacity_l, self.water_l + got)

    def _tick_regrowth(self, minutes: float) -> None:
        """Wild food regrows. Standing stock caps at half a year's production,
        so a stripped territory takes months to be worth walking over again."""
        annual = self.forage_annual_kcal
        if self.forage_stock_kcal < 0:
            self.forage_stock_kcal = annual * 0.4
        t = self.survey.clim.temp_on_day(self.day)
        growth = annual / 365.0 * (0.1 if t < 4 else 1.0) * minutes / MINUTES_PER_DAY
        self.forage_stock_kcal = min(annual * 0.5,
                                     self.forage_stock_kcal + growth)

    def _tick_water(self, minutes: float) -> None:
        gain = self.water_l_day * minutes / MINUTES_PER_DAY
        # Rain fills whatever is out in it.
        if self.weather.raining:
            gain += self.weather.precip_mm * 3.0 * minutes / MINUTES_PER_DAY
        self.water_l = min(self.water_capacity_l, self.water_l + gain)

    # ---- background systems ----

    def _consume_fuel(self, mj: float) -> bool:
        """Burn stored fuel. Returns False if there is not enough."""
        for key in ("charcoal", "coal", "wood", "plank", "oil"):
            it = items.ITEMS[key]
            if it.fuel_mj_kg <= 0:
                continue
            have = self.store.amount(key)
            if have <= 0:
                continue
            need_kg = mj / it.fuel_mj_kg
            got = self.store.take(key, min(have, need_kg))
            mj -= got * it.fuel_mj_kg
            if mj <= 0.01:
                return True
        return mj <= 0.01

    def _burn_fuel(self, minutes: float) -> None:
        """Heating. This is the cost that surprises colonies.

        A poorly insulated shelter in a Dfb winter needs a few hundred
        megajoules a day, which is 20-30 kg of wood -- every day, for months.
        """
        out = self.weather.temp_c
        if out >= 16.0:
            return
        deficit = 16.0 - out
        # Heat loss scales with the temperature difference and the number of
        # sheltered people; insulation divides it.
        occupants = max(1, self.population)
        mj = deficit * occupants * 0.85 / max(0.6, self.insulation)
        mj *= minutes / MINUTES_PER_DAY
        if not self._consume_fuel(mj):
            # No fuel: everyone experiences the outside temperature.
            pass

    def _tick_crops(self, minutes: float) -> None:
        if not self.fields:
            return
        t = self.weather.temp_c
        days = minutes / MINUTES_PER_DAY
        for f in self.fields:
            if not f.crop or f.dead:
                continue
            if t < f.crop.frost_kill_c:
                f.dead = True
                f.crop = None
                self.note(f"frost killed the crop in a {f.area_m2} m2 field")
                continue
            if t > f.crop.base_c:
                f.gdd += (min(30.0, t) - f.crop.base_c) * days
            # Water balance over the season.
            demand = f.crop.water_mm / 120.0 * days
            f.water_deficit_mm = max(0.0, f.water_deficit_mm + demand
                                     - self.weather.precip_mm * days)

    def _tick_stores(self, minutes: float, temp_c: float) -> None:
        cold = any(b.done and b.d.cooled for b in self.buildings)
        store_temp = min(temp_c, 4.0) if cold else temp_c
        lost = self.store.tick_spoilage(minutes, store_temp)
        for key, amount in lost.items():
            # Only worth reporting if it was a meaningful share of the larder.
            if amount * items.ITEMS[key].kcal_kg > 0.15 * max(1.0, self.store.food_kcal()):
                self.note(f"{amount:.0f} kg of {items.ITEMS[key].name} spoiled")

        # Refresh the derived statistics the AI and UI read.
        pop = max(1, self.population)
        daily_kcal = sum(p.daily_kcal_need(temp_c, 0.6) for p in self.alive) or 1.0
        self.stats["food_days"] = self.store.food_kcal() / daily_kcal
        heat_mj_day = max(0.1, (16.0 - self.weather.temp_c)) * pop * 0.85 \
            / max(0.6, self.insulation)
        self.stats["fuel_days"] = min(999.0, self.store.fuel_mj() / heat_mj_day)
        self.stats["population"] = pop
        self.stats["health"] = sum(
            p.capacity("consciousness") for p in self.alive) / pop
        self.stats["morale"] = sum(p.morale for p in self.alive) / pop

    # ---- reporting ----

    def report(self) -> str:
        s = self.stats
        return (
            f"{self.name} -- Y{self.year} D{self.day} "
            f"{self.minute_of_day//60:02d}:{self.minute_of_day%60:02d}\n"
            f"  {self.population} alive ({self.dead_count} lost), "
            f"morale {s.get('morale',0):.0%}, health {s.get('health',0):.0%}\n"
            f"  weather {self.weather.temp_c:+.1f} C"
            f"{' rain' if self.weather.raining else ''}"
            f"{' snow' if self.weather.snowing else ''}"
            f", indoors {self.indoor_temp():+.1f} C\n"
            f"  food {s.get('food_days',0):.0f} days, "
            f"fuel {s.get('fuel_days',0):.0f} days, "
            f"beds {self.beds}/{self.population}\n"
            f"  stores: {self.store.summary()}"
        )
