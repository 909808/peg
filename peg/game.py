"""The game: one colony you run, on a planet full of rivals who do not stop.

Binds the two scales together. Your settlement is simulated tile by tile at
one-minute resolution; the rest of the world advances on the strategic layer at
one-day resolution. When somebody comes for you, their abstract strength is
turned into actual people with actual weapons on your actual map, and the fight
happens where you built.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geo
from . import rng
from .local import terrain
from .meta import world as world_mod
from .meta.faction import Faction, Settlement
from .meta.steward import Steward
from .sim import combat, items
from .sim.colony import BUILDINGS, Colony
from .sim.pawn import Pawn

#: Real-world starting kit: what a network can still put on the ground in 2041.
STARTING_STORES = (
    ("grain", 900.0), ("preserved", 120.0), ("vegetables", 90.0),
    ("wood", 2200.0), ("stone", 400.0), ("herbs", 40.0),
    ("medicine", 12.0), ("cloth", 60.0), ("iron", 90.0),
    ("ammo", 400.0), ("salt", 60.0),
)


@dataclass
class Game:
    world: world_mod.World
    colony: Colony
    faction: Faction
    home: Settlement
    #: Real seconds of game time per tick of the interactive loop.
    speed: int = 1
    minutes_per_step: int = 10
    over: bool = False
    outcome: str = ""
    #: Raids that have been launched at the player and not yet resolved.
    incoming: list[tuple[Faction, float, int]] = field(default_factory=list)
    battle_log: list[str] = field(default_factory=list)

    # ---- construction ---------------------------------------------------

    @classmethod
    def new(cls, seed: int = 20410, lon: float | None = None,
            lat: float | None = None, rivals: int = 5,
            size: int = 160, colonists: int = 8) -> "Game":
        w = world_mod.new_world(seed, rivals=rivals,
                                player_lon=lon, player_lat=lat)
        faction = w.player
        home = faction.settlements[0]
        m = terrain.generate(home.survey, size, seed=seed)

        colony = Colony(name="Landfall", faction="player", survey=home.survey,
                        map=m, seed=seed, day=w.day, year=w.year)
        r = rng.Rng(rng.mix(seed, rng.tag("colonists")))
        centre = size // 2
        for i in range(colonists):
            p = Pawn.random(rng.mix(seed, i, rng.tag("pawn")))
            p.x = centre + (i % 4) - 2
            p.y = centre + (i // 4)
            colony.pawns.append(p)
        for key, amount in STARTING_STORES:
            colony.store.add(key, amount)
        colony.water_l = 400.0

        # A roof and a fire, because arriving with nothing is not a game.
        colony.order_building("shelter", centre - 3, centre - 3)
        colony.order_building("hearth", centre - 1, centre - 3)
        for b in colony.buildings:
            b.work_left = 0.0

        home.population = float(colonists)
        home.food_kcal = colony.store.food_kcal()

        g = cls(world=w, colony=colony, faction=faction, home=home)
        g.colony.note("Landfall. The network is down to one settlement.")
        return g

    # ---- the loop -------------------------------------------------------

    def step(self) -> None:
        """Advance one colony tick, and the world when a day turns over."""
        if self.over:
            return
        day_before = self.colony.day
        self.colony.tick(self.minutes_per_step)
        if self.colony.day != day_before:
            self._sync_to_world()
            self.world.advance(1)
            self._check_incoming()
            self._check_end()

    def run_headless(self, days: int, report_every: int = 30) -> None:
        """Run without a UI. Useful for balance work and for seeing whether a
        given patch of Earth is survivable at all."""
        steps = int(days * 1440 / self.minutes_per_step)
        for i in range(steps):
            self.step()
            if self.over:
                break
            if (i * self.minutes_per_step) % (report_every * 1440) == 0:
                print(self.colony.report())
                print()
        print(self.colony.report())
        print()
        print(self.standings_text())
        if self.over:
            print()
            print(self.outcome)

    # ---- the two scales ------------------------------------------------

    def _sync_to_world(self) -> None:
        """Push the detailed colony's real state up to the strategic layer.

        The rivals' planners read these numbers when they decide whether you
        are worth raiding, so they have to be the truth rather than a
        placeholder.
        """
        c = self.colony
        s = self.home
        s.population = float(c.population)
        s.food_kcal = c.store.food_kcal()
        s.fuel_mj = c.store.fuel_mj()
        s.farm_m2 = float(sum(f.area_m2 for f in c.fields))
        s.infrastructure = sum(
            b.d.work_min for b in c.buildings if b.done)
        s.metal_kg = c.store.amount("iron") + c.store.amount("steel")
        armed = sum(1 for p in c.able if c.store.amount("ammo") > 20)
        s.militia = float(min(len(c.able), max(1, armed)))
        s.weapons_tier = 1.0 + min(1.0, c.store.amount("ammo") / 400.0)
        if c.population <= 0:
            s.abandoned = True

    def _check_incoming(self) -> None:
        """See whether any Steward has decided to come for us.

        Rival planners call ``World.resolve_raid`` against each other, but a
        raid on the player is intercepted here and fought on the real map.
        """
        for st in self.world.stewards:
            f = st.f
            if f.is_player or f.eliminated:
                continue
            if f.relation("player") > -0.3:
                continue
            near = f.nearest_settlement(self.home.lat, self.home.lon)
            if near is None:
                continue
            d_km = geo.haversine_m(self.home.lat, self.home.lon,
                                   near.lat, near.lon) / 1000.0
            if d_km > f.doctrine.reach_km:
                continue
            # One roll a day, scaled by how much they dislike us.
            hostility = -f.relation("player")
            r = rng.Rng(rng.mix(self.world.seed, self.world.day, hash(f.key) & 0xFFFF))
            if r.random() < 0.004 * hostility * f.doctrine.aggression:
                force = near.strength * 0.7 * math.exp(-d_km / (f.doctrine.reach_km * 0.9))
                self.incoming.append((f, force, self.world.day))
                self.colony.note(f"Scouts report {f.name} raiders approaching "
                                 f"from {d_km:.0f} km away.")

    def resolve_incoming(self) -> str | None:
        """Fight any raid that has arrived. Returns a report, or None."""
        if not self.incoming:
            return None
        f, force, _day = self.incoming.pop(0)
        c = self.colony
        m = c.map
        centre = m.size // 2

        # Turn abstract strength into people. This is the moment the two
        # scales meet, and the conversion has to be honest: a strength of 12
        # is about six armed adults, not a number that scales with difficulty.
        n = max(2, int(round(force / 2.0)))
        r = rng.Rng(rng.mix(self.world.seed, self.world.day, rng.tag("raid")))
        tier = 0.0
        for s in f.alive_settlements:
            tier = max(tier, s.weapons_tier)
        weapon = ("assault_rifle" if tier > 2.2 else
                  "battle_rifle" if tier > 1.7 else
                  "hunting_rifle" if tier > 1.2 else
                  r.choice(("shotgun", "pistol", "spear")))
        armour = "padded" if tier > 1.5 else "none"

        fighters = []
        for p in c.able:
            p.x = max(1, min(m.size - 2, p.x))
            p.y = max(1, min(m.size - 2, p.y))
            w = "hunting_rifle" if c.store.amount("ammo") > 40 else "spear"
            fi = combat.make_fighter(p, w, "padded", "colony",
                                     int(c.store.amount("ammo") // max(1, len(c.able))))
            fi.role = "hold"
            fighters.append(fi)
        edge = r.choice(((centre, 4), (centre, m.size - 5),
                         (4, centre), (m.size - 5, centre)))
        for i in range(n):
            p = Pawn.random(rng.mix(self.world.seed, self.world.day, i))
            p.x = max(1, min(m.size - 2, edge[0] + (i % 5) - 2))
            p.y = max(1, min(m.size - 2, edge[1] + (i // 5)))
            fi = combat.make_fighter(p, weapon, armour, f.key, 80)
            fi.role = "assault"
            fighters.append(fi)

        eng = combat.Engagement(map=m, fighters=fighters,
                                seed=rng.mix(self.world.seed, self.world.day),
                                light=1.0 if 6 < c.minute_of_day / 60 < 20 else 0.35,
                                wind_ms=c.weather.wind_ms)
        winner = eng.resolve(900.0)
        self.battle_log = eng.log[-30:]

        c.store.take("ammo", min(c.store.amount("ammo"), n * 12.0))
        if winner == "colony":
            c.note(f"{f.name}'s raid was thrown back.")
            f.relations["player"] = max(-1.0, f.relation("player") - 0.1)
            report = f"You held. {f.name} broke off."
        else:
            looted = c.store.food_kcal() * 0.4
            for key in list(c.store.stacks):
                if "food" in items.ITEMS[key].tags:
                    c.store.take(key, c.store.amount(key) * 0.4)
            c.note(f"{f.name} overran the settlement and took what they could "
                   f"carry ({looted/1e6:.1f} Mkcal of food).")
            report = f"{f.name} broke through and stripped the stores."
        dead = sum(1 for x in fighters if not x.pawn.alive and x.faction == "colony")
        if dead:
            report += f" {dead} of yours killed."
        return report

    def _check_end(self) -> None:
        if self.colony.population <= 0:
            self.over = True
            self.outcome = ("Landfall is empty. The network stopped answering "
                            f"in Y{self.world.year} D{self.world.day}.")
            return
        live = [f for f in self.world.factions
                if not f.eliminated and f.alive_settlements]
        if len(live) == 1 and live[0].is_player:
            self.over = True
            self.outcome = "You are the last network still answering."

    # ---- reporting ------------------------------------------------------

    def standings_text(self) -> str:
        rows = []
        for f, score in self.world.standings():
            if not f.alive_settlements:
                continue
            mark = ">" if f.is_player else " "
            rel = "" if f.is_player else f"  rel {f.relation('player'):+.2f}"
            rows.append(f"{mark} {f.glyph} {f.name:14s} {f.doctrine.name:12s} "
                        f"{len(f.alive_settlements):2d} settlements  "
                        f"pop {f.population:6.0f}  score {score:7.0f}{rel}")
        return "\n".join(rows)

    def intel_text(self, limit: int = 6) -> list[str]:
        """What the rivals are thinking, as far as we can tell."""
        out = []
        for f in self.world.factions:
            if f.is_player or f.eliminated or not f.intel:
                continue
            last = f.intel[-1]
            head = last.split(" | ")[0]
            out.append(f"{f.glyph} {f.name}: {head}")
        return out[:limit]
