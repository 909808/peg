"""The strategic layer: the whole planet, and everyone on it.

Holds the factions, advances their settlements, runs the Stewards' planning
cycle, resolves raids and diplomacy, and keeps the chronicle of what happened.

The player's own settlement is simulated in full by :mod:`peg.sim.colony`; this
layer treats it as one more settlement for scoring and for being attacked. That
is the point of the split -- the strategic layer decides *who fights whom over
what*, and the tactical layer decides *how it goes*.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import geo
from .. import rng
from ..world import site as site_mod
from .faction import DOCTRINES, STEWARD_NAMES, Faction, Settlement, settlement_name
from .steward import PLAN_INTERVAL, Steward, advance_settlement

#: Glyphs and colours used to distinguish factions on the world map.
FACTION_GLYPHS = "@ABCDEFGH"
FACTION_COLOURS = (46, 214, 51, 213, 227, 117, 209, 159)


@dataclass
class World:
    seed: int
    factions: list[Faction] = field(default_factory=list)
    stewards: list[Steward] = field(default_factory=list)
    day: int = 80
    year: int = 2041
    chronicle: list[str] = field(default_factory=list)
    #: Settlement name -> faction key, kept current for fast lookup.
    _owner: dict[str, str] = field(default_factory=dict)

    # ---- lookup ---------------------------------------------------------

    def owner_of(self, settlement_name: str) -> Faction | None:
        key = self._owner.get(settlement_name)
        if key is None:
            return None
        return self.faction(key)

    def faction(self, key: str) -> Faction | None:
        for f in self.factions:
            if f.key == key:
                return f
        return None

    @property
    def player(self) -> Faction | None:
        for f in self.factions:
            if f.is_player:
                return f
        return None

    def reindex(self) -> None:
        self._owner.clear()
        for f in self.factions:
            for s in f.alive_settlements:
                self._owner[s.name] = f.key

    def leader(self) -> Faction | None:
        live = [f for f in self.factions if not f.eliminated]
        return max(live, key=lambda f: f.score) if live else None

    def standings(self) -> list[tuple[Faction, float]]:
        rows = [(f, f.score) for f in self.factions if not f.eliminated]
        rows.sort(key=lambda r: -r[1])
        return rows

    def event(self, msg: str) -> None:
        self.chronicle.append(f"[Y{self.year} D{self.day:03d}] {msg}")
        if len(self.chronicle) > 600:
            del self.chronicle[:200]

    # ---- the tick -------------------------------------------------------

    def advance(self, days: float) -> None:
        """Advance the strategic layer. Called once per game day or faster."""
        r = rng.Rng(rng.mix(self.seed, self.year, self.day))
        self.reindex()

        for f in self.factions:
            if f.eliminated:
                continue
            _share_food(f, days)
            for s in list(f.alive_settlements):
                for e in advance_settlement(s, f, days, self.day, r):
                    self.event(e)
            if not f.alive_settlements and not f.is_player:
                f.eliminated = True
                self.event(f"{f.name} has collapsed")

        # Relations drift back towards indifference; grudges fade, alliances
        # need maintaining.
        for f in self.factions:
            for k in list(f.relations):
                f.relations[k] *= (1.0 - 0.004 * days)

        # Stewards plan on their own cadence, staggered so they do not all
        # move on the same day.
        for i, st in enumerate(self.stewards):
            if st.f.eliminated:
                continue
            if (self.day + i * 2) % PLAN_INTERVAL == 0:
                st.plan(self)

        self.day += int(days)
        while self.day >= 365:
            self.day -= 365
            self.year += 1

    # ---- conflict -------------------------------------------------------

    def resolve_raid(self, attacker: Faction, defender: Faction,
                     target: Settlement, force: float, r: rng.Rng) -> None:
        """Resolve an abstract raid between two AI factions.

        Player settlements are never resolved here -- an attack on the player
        becomes a tactical engagement on the actual map, because that is the
        part worth playing.
        """
        attacker.relations[defender.key] = max(
            -1.0, attacker.relation(defender.key) - 0.45)
        defender.relations[attacker.key] = max(
            -1.0, defender.relation(attacker.key) - 0.6)

        defence = target.strength * (1.0 + target.survey.defensibility * 0.5)
        total = force + defence
        p_win = force / max(0.5, total)

        # Both sides lose people whatever happens. Lanchester-ish: casualties
        # scale with the opposition you faced, not with your own numbers.
        att_loss = defence / max(1.0, total) * force * 0.35
        def_loss = force / max(1.0, total) * defence * 0.40

        near = attacker.nearest_settlement(target.lat, target.lon)
        if near is not None:
            near.militia = max(0.0, near.militia - att_loss)
            near.population = max(1.0, near.population - att_loss * 0.4)
        target.militia = max(0.0, target.militia - def_loss)

        if r.random() < p_win:
            # A successful raid strips stores; it does not usually annex.
            loot_food = target.food_kcal * 0.45
            loot_metal = target.metal_kg * 0.5
            target.food_kcal -= loot_food
            target.metal_kg -= loot_metal
            target.population = max(1.0, target.population - def_loss * 0.5)
            target.unrest = min(1.0, target.unrest + 0.25)
            if near is not None:
                near.food_kcal += loot_food
                near.metal_kg += loot_metal
            self.event(f"{attacker.name} raided {defender.name}'s "
                       f"{target.name} and carried off supplies")
            # Occupation, if the settlement is broken and the attacker is
            # militarist enough to hold it.
            if target.militia <= 0.5 and attacker.doctrine.aggression > 0.7 \
                    and target.population < 6:
                target.faction = attacker.key
                defender.settlements.remove(target)
                attacker.settlements.append(target)
                self.event(f"{attacker.name} seized {target.name}")
        else:
            self.event(f"{defender.name} threw back a raid on {target.name}")

        # Everyone who can see it takes note. Aggression is expensive
        # diplomatically, which is what stops a militarist snowballing.
        for f in self.factions:
            if f.key in (attacker.key, defender.key) or f.eliminated:
                continue
            f.relations[attacker.key] = max(
                -1.0, f.relation(attacker.key) - 0.10 * f.doctrine.diplomacy)


def _share_food(f: Faction, days: float) -> None:
    """Move food between a faction's own settlements.

    A *network* is the whole premise -- these are logistics AIs, and the first
    thing any of them would do is send grain from the settlement that has it to
    the one that does not. Without this, a daughter colony founded a month
    after harvest starves within sight of a full granary, and networks bled
    settlements steadily for no reason a player could act on.

    Transfer is capped by distance and improves with logistics research, so a
    sprawling network still cannot feed its periphery indefinitely.
    """
    live = f.alive_settlements
    if len(live) < 2:
        return
    hub = max(live, key=lambda s: s.food_days)
    capacity = (0.10 + 0.20 * f.tech.get("logistics", 0.0)) * days
    for s in live:
        if s is hub or s.food_days > 120:
            continue
        need = s.population * 2800.0 * 150.0 - s.food_kcal
        if need <= 0:
            continue
        d = geo.haversine_m(hub.lat, hub.lon, s.lat, s.lon) / 1000.0
        reach = math.exp(-d / max(80.0, f.doctrine.reach_km * 0.7))
        # The hub keeps enough for its own people.
        spare = max(0.0, hub.food_kcal - hub.population * 2800.0 * 150.0)
        move = min(need, spare * capacity * reach)
        if move > 0:
            hub.food_kcal -= move
            s.food_kcal += move


# --------------------------------------------------------------------------
# world setup
# --------------------------------------------------------------------------


#: A starting site must be able to feed its people. Networks founded on
#: unfarmable soil never planted anything -- there was nothing to plant -- and
#: died in their second year without the player ever being told why.
MIN_START_ARABLE = 0.24


def viable_start(sv: site_mod.Survey) -> bool:
    """Can a network actually survive here?

    Farmable ground, or failing that enough wild food and water to hold on
    while it finds some.
    """
    if sv.is_ocean or sv.biome.key in ("ocean", "lake", "ice"):
        return False
    if sv.clim.growing_days < 100:
        return False
    if sv.fresh_water < 0.15:
        return False
    return (sv.arable_fraction >= MIN_START_ARABLE
            or sv.biome.forage_kcal_m2 >= 25.0)


def _pick_start(lon: float, lat: float, doctrine: str, seed: int,
                avoid: list[tuple[float, float]], radius_km: float
                ) -> site_mod.Survey | None:
    found = site_mod.find_sites(lon, lat, radius_km, doctrine=doctrine,
                                samples=60, seed=seed, avoid=avoid,
                                min_separation_km=MIN_START_SEPARATION_KM)
    for _score, sv, _parts in found:
        if viable_start(sv):
            return sv
    # Widen the search rather than settling somewhere fatal.
    if radius_km < 900:
        return _pick_start(lon, lat, doctrine, seed, avoid, radius_km * 1.8)
    return found[0][1] if found else None


#: Theatres: real regions large enough to hold several competing networks and
#: varied enough that the ground is worth arguing about. Everyone in a game
#: starts in *one* of these.
#:
#: Scattering factions across separate continents was the first version, and it
#: produced twenty years of complete peace -- nobody could see anybody, so
#: nobody competed. Contact is not a detail of the design, it is the design.
START_REGIONS = (
    ("the Great Plains", -98.0, 41.0),
    ("the Danube basin", 19.0, 47.0),
    ("the North European Plain", 14.0, 52.5),
    ("the Ganges plain", 81.0, 26.0),
    ("the North China Plain", 116.0, 35.0),
    ("the Pampas", -61.0, -34.0),
    ("the Murray basin", 145.0, -35.0),
    ("the East African highlands", 36.5, -0.5),
    ("the Anatolian plateau", 33.0, 39.0),
    ("the Mississippi valley", -90.0, 35.0),
    ("the Iberian meseta", -4.0, 40.5),
    ("the West Siberian plain", 75.0, 56.0),
)


#: How far apart networks start, in kilometres. Close enough that scouts meet
#: within a couple of years, far enough that the first meeting is a choice
#: rather than an immediate war.
THEATRE_RADIUS_KM = 620.0
MIN_START_SEPARATION_KM = 170.0


def new_world(seed: int, rivals: int = 5, player_lon: float | None = None,
              player_lat: float | None = None, verbose: bool = False) -> World:
    """Create a world: one player faction and ``rivals`` Stewards, all in one
    contested theatre."""
    w = World(seed=seed)
    r = rng.Rng(rng.mix(seed, rng.tag("world")))

    taken: list[tuple[float, float]] = []
    doctrines = r.shuffled(list(DOCTRINES.values()))
    names = r.shuffled(list(STEWARD_NAMES))

    # ---- the player -----------------------------------------------------
    if player_lon is None or player_lat is None:
        theatre = r.choice(START_REGIONS)
        sv = _pick_start(theatre[1], theatre[2], "agrarian", seed, taken, 300.0)
    else:
        theatre = ("your chosen ground", player_lon, player_lat)
        sv = site_mod.survey(player_lon, player_lat, seed)
        if sv.is_ocean or sv.biome.key in ("ocean", "lake", "ice"):
            sv = _pick_start(player_lon, player_lat, "agrarian", seed, taken, 400.0)
    if sv is None:
        raise RuntimeError("could not find a viable starting site")
    if verbose:
        print(f"  theatre: {theatre[0]}")

    player = Faction(key="player", name="Your network",
                     doctrine=DOCTRINES["agrarian"], glyph="@",
                     colour=FACTION_COLOURS[0], is_player=True)
    home = Settlement(name="Landfall", faction="player", lat=sv.lat, lon=sv.lon,
                      survey=sv, population=8.0, founded_day=w.day)
    player.settlements.append(home)
    w.factions.append(player)
    taken.append((sv.lat, sv.lon))

    # ---- the rivals -----------------------------------------------------
    # Placed on a ring around the theatre centre so they are spread out but
    # all within reach of each other.
    for i in range(rivals):
        doc = doctrines[i % len(doctrines)]
        bearing = (360.0 / max(1, rivals)) * i + r.uniform(-22.0, 22.0)
        dist = THEATRE_RADIUS_KM * r.uniform(0.45, 1.0)
        rlat, rlon = geo.offset_deg(theatre[2], theatre[1], bearing, dist * 1000.0)
        rsv = _pick_start(rlon, rlat, doc.site_weights, seed, taken, 240.0)
        if rsv is None:
            continue
        f = Faction(key=f"ai{i}", name=names[i % len(names)], doctrine=doc,
                    glyph=FACTION_GLYPHS[(i + 1) % len(FACTION_GLYPHS)],
                    colour=FACTION_COLOURS[(i + 1) % len(FACTION_COLOURS)])
        s = Settlement(name=settlement_name(r), faction=f.key,
                       lat=rsv.lat, lon=rsv.lon, survey=rsv,
                       population=8.0, founded_day=w.day)
        f.settlements.append(s)
        w.factions.append(f)
        w.stewards.append(Steward(f, rng.mix(seed, i, rng.tag("steward"))))
        taken.append((rsv.lat, rsv.lon))
        if verbose:
            d = geo.haversine_m(sv.lat, sv.lon, rsv.lat, rsv.lon) / 1000.0
            print(f"  {f.name} ({doc.name}) at {s.name}, "
                  f"{rsv.lat:+.2f},{rsv.lon:+.2f} -- {rsv.biome.name}, "
                  f"{d:.0f} km from you")

    # Everyone starts wary but not hostile.
    for f in w.factions:
        for g in w.factions:
            if f.key != g.key:
                f.relations[g.key] = r.uniform(-0.1, 0.2)

    w.reindex()
    w.event(f"Seven networks remain. {len(w.factions)} of them are still "
            f"answering.")
    return w
