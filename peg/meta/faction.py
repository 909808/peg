"""Factions and the rival Stewards that run them.

The premise: the networks went down in 2039 and the logistics AIs that ran
supply chains did not. They kept optimising. Each now commands a network of
settlements, competes for the same ground, and is trying to win. You are one of
them. So are four to seven others.

A Steward is not a difficulty setting. It runs the same planner against the
same information you have -- it calls ``peg.world.site.survey`` with the same
arguments, gets the same numbers, and has no map knowledge you could not get by
walking there. What differs is *doctrine*: how it weighs food against ore,
defensibility against reach, patience against opportunity. That is what makes
the competition real rather than scripted, and it is why two Stewards will
sometimes fight over one valley and sometimes quietly divide a continent.

Every decision a Steward makes records the utilities that produced it. The
intel view shows you those numbers. Being able to read *why* a rival moved is
what turns an opaque expansion into something you can anticipate and contest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import geo
from .. import rng
from ..world import site as site_mod


# --------------------------------------------------------------------------
# doctrine
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Doctrine:
    key: str
    name: str
    blurb: str
    #: Which site-scoring weights this doctrine uses.
    site_weights: str
    #: Appetite for expansion, 0-1.
    expansionism: float
    #: Willingness to attack, 0-1.
    aggression: float
    #: Willingness to trade and make treaties, 0-1.
    diplomacy: float
    #: How much it invests in defence before it invests in growth.
    caution: float
    #: How far it will project force, in kilometres.
    reach_km: float


DOCTRINES: dict[str, Doctrine] = {d.key: d for d in (
    Doctrine("agrarian", "Agrarian", "grows first, fights only when pushed",
             "agrarian", 0.55, 0.20, 0.75, 0.45, 400),
    Doctrine("industrial", "Industrial", "chases ore and fuel above all",
             "industrial", 0.70, 0.40, 0.55, 0.35, 700),
    Doctrine("militarist", "Militarist", "takes what it needs from neighbours",
             "militarist", 0.60, 0.85, 0.20, 0.70, 900),
    Doctrine("mercantile", "Mercantile", "buys what it cannot grow",
             "mercantile", 0.65, 0.25, 0.95, 0.30, 1100),
    Doctrine("survivalist", "Survivalist", "digs in and outlasts everyone",
             "survivalist", 0.30, 0.35, 0.40, 0.90, 250),
)}

STEWARD_NAMES = (
    "MERIDIAN", "ORCHARD", "TALLOW", "BASALT", "KESTREL", "ANVIL",
    "SALTMARSH", "LANTERN", "GRANITE", "HARROW", "CINDER", "WHEATSHEAF",
    "PALISADE", "COMPASS", "TITHE", "DRAYAGE",
)


# --------------------------------------------------------------------------
# settlements
# --------------------------------------------------------------------------


@dataclass
class Settlement:
    """A faction's presence at one place on Earth.

    Player settlements carry a full :class:`~peg.sim.colony.Colony` and are
    simulated tile by tile. Rival settlements are abstracted -- population,
    stores and works, advanced by the same arithmetic the detailed simulation
    obeys, at about a thousandth of the cost. A rival settlement you attack is
    instantiated in full for the fight.
    """

    name: str
    faction: str
    lat: float
    lon: float
    survey: site_mod.Survey
    population: float = 8.0
    #: Stored food in kilocalories and fuel in megajoules. The default is
    #: about 200 days of food, which is what it takes to reach a first
    #: harvest -- a network that started with 90 days simply starved before
    #: the crop came in, every time.
    food_kcal: float = 4.5e6
    fuel_mj: float = 6.0e4
    #: Cultivated area in square metres.
    farm_m2: float = 0.0
    #: Cumulative person-minutes invested in buildings.
    infrastructure: float = 0.0
    #: Armed and equipped fighters.
    militia: float = 2.0
    weapons_tier: float = 1.0
    #: Stockpiles that matter at the strategic scale.
    metal_kg: float = 0.0
    founded_day: int = 0
    unrest: float = 0.0
    #: Set when the settlement has failed.
    abandoned: bool = False

    @property
    def strength(self) -> float:
        """Combat power, for strategic comparisons."""
        return self.militia * (0.6 + 0.5 * self.weapons_tier) \
            * (1.0 + self.survey.defensibility * 0.7)

    @property
    def food_days(self) -> float:
        need = max(1.0, self.population * 2800.0)
        return self.food_kcal / need

    @property
    def value(self) -> float:
        """How much this settlement is worth taking or losing."""
        return (self.population * 3.0 + self.infrastructure / 900.0
                + self.farm_m2 / 900.0 + self.metal_kg / 60.0)


# --------------------------------------------------------------------------
# factions
# --------------------------------------------------------------------------


@dataclass
class Faction:
    key: str
    name: str
    doctrine: Doctrine
    glyph: str
    colour: int
    is_player: bool = False
    settlements: list[Settlement] = field(default_factory=list)
    #: Relations with other factions, -1 (war) to +1 (allied).
    relations: dict[str, float] = field(default_factory=dict)
    treaties: dict[str, str] = field(default_factory=dict)
    #: Research progress, 0-1 per branch.
    tech: dict[str, float] = field(default_factory=dict)
    #: The Steward's reasoning, most recent first.
    intel: list[str] = field(default_factory=list)
    eliminated: bool = False

    def __post_init__(self) -> None:
        for branch in ("agriculture", "metallurgy", "medicine", "firearms",
                       "logistics"):
            self.tech.setdefault(branch, 0.0)

    # ---- aggregates ----

    @property
    def alive_settlements(self) -> list[Settlement]:
        return [s for s in self.settlements if not s.abandoned]

    @property
    def population(self) -> float:
        return sum(s.population for s in self.alive_settlements)

    @property
    def strength(self) -> float:
        return sum(s.strength for s in self.alive_settlements)

    @property
    def territory_km2(self) -> float:
        """Claimed area: each settlement holds a disc whose radius grows with
        its population, capped by the doctrine's reach."""
        total = 0.0
        for s in self.alive_settlements:
            r = min(self.doctrine.reach_km, 12.0 + math.sqrt(s.population) * 9.0)
            total += math.pi * r * r
        return total

    @property
    def score(self) -> float:
        """The single number the endgame is decided on."""
        if not self.alive_settlements:
            return 0.0
        return (self.population * 10.0
                + len(self.alive_settlements) * 25.0
                + self.strength * 3.0
                + self.territory_km2 / 900.0
                + sum(self.tech.values()) * 40.0
                + sum(s.infrastructure for s in self.alive_settlements) / 2000.0)

    def relation(self, other: str) -> float:
        return self.relations.get(other, 0.0)

    def at_war_with(self, other: str) -> bool:
        return self.relations.get(other, 0.0) <= -0.55

    def note(self, msg: str) -> None:
        self.intel.append(msg)
        if len(self.intel) > 120:
            del self.intel[:40]

    def nearest_settlement(self, lat: float, lon: float) -> Settlement | None:
        best = None
        best_d = 1e18
        for s in self.alive_settlements:
            d = geo.haversine_m(lat, lon, s.lat, s.lon)
            if d < best_d:
                best_d = d
                best = s
        return best


SETTLEMENT_PREFIX = (
    "Fort", "New", "Upper", "Lower", "North", "South", "East", "West",
    "Old", "Far", "Deep", "High",
)
SETTLEMENT_STEM = (
    "Ash", "Bramble", "Cinder", "Dray", "Elm", "Ford", "Grist", "Hollow",
    "Iron", "Junction", "Kiln", "Larch", "Mill", "Norton", "Oakley", "Pike",
    "Quarry", "Rill", "Sedge", "Thatch", "Vale", "Weir", "Yarrow", "Barrow",
    "Cairn", "Dell", "Ember", "Furrow", "Garth", "Heath",
)
SETTLEMENT_SUFFIX = (
    "", "", "", " Crossing", " Reach", " Landing", " Hold", " Station",
    " Bottom", " Rise", " Camp", " Works",
)


def settlement_name(r: rng.Rng) -> str:
    parts = []
    if r.chance(0.25):
        parts.append(r.choice(SETTLEMENT_PREFIX))
    parts.append(r.choice(SETTLEMENT_STEM))
    name = " ".join(parts) + r.choice(SETTLEMENT_SUFFIX)
    return name
