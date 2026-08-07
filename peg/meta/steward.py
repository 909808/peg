"""The Steward: the planner that runs a rival faction.

Once a week of game time, each Steward scores every action it could take and
performs the best one it can afford. The action set is small and the scoring is
transparent, because the point is not that the AI be unbeatable -- it is that
its behaviour be *legible*. A player who watches ORCHARD take three river
valleys in a row should be able to work out that ORCHARD is agrarian, that it
values water above ore, and that the fourth valley upstream is therefore worth
claiming first.

Every evaluation is recorded with its utilities, and the intel view prints
them. Nothing here reads private state: a Steward surveys candidate ground with
the same function the player uses, and it only knows about rival settlements
that lie inside its detection range.

The abstraction is deliberate. Simulating six rival factions tile by tile would
cost a thousand times what it is worth, so rival settlements advance by the
same arithmetic the detailed colony obeys -- calories in against calories
burned, person-minutes against work required -- at a coarser grain. When you
attack one, it is instantiated in full and fought out metre by metre.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import geo
from .. import rng
from ..sim import items
from ..world import site as site_mod
from .faction import Faction, Settlement, settlement_name

#: How often a Steward re-plans, in days.
PLAN_INTERVAL = 7

#: Cultivated square metres per person a network aims for. Real pre-industrial
#: subsistence runs 2000-4000 m2 a head depending on crop and soil; 1400 left
#: every faction about 10% short, which does not starve them in any one year
#: but loses them the first bad one.
SUBSISTENCE_M2 = 2400.0


@dataclass
class Option:
    """One candidate action with the reasoning that scored it."""

    kind: str
    utility: float
    detail: str
    parts: dict[str, float] = field(default_factory=dict)
    payload: object = None

    def explain(self) -> str:
        bits = ", ".join(f"{k} {v:+.2f}" for k, v in
                         sorted(self.parts.items(), key=lambda kv: -abs(kv[1])))
        return f"{self.kind} ({self.utility:.2f}): {self.detail} [{bits}]"


class Steward:
    """Drives one faction. One instance per rival; the player has none."""

    def __init__(self, faction: Faction, seed: int):
        self.f = faction
        self.seed = seed
        self.last_plan_day = -999
        #: Rival settlements this faction has actually observed.
        self.known: dict[str, tuple[float, float, float]] = {}

    # ---- perception -----------------------------------------------------

    def detection_km(self) -> float:
        """How far this faction can see.

        Scouting range grows with logistics technology and with how many
        settlements there are to scout from. It has to comfortably exceed the
        typical distance between networks, or the Stewards never learn that
        anyone else exists and the whole competition quietly evaporates --
        which is exactly what 180 km produced.
        """
        base = 380.0 + 260.0 * self.f.tech.get("logistics", 0.0)
        return base * (1.0 + 0.12 * len(self.f.alive_settlements))

    def observe(self, world) -> None:
        """Update knowledge of neighbours.

        A Steward knows what its people could plausibly have seen: settlements
        within scouting range of one of its own. It does not get a list of
        every colony on Earth, which is why a distant rival can grow large
        without anyone reacting.
        """
        reach = self.detection_km() * 1000.0
        for other in world.factions:
            if other.key == self.f.key or other.eliminated:
                continue
            for s in other.alive_settlements:
                for mine in self.f.alive_settlements:
                    d = geo.haversine_m(mine.lat, mine.lon, s.lat, s.lon)
                    if d <= reach:
                        self.known[s.name] = (s.lat, s.lon, s.strength)
                        break

    # ---- planning -------------------------------------------------------

    def plan(self, world) -> Option | None:
        """Score every option and take the best. Returns what was chosen."""
        f = self.f
        if not f.alive_settlements:
            f.eliminated = True
            return None

        self.observe(world)
        r = rng.Rng(rng.mix(self.seed, world.day, len(f.settlements)))

        options: list[Option] = []
        options.extend(self._consider_expansion(world, r))
        options.extend(self._consider_development(world, r))
        options.extend(self._consider_military(world, r))
        options.extend(self._consider_diplomacy(world, r))
        options.extend(self._consider_research(world, r))

        if not options:
            return None

        # Survival first. A network whose farms do not yet feed it has no
        # business raising militia or researching metallurgy, and a planner
        # that does not know this arms itself straight into a famine -- which
        # is precisely how every militarist died in testing.
        precarious = self._precarity()
        if precarious > 0.0:
            for o in options:
                # Raiding is exempt, because for an aggressive doctrine a raid
                # IS the food plan. Suppressing it alongside research and
                # expansion left militarists starving quietly next to a full
                # neighbour, which is the one thing a militarist should never
                # do.
                if o.kind not in ("farm", "fuel", "attack"):
                    o.utility *= 1.0 - 0.75 * precarious

        options.sort(key=lambda o: -o.utility)

        # Record the top few so the player can read the Steward's mind.
        f.note(f"D{world.day}: " + " | ".join(o.explain() for o in options[:3]))

        # A larger network does more in a week than a smaller one. Executing a
        # single action per faction per week regardless of size meant an
        # eleven-settlement network tended each settlement once a quarter and
        # starved from sheer administrative neglect.
        budget = 1 + len(f.alive_settlements) // 2
        taken: list[Option] = []
        used_settlements: set[int] = set()
        for o in options:
            if len(taken) >= budget or o.utility <= 0.05:
                break
            # One action per settlement per cycle, and only one expansion.
            target_id = id(o.payload[0]) if isinstance(o.payload, tuple) \
                else id(o.payload)
            if o.kind == "expand" and any(t.kind == "expand" for t in taken):
                continue
            if o.kind in ("farm", "fuel", "build", "mine"):
                if target_id in used_settlements:
                    continue
                used_settlements.add(target_id)
            self._execute(o, world, r)
            taken.append(o)
        return taken[0] if taken else None

    def _precarity(self) -> float:
        """How close this faction is to not making it through the year, 0-1.

        Two independent measures per settlement -- whether the farms are big
        enough to feed the people, and whether the stores reach the next
        harvest -- combined across the network weighted by population. Taking
        the worst settlement instead permanently pinned large factions,
        because a colony founded last week always looks desperate.
        """
        total = 0.0
        weight = 0.0
        for s in self.f.alive_settlements:
            needed = max(1400.0, s.population * SUBSISTENCE_M2)
            coverage = min(1.0, s.farm_m2 / needed)
            # Stores buy time, they do not buy safety. A settlement with a
            # full granary and no fields is not secure, it is on a countdown,
            # and capping the credit stores can earn is what makes a Steward
            # plant before it is hungry rather than after.
            stores = min(0.60, s.food_days / 200.0)
            total += (1.0 - max(coverage, stores)) * s.population
            weight += s.population
        return total / weight if weight > 0 else 0.0

    # ---- option generators ----------------------------------------------

    def _consider_expansion(self, world, r: rng.Rng) -> list[Option]:
        f = self.f
        doc = f.doctrine
        if not f.alive_settlements or len(f.alive_settlements) >= 12:
            return []

        # Expansion is the most dangerous thing a network can do, because a
        # daughter colony consumes for a full year before it produces
        # anything. Three separate gates, all learned the hard way: the
        # network must be secure, it must have people to spare, and it must be
        # sitting on more than a year of food.
        #
        # Without these, Stewards expanded every week they possibly could and
        # every faction in the game was dead by its second winter.
        if self._precarity() > 0.25:
            return []
        surplus = min(s.food_days for s in f.alive_settlements)
        pop = f.population
        if surplus < 150 or pop < 16:
            return []

        home = max(f.alive_settlements, key=lambda s: s.population)
        avoid = [(s.lat, s.lon) for fac in world.factions
                 for s in fac.alive_settlements]

        # Search a ring at the edge of comfortable reach.
        radius = min(doc.reach_km, 90.0 + 40.0 * len(f.alive_settlements))
        found = site_mod.find_sites(
            home.lon, home.lat, radius, doctrine=doc.site_weights,
            samples=42, seed=world.seed, avoid=avoid, min_separation_km=55.0)
        if not found:
            return []

        out = []
        for score, sv, parts in found[:3]:
            dist_km = geo.haversine_m(home.lat, home.lon, sv.lat, sv.lon) / 1000.0
            # Distance is a real cost: a colony four hundred kilometres out
            # cannot be reinforced.
            logistics = math.exp(-dist_km / (doc.reach_km * 0.55))
            crowding = 1.0
            for fac in world.factions:
                if fac.key == f.key:
                    continue
                near = fac.nearest_settlement(sv.lat, sv.lon)
                if near is None:
                    continue
                d = geo.haversine_m(sv.lat, sv.lon, near.lat, near.lon) / 1000.0
                if d < 130:
                    # Settling on a rival's doorstep is provocative, and a
                    # cautious doctrine will not do it.
                    crowding *= 0.35 + 0.65 * (d / 130.0) + doc.aggression * 0.4
            u = (score / 100.0) * doc.expansionism * logistics * crowding
            u *= min(1.5, surplus / 60.0)
            out.append(Option(
                "expand", u,
                f"found a colony at {sv.lat:+.2f},{sv.lon:+.2f} "
                f"({sv.biome.name}, {dist_km:.0f} km)",
                {"site": score / 100.0, "logistics": logistics,
                 "crowding": crowding, "surplus": min(1.5, surplus / 60.0)},
                payload=sv))
        return out

    def _consider_development(self, world, r: rng.Rng) -> list[Option]:
        f = self.f
        out = []
        for s in f.alive_settlements:
            # Farmland: the return on clearing ground, against how hungry we
            # are and whether the climate rewards it.
            gdd = s.survey.clim.growing_degree_days(base=5.0)
            arable = s.survey.arable_fraction
            # Even bad ground is worth breaking if it is all there is: a
            # threshold of 0.05 here meant networks on marginal soil never
            # planted anything at all and starved with the option unconsidered.
            if arable > 0.004:
                # About 1400 square metres per person is subsistence. Urgency
                # comes from how far short of that we are, NOT from how hungry
                # we are today: a planner that waits until the stores run low
                # is planting in October, and every network that did it
                # starved in its first winter.
                needed = max(1400.0, s.population * SUBSISTENCE_M2)
                coverage = min(1.0, s.farm_m2 / needed)
                want = needed - s.farm_m2
                if want > 200:
                    urgency = (1.0 - coverage) ** 0.7
                    # Urgency dominates quality. Multiplying straight through
                    # by arable meant a network on middling soil rated farming
                    # below routine construction and never broke ground at
                    # all -- when in fact people short of food farm whatever
                    # they have, and how good it is only sets the yield.
                    u = (0.30 + 1.40 * urgency) * (0.35 + 0.65 * arable) \
                        * min(1.0, gdd / 900.0)
                    # Genuinely short of food is still worth a bonus.
                    if s.food_days < 120:
                        u *= 1.0 + rng.clamp01(1.0 - s.food_days / 120.0)
                    out.append(Option(
                        "farm", u, f"clear {min(want, 4000):.0f} m2 at {s.name} "
                        f"({coverage:.0%} of subsistence)",
                        {"arable": arable, "shortfall": urgency,
                         "season": min(1.0, gdd / 1600.0)},
                        payload=(s, min(want, 4000.0))))

            # Fuel and shelter, weighted by how cold it gets.
            hdd = s.survey.heating_degree_days
            if hdd > 800:
                fuel_days = s.fuel_mj / max(1.0, s.population * hdd / 365.0 * 0.9)
                if fuel_days < 90:
                    u = 0.6 * rng.clamp01(1.0 - fuel_days / 90.0) \
                        * min(1.0, s.survey.timber_m3_ha / 200.0)
                    out.append(Option(
                        "fuel", u, f"cut and stack fuel at {s.name} "
                        f"({fuel_days:.0f} days on hand)",
                        {"shortfall": rng.clamp01(1.0 - fuel_days / 90.0),
                         "timber": min(1.0, s.survey.timber_m3_ha / 200.0)},
                        payload=s))

            # Infrastructure: always available, never urgent.
            u = 0.30 * f.doctrine.caution * rng.clamp01(
                1.0 - s.infrastructure / (s.population * 900.0 + 1.0))
            out.append(Option(
                "build", u, f"build at {s.name}",
                {"caution": f.doctrine.caution}, payload=s))

            # Ore, if there is any and we have people spare.
            ore = max(s.survey.minerals.get("iron", 0.0),
                      s.survey.minerals.get("copper", 0.0))
            if ore > 0.3 and s.population > 12:
                u = 0.45 * ore * (0.4 + f.tech.get("metallurgy", 0.0))
                out.append(Option(
                    "mine", u, f"work the {'iron' if s.survey.minerals.get('iron',0) >= s.survey.minerals.get('copper',0) else 'copper'} at {s.name}",
                    {"ore": ore, "metallurgy": f.tech.get("metallurgy", 0.0)},
                    payload=s))
        return out

    def _consider_military(self, world, r: rng.Rng) -> list[Option]:
        f = self.f
        doc = f.doctrine
        out = []

        # Arm ourselves. Threat is what neighbours we can see, weighted by
        # how badly they like us.
        threat = 0.0
        for name, (lat, lon, strength) in self.known.items():
            owner = world.owner_of(name)
            if owner is None or owner.key == f.key:
                continue
            rel = f.relation(owner.key)
            near = f.nearest_settlement(lat, lon)
            if near is None:
                continue
            d = geo.haversine_m(lat, lon, near.lat, near.lon) / 1000.0
            threat += strength * math.exp(-d / 260.0) * max(0.0, 0.5 - rel)
        own = max(1.0, f.strength)
        ratio = threat / own
        if ratio > 0.25 or doc.aggression > 0.45:
            u = 0.5 * rng.clamp01(ratio) + 0.35 * doc.aggression
            u *= doc.caution + 0.4
            out.append(Option("arm", u,
                              f"raise and equip militia (threat ratio {ratio:.2f})",
                              {"threat": rng.clamp01(ratio),
                               "aggression": doc.aggression}, payload=None))

        # Attack. Only worth it if we can win and the prize is worth the loss.
        for name, (lat, lon, strength) in self.known.items():
            owner = world.owner_of(name)
            if owner is None or owner.key == f.key or owner.eliminated:
                continue
            target = next((s for s in owner.alive_settlements if s.name == name), None)
            if target is None:
                continue
            near = f.nearest_settlement(lat, lon)
            if near is None:
                continue
            d = geo.haversine_m(lat, lon, near.lat, near.lon) / 1000.0
            if d > doc.reach_km:
                continue
            # Force is concentrated from every settlement that can reach the
            # target, each contributing what it can spare after leaving a
            # garrison and losing strength to the march. Sending only the
            # nearest settlement's militia meant an attack never once cleared
            # the odds threshold, so no war ever happened.
            projected = 0.0
            for src in f.alive_settlements:
                sd = geo.haversine_m(lat, lon, src.lat, src.lon) / 1000.0
                if sd > doc.reach_km:
                    continue
                spare = 0.75 if src is not near else 0.6
                projected += src.strength * spare * math.exp(-sd / (doc.reach_km * 0.9))
            defence = target.strength * (1.0 + target.survey.defensibility * 0.5)
            odds = projected / max(0.5, projected + defence)
            if odds < 0.30:
                continue
            prize = target.value / 40.0
            rel = f.relation(owner.key)
            # Attacking an ally is not merely unwise, it is off the table.
            if rel > 0.4:
                continue
            hostility = rng.clamp01(0.5 - rel)
            # Hunger is the oldest reason to raid, and a granary next door is
            # a far more attractive target when your own is empty.
            desperation = self._precarity()
            if target.food_days > 60:
                prize *= 1.0 + desperation * 1.5
            u = doc.aggression * odds * min(2.0, prize) * (0.4 + hostility)
            u *= 1.0 + desperation * 0.8
            out.append(Option(
                "attack", u,
                f"raid {owner.name}'s {name} ({d:.0f} km, odds {odds:.0%})",
                {"odds": odds, "prize": min(2.0, prize),
                 "aggression": doc.aggression, "hostility": hostility,
                 "desperation": desperation},
                payload=(owner, target, projected)))
        return out

    def _consider_diplomacy(self, world, r: rng.Rng) -> list[Option]:
        f = self.f
        doc = f.doctrine
        out = []
        for other in world.factions:
            if other.key == f.key or other.eliminated:
                continue
            if not any(world.owner_of(n) is other for n in self.known):
                continue
            rel = f.relation(other.key)

            # Trade: worth most when our shortages are their surpluses.
            if rel > -0.4:
                u = 0.4 * doc.diplomacy * (0.5 + rel * 0.5)
                out.append(Option("trade", u, f"open trade with {other.name}",
                                  {"diplomacy": doc.diplomacy, "relation": rel},
                                  payload=other))

            # Gang up on the leader. A faction that is running away with the
            # game finds everyone else suddenly friendly with each other.
            leader = world.leader()
            if leader and leader.key not in (f.key, other.key):
                if leader.score > f.score * 1.6:
                    u = 0.45 * doc.diplomacy * min(1.0, leader.score / max(1.0, f.score) / 3.0)
                    out.append(Option(
                        "pact", u,
                        f"seek a pact with {other.name} against {leader.name}",
                        {"diplomacy": doc.diplomacy,
                         "leader_lead": leader.score / max(1.0, f.score)},
                        payload=other))
        return out

    def _consider_research(self, world, r: rng.Rng) -> list[Option]:
        f = self.f
        out = []
        pop = f.population
        if pop < 12:
            return []
        priorities = {
            "agrarian": ("agriculture", "medicine", "logistics"),
            "industrial": ("metallurgy", "logistics", "agriculture"),
            "militarist": ("firearms", "metallurgy", "medicine"),
            "mercantile": ("logistics", "agriculture", "metallurgy"),
            "survivalist": ("medicine", "agriculture", "firearms"),
        }[f.doctrine.key]
        for i, branch in enumerate(priorities):
            level = f.tech.get(branch, 0.0)
            if level >= 1.0:
                continue
            u = 0.34 * (1.0 - i * 0.22) * (1.0 - level * 0.5) \
                * min(1.5, pop / 30.0)
            out.append(Option("research", u, f"research {branch}",
                              {"branch_priority": 1.0 - i * 0.22,
                               "capacity": min(1.5, pop / 30.0)},
                              payload=branch))
        return out

    # ---- execution ------------------------------------------------------

    def _execute(self, o: Option, world, r: rng.Rng) -> None:
        f = self.f
        if o.kind == "expand":
            sv: site_mod.Survey = o.payload
            home = max(f.alive_settlements, key=lambda s: s.population)
            # Colonists come out of an existing settlement, and they take a
            # year of food with them. A colony sent out with forty days of
            # stores arrives, plants nothing in time, and dies.
            send = min(9.0, max(5.0, home.population * 0.25))
            if home.population - send < 8:
                return
            dowry = send * 2800.0 * 200.0
            if home.food_kcal < dowry * 1.6:
                return
            home.population -= send
            home.food_kcal -= dowry
            s = Settlement(
                name=settlement_name(r), faction=f.key,
                lat=sv.lat, lon=sv.lon, survey=sv,
                population=send, founded_day=world.day,
                food_kcal=dowry, fuel_mj=send * 900,
                militia=max(1.0, send * 0.2),
                weapons_tier=1.0 + f.tech.get("firearms", 0.0) * 2.0)
            f.settlements.append(s)
            world.event(f"{f.name} founded {s.name} at "
                        f"{sv.lat:+.2f},{sv.lon:+.2f} ({sv.biome.name})")

        elif o.kind == "farm":
            s, area = o.payload
            s.farm_m2 += area
            s.infrastructure += area * 0.25

        elif o.kind == "fuel":
            s: Settlement = o.payload
            # Cutting fuel is labour; the return depends on standing timber.
            s.fuel_mj += min(4.0e4, s.population * 900.0
                             * min(2.0, s.survey.timber_m3_ha / 120.0))

        elif o.kind == "build":
            s = o.payload
            s.infrastructure += s.population * 260.0

        elif o.kind == "mine":
            s = o.payload
            ore = max(s.survey.minerals.get("iron", 0.0),
                      s.survey.minerals.get("copper", 0.0))
            s.metal_kg += s.population * 12.0 * ore \
                * (0.5 + f.tech.get("metallurgy", 0.0))

        elif o.kind == "arm":
            for s in f.alive_settlements:
                spare = max(0.0, s.population * 0.28 - s.militia)
                if spare <= 0:
                    continue
                cost = spare * 18.0
                if s.metal_kg >= cost:
                    s.metal_kg -= cost
                    s.militia += spare
                else:
                    armed = s.metal_kg / 18.0
                    s.metal_kg = 0.0
                    # People without metal still turn out, with spears and
                    # whatever is in the shed. Worth having; worth less.
                    s.militia += armed + (spare - armed) * 0.45
                s.weapons_tier = 1.0 + f.tech.get("firearms", 0.0) * 2.0

        elif o.kind == "attack":
            owner, target, force = o.payload
            world.resolve_raid(f, owner, target, force, r)

        elif o.kind == "trade":
            other: Faction = o.payload
            f.relations[other.key] = min(1.0, f.relation(other.key) + 0.12)
            other.relations[f.key] = min(1.0, other.relation(f.key) + 0.10)
            # Trade is worth something material, not just goodwill.
            for s in f.alive_settlements[:2]:
                s.food_kcal += s.population * 2800.0 * 3.0
                s.metal_kg += 25.0

        elif o.kind == "pact":
            other = o.payload
            leader = world.leader()
            f.relations[other.key] = min(1.0, f.relation(other.key) + 0.25)
            other.relations[f.key] = min(1.0, other.relation(f.key) + 0.20)
            if leader and leader.key not in (f.key, other.key):
                f.relations[leader.key] = max(-1.0, f.relation(leader.key) - 0.20)
                other.relations[leader.key] = max(-1.0, other.relation(leader.key) - 0.15)
                world.event(f"{f.name} and {other.name} align against "
                            f"{leader.name}")

        elif o.kind == "research":
            branch = o.payload
            gain = 0.035 * min(2.0, f.population / 25.0)
            f.tech[branch] = min(1.0, f.tech.get(branch, 0.0) + gain)


# --------------------------------------------------------------------------
# settlement growth
# --------------------------------------------------------------------------


def advance_settlement(s: Settlement, f: Faction, days: float,
                       day_of_year: int, r: rng.Rng) -> list[str]:
    """Advance an abstracted settlement by ``days``.

    This is the detailed colony's arithmetic at a coarser grain: calories
    produced against calories burned, fuel cut against fuel needed, and a
    population that grows or dies on the difference. It has to agree with the
    detailed simulation closely enough that a player who switches from
    watching a rival to fighting one does not find the numbers were fiction.
    """
    events: list[str] = []
    if s.abandoned:
        return events

    clim = s.survey.clim
    temp = clim.temp_on_day(day_of_year)

    # ---- food ----------------------------------------------------------
    need = s.population * 2800.0 * days
    s.food_kcal -= need

    # Harvest arrives once a year, in the autumn of whichever hemisphere.
    if s.farm_m2 > 0:
        crops = items.best_crops(clim)
        if crops:
            crop, suit = crops[0]
            harvest_day = 250 if s.lat >= 0 else 68
            if abs((day_of_year - harvest_day) % 365) < days:
                out = items.ITEMS[crop.yields]
                kg = (crop.kg_per_m2 * s.farm_m2 * suit
                      * s.survey.soil.fertility
                      * (0.75 + 0.35 * f.tech.get("agriculture", 0.0)))
                s.food_kcal += kg * out.kcal_kg
                events.append(f"{s.name} brought in {kg:.0f} kg of {crop.name}")

    # Foraging and hunting supplement, bounded by the territory as in the
    # detailed model.
    forage = s.survey.biome.forage_kcal_m2 * math.pi * 2000.0 ** 2 * 0.01
    s.food_kcal += forage / 365.0 * days * min(1.0, s.population / 20.0)

    # ---- fuel and cold -------------------------------------------------
    if temp < 16.0:
        s.fuel_mj -= (16.0 - temp) * s.population * 0.85 * days \
            / max(0.8, 1.0 + s.infrastructure / (s.population * 900.0 + 1.0))
    s.fuel_mj = max(0.0, s.fuel_mj)

    # ---- consequences --------------------------------------------------
    hardship = 0.0
    if s.food_kcal < 0:
        s.food_kcal = 0.0
        hardship += 0.55
    if s.fuel_mj <= 0 and temp < 2.0:
        hardship += 0.35
    if s.survey.fresh_water < 0.15:
        hardship += 0.10

    if hardship > 0:
        lost = s.population * hardship * 0.022 * days
        s.population -= lost
        s.unrest = min(1.0, s.unrest + hardship * 0.03 * days)
        if lost > 0.75:
            events.append(f"{s.name} lost {lost:.0f} to hunger and cold")
    else:
        # Growth: births plus the odd arrival, capped by what the land bears.
        carrying = 20.0 + s.farm_m2 / 700.0 + s.survey.arable_fraction * 60.0 \
            + s.survey.biome.forage_kcal_m2 * 0.8
        room = max(0.0, 1.0 - s.population / max(1.0, carrying))
        # About 12% a year at full room -- fast for a real subsistence
        # population, but this is a game and a century has to fit in a session.
        s.population += s.population * 0.00032 * days * room
        s.unrest = max(0.0, s.unrest - 0.01 * days)

    if s.population < 2.0:
        s.abandoned = True
        events.append(f"{s.name} was abandoned")
    return events
