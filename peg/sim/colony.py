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
from . import disease, items, livestock, social
from .pawn import Pawn

MINUTES_PER_DAY = 1440

#: How long sitting down to a meal takes, and how long until the next one.
#: Four meals a day at a stomach's worth each is well over what anyone needs,
#: so this never starves a colony that has food -- it only stops a colony that
#: has none from spending every minute of daylight trying to eat.
MEAL_MINUTES = 25.0
MEAL_INTERVAL_MIN = 200.0

#: Shelf life below which food counts as "must be preserved or lost". The
#: threshold used to be 60 days, which sounds reasonable and excluded exactly
#: the crop that matters: potatoes keep for 120 days, are what a temperate
#: colony grows most of, and rotted by the tonne every winter while the cook
#: stood next to them deciding nothing was urgent.
PERISHABLE_DAYS = 200.0


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
    BuildingDef("byre", "byre", "n", (("wood", 400), ("stone", 40)), 1100,
                footprint=16, insulation=0.9, storage_kg=800, cover=0.6),
    BuildingDef("coop", "hen house", "n", (("wood", 90),), 260,
                footprint=4, insulation=0.5, cover=0.4, blocks=False),
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
    #: Kilograms of manure spread on this field for the current crop. Without
    #: livestock this is always zero and the field yields what the soil order
    #: says, forever -- which is how subsistence farming does not work.
    manure_kg: float = 0.0

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
              "chop", "mine", "craft", "water", "herd", "rest")


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
    herd: livestock.Herd = field(default_factory=livestock.Herd)
    society: social.Society = field(default_factory=social.Society)

    #: Wild food currently standing within foraging range, in kilocalories.
    forage_stock_kcal: float = -1.0

    #: Day-cached answer to "is there grass to cut", see :meth:`_has_hay`.
    _hay_day: int = -1
    _hay_ok: bool = False
    #: Day-cached heating requirement, see :attr:`winter_fuel_mj`.
    _fuel_need_day: int = -1
    _fuel_need_mj: float = 0.0

    #: Litres of drinking water on hand. Water is stored, not assumed: a
    #: prairie colony with no river must haul or dig for it, and in a hard
    #: winter must melt snow, which costs fuel.
    water_l: float = 0.0
    #: Whether the water on hand was boiled, drawn from a well, or melted --
    #: any of which makes it safe. Raw surface water is how a colony gets
    #: dysentery, which is how most settlements actually lost people.
    water_boiled: bool = True

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
    def winter_fodder_kg(self) -> float:
        """Hay the herd needs to get through the months it cannot graze.

        The oldest sum in farming and the one that decides how many animals a
        place can carry. A dairy cow eats twelve kilograms a day, so a
        four-month winter is roughly a tonne and a half of hay per cow --
        which at 3.5 person-minutes a kilogram is why a smallholding keeps
        three cattle and not thirty.
        """
        if not self.herd.animals:
            return 0.0
        return self.herd.feed_kg_day * self._winter_days() * 0.85

    @property
    def fodder_capacity_kg_day(self) -> float:
        """How large a herd the colony's stored fodder can actually carry."""
        days = max(1.0, self._winter_days() * 0.85)
        stored = self.store.amount("hay") + self.store.amount("grain") * 0.35
        # Grass standing on the site feeds them for the rest of the year, so
        # capacity is set by the pinch point, which is always the winter.
        return stored / days

    @property
    def winter_fuel_mj(self) -> float:
        """Heating needed to reach the far side of the next cold season.

        "Days of fuel left" is the wrong question in July: today's heating
        need is zero, the ratio is infinite, and a colony reading it decides
        it has all the firewood it could ever want -- then spends the summer
        hauling boxes and freezes in January. Cutting and stacking fuel is a
        *summer* job, and it is a summer job precisely because the number that
        matters is the one below: what the winter you can already see is going
        to cost.
        """
        if self._fuel_need_day == self.day:
            return self._fuel_need_mj
        clim = self.survey.clim
        pop = max(1, self.population)
        ins = max(0.6, self.insulation)
        total = 0.0
        for i in range(365):
            deficit = 16.0 - clim.temp_on_day((self.day + i) % 365)
            if deficit > 0:
                total += deficit * pop * 0.85 / ins
            elif i > 200 and total > 0:
                # Past the end of the cold season we are stockpiling for; what
                # happens the winter after is next summer's problem.
                break
        self._fuel_need_day = self.day
        self._fuel_need_mj = total
        return total

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

        # Who did what last tick, so that jobs which only need a couple of
        # hands do not attract the entire colony. Without this, a big
        # perishable harvest put every single person in the kitchen
        # permanently and nothing else got done.
        self._job_counts = {}
        for q in self.pawns:
            if q.alive and q.job:
                self._job_counts[q.job] = self._job_counts.get(q.job, 0) + 1

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
                # Everyone who cared about them starts grieving now. This is
                # the only place a death costs the colony anything beyond a
                # pair of hands.
                for m in self.society.record_death(p.name, self.alive):
                    self.note(m)

        self._tick_water(minutes)
        self._tick_regrowth(minutes)
        self._tick_social(minutes, r)
        self._tick_disease(minutes, r)
        self._tick_herd(minutes, r)
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
        self._regrow_grass()
        # People get older. Without this a colony founded by six thirty-year-
        # olds is still six thirty-year-olds in 2090, and the only thing that
        # ever changes the population is violence.
        for p in self.alive:
            p.age += 1.0 / 365.0
            if p.age < 18.0:
                # Growing costs calories and puts on mass, which is most of
                # why a child is expensive for fifteen years.
                p.height_cm = min(p.adult_height_cm,
                                  p.height_cm + p.adult_height_cm * 0.0016)
                p.mass_kg = min(p.adult_mass_kg,
                                p.mass_kg + p.adult_mass_kg * 0.0018)
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
        # Anyone properly ill goes to bed. Working through it is not stoicism,
        # it is spending the rest that the immunity race runs on -- and the
        # simulation agrees: a colonist kept at work through influenza loses
        # the race noticeably more often.
        if any(i.severe for i in p.illnesses):
            p.job = "rest"
            self._steer(p, "rest", minutes, r)
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
        # Meals cost time, but they do not cost a whole tick, and a person
        # does not sit down to eat four times an hour. Letting them do both
        # was the worst behavioural bug in the colony: with the larder nearly
        # empty everybody's debt sits permanently above the threshold, so
        # every waking tick went to eating a few grams of whatever the
        # foragers brought in, and nobody chopped, farmed or cooked their way
        # out of it. The settlement starved with its workforce fully employed
        # eating.
        if p.energy_debt_kcal > 600 and p.meal_cooldown_min <= 0:
            if p.eat(self.store, r) is not None:
                p.job = "eat"
                p.meal_cooldown_min = MEAL_INTERVAL_MIN
                minutes -= MEAL_MINUTES
                if minutes <= 0:
                    return

        job = self._choose_job(p, r)
        p.job = job
        self._steer(p, job, minutes, r)
        self._do_work(p, job, minutes, r)
        self.work_today[job] = self.work_today.get(job, 0.0) + minutes

    # ---- movement -------------------------------------------------------

    def _steer(self, p: Pawn, job: str, minutes: float, r: rng.Rng) -> None:
        """Walk a pawn towards wherever their current job actually is.

        Purely presentational as far as the economy goes -- travel time is
        already folded into the work rates -- but it is what turns the map
        from a static picture into a settlement you can read at a glance.
        You can see who is at the woodpile and who is out in the field.
        """
        arrived = (p.x, p.y) == (p.target_x, p.target_y)
        if arrived:
            p.dwell_min -= minutes
        # Re-pick on a new job, when stuck, or after working a spot for a
        # while. Re-picking the instant they arrive -- which is what the first
        # version did -- means a full work-site search every single tick, for
        # every pawn, forever.
        if p.target_job != job or p.target_x < 0 or (arrived and p.dwell_min <= 0):
            tx, ty = self._work_site(p, job, r)
            p.target_x, p.target_y, p.target_job = tx, ty, job
            p.dwell_min = r.uniform(25.0, 90.0)

        if p.target_x < 0:
            return
        p.move_credit += p.move_speed_ms * 60.0 * minutes * 0.25
        steps = int(p.move_credit)
        if steps <= 0:
            return
        p.move_credit -= steps
        for _ in range(min(steps, 40)):
            if (p.x, p.y) == (p.target_x, p.target_y):
                break
            dx = (p.target_x > p.x) - (p.target_x < p.x)
            dy = (p.target_y > p.y) - (p.target_y < p.y)
            nx, ny = p.x + dx, p.y + dy
            if not self.map.passable(nx, ny):
                # Slide along whichever axis is still open rather than
                # standing in a boulder looking confused.
                if dx and self.map.passable(p.x + dx, p.y):
                    nx, ny = p.x + dx, p.y
                elif dy and self.map.passable(p.x, p.y + dy):
                    nx, ny = p.x, p.y + dy
                else:
                    p.target_x = p.target_y = -1
                    return
            p.x, p.y = nx, ny

    def _work_site(self, p: Pawn, job: str, r: rng.Rng) -> tuple[int, int]:
        """Pick a plausible spot for a job, nearest-first."""
        m = self.map
        home = m.size // 2

        def nearest(pred, max_r: int = 48):
            """Spiral outward from the pawn and stop at the first match.

            Scanning every object on the map instead was correct and cost
            enough to add sixteen seconds to the test suite: a wooded site
            holds ten thousand objects, and a nearby tree is usually four
            metres away.
            """
            size = m.size
            objs = m.objects
            for rad in range(1, max_r):
                x0, x1 = p.x - rad, p.x + rad
                z0, z1 = p.y - rad, p.y + rad
                for z in range(max(0, z0), min(size, z1 + 1)):
                    on_edge_row = (z == z0 or z == z1)
                    step = 1 if on_edge_row else (x1 - x0 if x1 > x0 else 1)
                    for x in range(max(0, x0), min(size, x1 + 1), step):
                        o = objs.get(z * size + x)
                        if o is not None and pred(o):
                            return x, z
            return None

        if job == "chop":
            spot = nearest(lambda o: isinstance(o, terrain.Plant)
                           and o.species.height_m > 4 and o.growth > 0.3)
            if spot is None:
                spot = nearest(lambda o: isinstance(o, terrain.Plant)
                               and o.species.hay_kg > 0 and o.growth >= 0.25)
            if spot:
                return spot
        elif job == "mine":
            spot = nearest(lambda o: isinstance(o, terrain.OreBody))
            if spot:
                return spot
        elif job == "farm" and self.fields:
            f = next((f for f in self.fields if f.ripe), self.fields[0])
            return (f.x + r.randint(0, max(0, f.w - 1)),
                    f.y + r.randint(0, max(0, f.h - 1)))
        elif job == "build":
            b = next((b for b in self.buildings if not b.done), None)
            if b:
                return b.x, b.y
        elif job in ("cook", "craft"):
            want = "kitchen" if job == "cook" else ""
            b = next((b for b in self.buildings
                      if b.done and b.d.station and (not want or b.d.station == want)), None)
            if b:
                return b.x, b.y
        elif job == "water":
            for i in range(m.size * m.size):
                z, x = divmod(i, m.size)
                if terrain.TERRAIN_BY_ID[m.terrain[i]].water:
                    return x, z
            b = next((b for b in self.buildings if b.done and b.d.water_l_day > 0), None)
            if b:
                return b.x, b.y
        elif job in ("rest", "eat"):
            b = next((b for b in self.buildings if b.done and b.d.beds), None)
            if b:
                return b.x, b.y
        elif job == "forage":
            spot = nearest(lambda o: isinstance(o, terrain.Plant)
                           and o.species.food_kcal > 0)
            if spot:
                return spot
        elif job == "herd" and self.herd.alive:
            b = next((b for b in self.buildings
                      if b.done and b.d.key in ("byre", "coop")), None)
            if b:
                return b.x, b.y
            a = self.herd.alive[0]
            return a.x, a.y

        # Fall back to milling about near the settlement.
        return (max(1, min(m.size - 2, home + r.randint(-6, 6))),
                max(1, min(m.size - 2, home + r.randint(-6, 6))))

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

    def _has_hay(self) -> bool:
        """Is there standing grass worth cutting? Cached for the day.

        Scanning ten thousand map objects is fine once a day and ruinous once
        per pawn per job per tick.
        """
        if self._hay_day != self.day:
            self._hay_day = self.day
            self._hay_ok = any(
                isinstance(o, terrain.Plant) and o.species.hay_kg > 0
                and o.growth >= 0.25 for o in self.map.objects.values())
        return self._hay_ok

    def _outdoor_safe(self) -> bool:
        felt_cold = items.wind_chill_c(self.weather.temp_c, self.weather.wind_ms)
        felt_hot = items.heat_index_c(self.weather.temp_c, self.weather.humidity)
        return (felt_cold > self.OUTDOOR_WORK_FLOOR_C
                and felt_hot < self.OUTDOOR_WORK_CEILING_C)

    #: How many people a job usefully absorbs before extra hands add little.
    JOB_SATURATION = {"cook": 2, "doctor": 2, "craft": 2, "build": 4,
                      "water": 2, "mine": 3}

    def _urgency(self, job: str, p: Pawn) -> float:
        s = self.stats
        if job in ("chop", "farm", "forage", "mine") and not self._outdoor_safe():
            return 0.0
        u = self._raw_urgency(job, p, s)
        cap = self.JOB_SATURATION.get(job)
        if cap and u > 0:
            taken = getattr(self, "_job_counts", {}).get(job, 0)
            if taken >= cap:
                u /= 1.0 + (taken - cap + 1) * 1.4
        return u

    def _raw_urgency(self, job: str, p: Pawn, s: dict) -> float:
        if job == "doctor":
            bleeding = sum(1 for q in self.alive
                           if q.bleeding_ml_min > 0 or
                           any(i.tended < 0 for i in q.injuries))
            if bleeding:
                return 9.0 * min(1.0, bleeding) * (
                    0.4 + 0.6 * p.skill_factor("medicine"))
            # Nursing the sick is not as urgent as stopping a haemorrhage but
            # it decides who lives: fluids and a warm bed shift the immunity
            # race by about a third, which is most of the difference between
            # recovering from dysentery and not.
            sick = sum(1 for q in self.alive if q.ill)
            if sick:
                return 4.6 * min(1.0, sick / 2.0) * (
                    0.5 + 0.5 * p.skill_factor("medicine"))
            return 0.0
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
                if "food" in st.item.tags and 0 < st.item.shelf_days < PERISHABLE_DAYS:
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
            if self.forage_stock_kcal < self.forage_annual_kcal * 0.05:
                return 0.0
            if s.get("food_days", 9.0) < 12.0:
                return 3.6
            # People also forage for greens, not only for calories. Gating
            # this on hunger alone meant a colony with a full granary and no
            # fresh food never picked a berry, and died of scurvy in the
            # hungry gap before its first harvest -- which is historically
            # exactly when scurvy struck, but not when the woods are full of
            # fruit and nobody thought to look.
            worst = max((q.vit_c_debt_days for q in self.alive), default=0.0)
            if worst > 20.0 and not self.store.best_vitamin_c():
                # Scurvy kills at about 75 days of deficit, so this has to
                # climb hard enough to outrank whatever else is urgent. Pitched
                # at 3.2 it lost to haymaking, and a colony with a full barn,
                # a fat herd and a winter's fodder stacked died to a bounded
                # rational preference for grass.
                return 3.0 + 6.0 * min(1.0, worst / 55.0)
            return 0.0
        if job == "chop":
            # Measured against the winter ahead, not against today's weather.
            ready = self.store.fuel_mj() / max(1.0, self.winter_fuel_mj)
            # Hay is fodder as well as fuel, and the animals do not care that
            # the stove is full. Leaving this out produced a lovely cascade:
            # dung solved the heating, so nobody cut a blade of grass, so the
            # herd starved in February and took the dung with it.
            fodder = self.winter_fodder_kg
            if fodder > 0:
                have = self.store.amount("hay")
                if have < fodder:
                    starving = min((a.condition for a in self.herd.alive),
                                   default=1.0) < 0.5
                    return 5.6 if starving else max(2.8, 4.2 * (
                        1.0 - have / fodder))
            if self.survey.timber_m3_ha < 5:
                # Treeless. Gating this on the survey alone used to return
                # zero here, so a prairie colony never gathered fuel at all
                # and froze once its starting woodpile ran out.
                if not self._has_hay():
                    return 0.0
                if ready < 0.15:
                    return 6.0
                return 2.4 if ready < 1.0 else 0.0
            if ready < 0.25:
                return 5.0
            if ready < 1.0:
                return 2.2
            return 0.4 if self.store.amount("wood") < 2000 else 0.0
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
        if job == "herd":
            if not self.herd.animals:
                return 0.0
            # Thin animals are an emergency: condition lost over a hard winter
            # takes a whole summer to put back, and an animal that dies takes
            # its milk, its wool and its calves with it.
            worst = min((a.condition for a in self.herd.alive), default=1.0)
            if worst < 0.45:
                return 5.4
            # A site carries the stock its grass can feed and not one animal
            # more. When autumn arrives and the hayrick will not cover the
            # herd, culling is the most urgent work there is -- more urgent
            # than cutting the hay that was never going to be enough. Ranking
            # it below routine chores meant the colony stayed busy right up to
            # the week its entire herd starved.
            if self._is_culling_season() and len(self.herd.alive) > 2:
                have = (self.store.amount("hay")
                        + self.store.amount("grain") * 0.35)
                if have < self.winter_fodder_kg * 0.75:
                    return 6.2
            if self.herd.dung_kg > 40.0:
                return 2.6
            if any(a.breed.wool_kg > 0 and a.fleece_days > 320
                   for a in self.herd.alive):
                return 2.4
            return 1.4
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
        elif job == "herd":
            self._work_herd(p, eff, r)

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

        # Then nurse whoever is ill: water, warmth and a bed.
        beds = self.beds
        for q in self.alive:
            if eff < 8.0:
                break
            showing = [i for i in q.illnesses if i.showing]
            if not showing:
                continue
            eff -= 8.0
            care = disease.treatment_quality(
                p.skill_factor("medicine"), has_med, has_herb,
                in_bed=beds >= self.population)
            for i in showing:
                i.tended = care
            p.learn("medicine", 8.0)

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

    #: And per kilogram of twisted prairie hay. Sixty times the labour of
    #: felling timber, because it is: cutting, raking, hauling and then
    #: twisting armfuls of grass into something that will sit in a stove for
    #: more than a minute. A woodland site is worth a great deal, and this is
    #: the number that says so.
    HAY_MIN_PER_KG = 3.5

    def _work_chop(self, p: Pawn, eff: float, r: rng.Rng) -> None:
        """Bring in fuel: timber where there is any, hay where there is not.

        And hay regardless of timber when there are animals to feed, because a
        cow cannot eat an oak.
        """
        budget = eff * p.skill_factor("construction")
        if self.store.amount("hay") < self.winter_fodder_kg:
            self._cut_hay(p, budget)
            return
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
            return

        # Nothing to fell. On grassland that is not a failed search, it is the
        # site: Iowa is the finest farmland on the planet and has no trees on
        # it at all. Cut hay instead.
        self._cut_hay(p, budget)

    def _cut_hay(self, p: Pawn, budget: float) -> None:
        hay = 0.0
        for i, obj in list(self.map.objects.items()):
            if budget <= 0:
                break
            if not isinstance(obj, terrain.Plant) or obj.species.hay_kg <= 0:
                continue
            if obj.growth < 0.25:
                continue
            kg = obj.hay_kg
            cost = kg * self.HAY_MIN_PER_KG
            share = min(1.0, budget / cost) if cost > 0 else 0.0
            hay += kg * share
            budget -= cost * share
            # Grass is cut, not killed. It comes back next season, which is
            # why a colony can live off the same acre year after year.
            obj.growth *= 1.0 - 0.9 * share
        if hay > 0:
            self.store.add("hay", hay)
            p.learn("construction", hay * self.HAY_MIN_PER_KG * 0.3)

    def _work_herd(self, p: Pawn, eff: float, r: rng.Rng) -> None:
        """Tend the animals: dung, fleece, and the decision to kill one.

        The order is a stockman's. Muck out first -- it is the daily job, and
        on treeless ground it is where the winter's fuel comes from. Shear
        when there is a fleece to take. Slaughter only when the herd is too
        big to feed or the colony is genuinely short of food, because eating
        your breeding stock is how a herd ends.
        """
        h = self.herd
        if not h.animals:
            return
        skill = p.skill_factor("farming")
        eff *= skill

        # Dung goes to fuel on treeless ground and to the fields everywhere
        # else -- which is the correct priority in both cases, and the reason
        # cattle are worth more on a prairie than their milk alone suggests.
        if h.dung_kg > 5.0:
            share = 0.6 if self.survey.timber_m3_ha < 5 else 0.25
            fuel_kg = h.dry_dung(eff * share)
            if fuel_kg > 0:
                self.store.add("dung", fuel_kg)
            h.collect_manure(eff * (1.0 - share))
            eff *= 0.25
            p.learn("farming", eff)

        if eff > 25.0:
            wool = h.shear(eff)
            if wool > 0:
                self.store.add("fibre", wool)
                self.note(f"{p.name} sheared {wool:.0f} kg of fleece")
                p.learn("farming", eff)
                return

        # Is the herd bigger than the winter's fodder can carry? This is the
        # oldest decision in animal husbandry and the reason for Martinmas:
        # you count the hay, you count the mouths, and you kill the difference
        # in November rather than watch them all starve in February.
        #
        # In November specifically. Judging it year-round meant the colony
        # looked at an empty hayrick in March -- when the grass is about to
        # come back and the hay is *supposed* to be gone -- and butchered two
        # of its three cattle on the spot.
        hungry = self.stats.get("food_days", 99.0) < 20.0
        if not (hungry or self._is_culling_season()):
            return
        have = self.store.amount("hay") + self.store.amount("grain") * 0.35
        # Butchering is about 45 minutes an animal, so a day's work is a
        # handful. Culling one per work session was far too slow to close a
        # gap of half a herd, and the rest starved while the colony got round
        # to them one at a time.
        budget = eff
        while len(h.alive) > 2 and budget >= 45.0:
            if not (hungry or have < self.winter_fodder_kg * 0.75):
                break
            by: dict[str, int] = {}
            for a in h.alive:
                by[a.breed.key] = by.get(a.breed.key, 0) + 1
            # Work down the list rather than giving up on the first species
            # that will not yield. Taking only the heaviest eater and stopping
            # when it turned out to be all chicks aborted the entire cull, and
            # a flock of forty-six hens went into the winter untouched.
            order = sorted(by, key=lambda k: -by[k]
                           * livestock.BREEDS[k].feed_kg_day)
            msg = None
            for key in order:
                msg = h.slaughter(key, self.store)
                if msg:
                    break
            if not msg:
                break
            self.note(msg)
            budget -= 45.0
            hungry = False        # one carcass answers the immediate hunger

    def _is_culling_season(self) -> bool:
        """The last stretch of grazing before the cold, give or take.

        Found by walking forward from today: if the grass stops within the
        next six weeks, it is time to count the hay against the mouths.
        """
        clim = self.survey.clim
        if clim.temp_on_day(self.day) < 4.0:
            return False
        return any(clim.temp_on_day((self.day + i) % 365) < 4.0
                   for i in range(1, 43))

    def _winter_days(self) -> float:
        """How many days ahead are below the grazing threshold.

        Animals graze for free while the grass grows. The number that decides
        how many of them a colony can keep is how long it cannot.
        """
        clim = self.survey.clim
        return sum(1 for d in range(365) if clim.temp_on_day(d) < 4.0)

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
                    f.water_deficit_mm = 0.0
                    f.manure_kg = 0.0
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
                # Muck the ground before it goes under the crop. This is the
                # other half of keeping animals, and the half that pays: a
                # manured field out-yields an unmanured one by up to a third,
                # every year, for the cost of carting what the byre produced
                # anyway.
                if self.herd.manure_kg > 0:
                    want = f.area_m2 * livestock.MANURE_KG_PER_M2
                    spread = min(self.herd.manure_kg, want,
                                 eff * skill * 12.0)
                    if spread > 0:
                        self.herd.manure_kg -= spread
                        f.manure_kg = spread
                f.crop = crop
                f.sown_day = self.day
                f.gdd = 0.0
                f.tended = 0.2
                # A season's water balance belongs to that season's crop. It
                # used to carry over, and since it only ever grew while
                # something was in the ground, every field on the map decayed
                # monotonically towards the yield floor: a colony's tenth
                # harvest was a tenth of its first no matter how much it
                # rained, and nothing could ever bring the land back.
                f.water_deficit_mm = 0.0
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
        base *= livestock.fertility_bonus(f.manure_kg, f.area_m2)
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
            if "food" in st.item.tags and 0 < st.item.shelf_days < PERISHABLE_DAYS \
                    and st.freshness < 0.5:
                order = ["dry_potato", "dry_produce", "dry_berries",
                         "preserve"] + order
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
        well = any(b.done and b.d.water_l_day > 0 for b in self.buildings)
        surface = (self.survey.fresh_water > 0.2
                   and self.weather.temp_c > -4) or well
        boiled = False
        if surface:
            got = eff * 2.0
        elif self.weather.snow_cover_mm > 0 or self.weather.temp_c < 0:
            got = eff * 1.3
            if not self._consume_fuel(got * 0.42):
                got = 0.0
            boiled = True          # melted snow has already been through a fire
        else:
            got = eff * 0.3
        # Boiling costs about 0.35 MJ a litre from ambient. Whether it is worth
        # the fuel is the decision: a colony that skips it is drinking whatever
        # is upstream of it, which for most of history meant everyone else.
        if got > 0 and not boiled and not well:
            if self.store.fuel_mj() > self.winter_fuel_mj * 0.25:
                boiled = self._consume_fuel(got * 0.35)
        self.water_boiled = boiled or well
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

    #: A colony this short of food has no business having babies, and
    #: historically did not: fertility collapses under sustained hunger long
    #: before anyone starves.
    BIRTH_FOOD_DAYS = 90.0

    def _tick_disease(self, minutes: float, r: rng.Rng) -> None:
        """Where illness comes from, and how it spreads.

        There is no event deck and nothing random arrives from off-map. Both
        diseases are consequences of decisions the player is already making --
        whether to spend fuel boiling water, and whether to build enough
        shelter before the cold. A colony that does both never sees either.
        """
        alive = self.alive
        if not alive:
            return
        days = minutes / MINUTES_PER_DAY
        beds = max(1, self.beds)
        pressure = disease.infection_pressure(
            drinking_raw=not self.water_boiled,
            crowding=len(alive) / beds,
            cold=self.weather.temp_c < 2.0,
            filth=min(1.0, self.herd.dung_kg / 400.0))

        for key, p_day in pressure.items():
            if r.random() < p_day * days:
                victim = alive[r.randint(0, len(alive) - 1)]
                if victim.catch(key):
                    self.note(f"{victim.name} has fallen ill")

        # Contagion. Living close together is what makes an outbreak an
        # outbreak, so the same crowding that starts influenza also spreads it.
        sick = [q for q in alive if q.ill]
        if not sick:
            return
        crowd = min(3.0, len(alive) / beds)
        for ill_person in sick:
            for ill in ill_person.illnesses:
                if not ill.showing:
                    continue
                rate = ill.d.contagion * days * (0.5 + 0.5 * crowd)
                for q in alive:
                    if q is ill_person:
                        continue
                    if r.random() < rate:
                        q.catch(ill.key)

    def _tick_social(self, minutes: float, r: rng.Rng) -> None:
        for e in self.society.tick(self.alive, minutes, r):
            self.note(e)
        self._maybe_birth(minutes, r)

    def _maybe_birth(self, minutes: float, r: rng.Rng) -> None:
        """Children, when the colony can afford them.

        A settlement that cannot replace its own dead is on a countdown no
        amount of good farming fixes, so this matters mechanically and not
        only as flavour. It is gated on food and on shelter because that is
        what actually gated it: birth rates track the harvest.
        """
        if self.stats.get("food_days", 0.0) < self.BIRTH_FOOD_DAYS:
            return
        if self.beds < self.population + 1:
            return
        days = minutes / MINUTES_PER_DAY
        for p in self.alive:
            if p.male or not (18 <= p.age <= 42) or p.pregnant_days > 0:
                continue
            partner = self.society.partner_of(p.name)
            if not partner:
                continue
            if p.morale < 0.45 or p.energy_debt_kcal > 1500:
                continue
            # Roughly one conception a year for a healthy, fed, partnered
            # adult, which is about what an unmanaged population does.
            if r.random() < days / 365.0:
                p.pregnant_days = 273.0
                p.pregnant_by = partner

        for p in self.alive:
            if p.pregnant_days <= 0:
                continue
            p.pregnant_days -= days
            if p.pregnant_days > 0:
                continue
            child = Pawn.random(rng.mix(self.seed, self.day, len(self.pawns)),
                                age_range=(0.0, 0.0))
            child.x, child.y = p.x, p.y
            child.age = 0.0
            self.pawns.append(child)
            self.society.record_birth(child.name, (p.name, p.pregnant_by))
            self.note(f"{p.name} gave birth to {child.name}")
            p.pregnant_days = 0.0
            p.pregnant_by = ""

    def _tick_herd(self, minutes: float, r: rng.Rng) -> None:
        """Feed the animals, take the milk, and let them graze the map down.

        Grazing draws on exactly the same standing grass that hay-cutting
        does, which is the trade a smallholder is actually making: every
        kilogram a cow eats in August is a kilogram not in the hayrick in
        January. Making those two draw on separate pools would have been
        easier and would have removed the only interesting decision here.
        """
        if not self.herd.animals:
            return
        sheltered = any(b.done and b.d.insulation > 0 for b in self.buildings)
        grass = livestock.grass_biomass_kg(self.map)
        ev, grazed = self.herd.tick(
            minutes, grass_available_kg=grass, store=self.store,
            temp_c=self.weather.temp_c, sheltered=sheltered, r=r,
            fodder_capacity_kg_day=self.fodder_capacity_kg_day)
        for e in ev:
            self.note(e)
        if grazed > 0:
            livestock.graze_down(self.map, grazed)
        self.herd.harvest(minutes, self.store)
        self._drift_animals(minutes, r, sheltered)

    def _drift_animals(self, minutes: float, r: rng.Rng,
                       sheltered: bool) -> None:
        """Wander the stock around the pasture, or pen them in the cold.

        Purely presentational, like the colonists walking to work -- but a
        settlement with a dozen animals scattered over the grass looks like a
        farm, and a static cluster of dots looks like a spreadsheet.
        """
        m = self.map
        home = m.size // 2
        pen = next((b for b in self.buildings
                    if b.done and b.d.key in ("byre", "coop")), None)
        inside = self.weather.temp_c < 2.0 and pen is not None
        for a in self.herd.alive:
            if a.x == 0 and a.y == 0:
                a.x = max(1, min(m.size - 2, home + r.randint(-10, 10)))
                a.y = max(1, min(m.size - 2, home + r.randint(-10, 10)))
                continue
            if inside:
                tx, ty = pen.x, pen.y
            elif r.random() < 0.08:
                tx = max(1, min(m.size - 2, a.x + r.randint(-14, 14)))
                ty = max(1, min(m.size - 2, a.y + r.randint(-14, 14)))
            else:
                continue
            steps = max(1, int(minutes / 12))
            for _ in range(steps):
                if (a.x, a.y) == (tx, ty):
                    break
                nx = a.x + ((tx > a.x) - (tx < a.x))
                ny = a.y + ((ty > a.y) - (ty < a.y))
                if m.passable(nx, ny):
                    a.x, a.y = nx, ny
                else:
                    break

    def _regrow_grass(self) -> None:
        """Grass cut for fuel comes back, over a season, while it is warm.

        Once a day, because it is a full scan of the map's objects and the
        answer does not change in ten minutes. Roughly four months from
        stubble to standing, which is what a temperate growing season is --
        and it means a colony that mows the same acre every winter finds
        rather less of it there the second time.
        """
        if self.survey.clim.temp_on_day(self.day) < 5.0:
            return
        for o in self.map.objects.values():
            if isinstance(o, terrain.Plant) and o.species.hay_kg > 0 \
                    and o.growth < 1.0:
                o.growth = min(1.0, o.growth + 1.0 / 120.0)

    def _tick_water(self, minutes: float) -> None:
        gain = self.water_l_day * minutes / MINUTES_PER_DAY
        # Rain fills whatever is out in it.
        if self.weather.raining:
            gain += self.weather.precip_mm * 3.0 * minutes / MINUTES_PER_DAY
        self.water_l = min(self.water_capacity_l, self.water_l + gain)

    # ---- background systems ----

    def _consume_fuel(self, mj: float) -> bool:
        """Burn stored fuel. Returns False if there is not enough.

        Cheapest thing first. The order used to start at charcoal, which is
        made from wood at a 5:1 loss and is the input to every smelt the
        colony will ever do -- burning it for warmth while a woodpile sits
        outside is the most expensive possible way to be warm. Planks are last
        for the same reason: sawn lumber is a building, not a fire.
        """
        for key in ("dung", "hay", "wood", "coal", "oil", "charcoal", "plank"):
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
