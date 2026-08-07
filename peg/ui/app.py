"""The interactive terminal client.

Deliberately turn-based rather than real-time. PEG's interesting decisions --
what to plant, what to build before the frost, whether to answer a raid or
absorb it -- are decisions you want to think about, and a game that ticks while
you think turns them into reflexes. So the world advances when you tell it to,
in steps you choose.
"""

from __future__ import annotations

from .. import geo
from ..sim import items
from ..sim.colony import BUILDINGS
from . import render

HELP = """
  Commands
    <enter>          advance one hour
    d [n]            advance n days (default 1)
    m                the world map, with everyone on it
    s                the site map at 1 m per tile
    p [n]            people -- list, or inspect colonist n
    b                what can be built, and build it
    f x y w h        mark a field for cultivation
    i                intel: what the rival Stewards are thinking
    w                standings
    l                recent events
    ?                this
    q                quit
"""


class App:
    def __init__(self, game, pal: render.Palette):
        self.g = game
        self.pal = pal
        self.cursor = (game.colony.map.size // 2, game.colony.map.size // 2)

    # ---- main loop ------------------------------------------------------

    def run(self) -> int:
        pal = self.pal
        c = self.g.colony
        print(pal.bold("\n  PEG"))
        print(f"  Landfall: {render.compass_note(c.survey.lat, c.survey.lon)}"
              f" -- {c.survey.biome.name}, {c.survey.clim.koppen}")
        print(f"  {render.scale_note(c.map)}")
        print(pal.dim("  '?' for commands.\n"))
        self.show_status()

        while True:
            try:
                raw = input(pal.dim("  > ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not self.dispatch(raw):
                return 0
            if self.g.over:
                print()
                print("  " + pal.bold(self.g.outcome))
                return 0

    def dispatch(self, raw: str) -> bool:
        parts = raw.split()
        cmd = parts[0].lower() if parts else ""
        args = parts[1:]

        if cmd in ("q", "quit", "exit"):
            return False
        if cmd in ("?", "h", "help"):
            print(HELP)
        elif cmd == "":
            self.advance(60)
        elif cmd == "d":
            n = int(args[0]) if args and args[0].isdigit() else 1
            self.advance(n * 1440)
        elif cmd == "m":
            self.show_world()
        elif cmd == "s":
            self.show_site()
        elif cmd == "p":
            self.show_people(args)
        elif cmd == "b":
            self.build_menu(args)
        elif cmd == "f":
            self.mark_field(args)
        elif cmd == "i":
            self.show_intel()
        elif cmd == "w":
            print()
            print(self.g.standings_text())
            print()
        elif cmd == "l":
            self.show_log()
        else:
            print("  unknown command; '?' for the list")
        return True

    # ---- time -----------------------------------------------------------

    def advance(self, minutes: int) -> None:
        steps = max(1, minutes // self.g.minutes_per_step)
        before = self.g.colony.population
        for _ in range(steps):
            self.g.step()
            if self.g.over:
                break
            report = self.g.resolve_incoming()
            if report:
                print()
                print("  " + self.pal.fg(203, "RAID") + "  " + report)
                for line in self.g.battle_log[:10]:
                    print("    " + self.pal.dim(line))
                print()
        lost = before - self.g.colony.population
        if lost > 0:
            print("  " + self.pal.fg(203, f"{lost} colonist(s) died."))
        self.show_status()

    # ---- views ----------------------------------------------------------

    def show_status(self) -> None:
        pal = self.pal
        c = self.g.colony
        w, _ = render.term_size()
        width = min(96, w - 4)
        s = c.stats
        print()
        print("  " + render.rule(width, pal, render.clock(c.day, c.year,
                                                          c.minute_of_day)))
        wx = c.weather
        cond = "rain" if wx.raining else ("snow" if wx.snowing else "clear")
        print(f"  {c.population} alive   "
              f"outside {wx.temp_c:+5.1f} C {cond:5s}  "
              f"inside {c.indoor_temp():+5.1f} C   "
              f"wind {wx.wind_ms:.0f} m/s")
        print(f"  food  {render.bar(min(1.0, s.get('food_days', 0) / 200.0), 24, pal)} "
              f"{s.get('food_days', 0):5.0f} days")
        print(f"  fuel  {render.bar(min(1.0, s.get('fuel_days', 0) / 120.0), 24, pal)} "
              f"{s.get('fuel_days', 0):5.0f} days")
        print(f"  water {render.bar(min(1.0, c.water_days / 5.0), 24, pal)} "
              f"{c.water_days:5.1f} days")
        print(f"  morale{render.bar(s.get('morale', 0), 24, pal)} "
              f"{s.get('morale', 0):5.0%}   "
              f"health {s.get('health', 0):.0%}   beds {c.beds}/{c.population}")
        hurt = [p for p in c.alive if p.injuries]
        if hurt:
            print("  " + pal.fg(214, f"{len(hurt)} injured: ")
                  + ", ".join(f"{p.name.split()[0]} ({p.status()})"
                              for p in hurt[:3]))
        print("  stores: " + pal.dim(c.store.summary()))
        print()

    def show_world(self) -> None:
        from ..world import raster as raster_mod
        pal = self.pal
        r = raster_mod.get()
        w, h = render.term_size()
        width = min(w - 2, 150)
        height = max(16, min(h - 10, width // 3))
        home = self.g.home
        print()
        for row in render.world_map(r, width, height, pal,
                                    factions=self.g.world.factions,
                                    focus=(home.lon, home.lat)):
            print(row)
        print()
        for f in self.g.world.factions:
            if f.eliminated or not f.alive_settlements:
                continue
            near = f.nearest_settlement(home.lat, home.lon)
            d = geo.haversine_m(home.lat, home.lon, near.lat, near.lon) / 1000.0
            rel = "you" if f.is_player else f"{f.relation('player'):+.2f}"
            print("  " + pal.fg(f.colour, f"{f.glyph} {f.name:14s}")
                  + f" {f.doctrine.name:12s} nearest {d:6.0f} km   relation {rel}")
        print()

    def show_site(self) -> None:
        pal = self.pal
        c = self.g.colony
        w, h = render.term_size()
        width = min(c.map.size, w - 4)
        height = min(c.map.size, h - 12)
        print()
        for row in render.site_map(c.map, self.cursor[0], self.cursor[1],
                                   width, height, pal, pawns=c.alive):
            print(row)
        print("  " + pal.dim(render.scale_note(c.map)))
        if c.fields:
            for i, f in enumerate(c.fields):
                crop = f.crop.name if f.crop else "fallow"
                print(f"  field {i}: {f.area_m2} m2, {crop}, "
                      f"{f.progress:.0%} grown, {f.tended:.0%} tended")
        print()

    def show_people(self, args) -> None:
        pal = self.pal
        c = self.g.colony
        alive = c.alive
        if args and args[0].isdigit():
            i = int(args[0])
            if not (0 <= i < len(alive)):
                print("  no such colonist")
                return
            p = alive[i]
            print()
            print(f"  {pal.bold(p.name)}  {p.age:.0f}, "
                  f"{p.mass_kg:.0f} kg, {p.height_cm:.0f} cm")
            print(f"  traits: {', '.join(p.traits) or 'none'}")
            print(f"  status: {p.status()}")
            print(f"  doing:  {p.job or 'idle'}")
            skills = sorted(p.skills.items(), key=lambda kv: -kv[1].level)
            print("  skills: " + ", ".join(f"{k} {v.level}"
                                           for k, v in skills[:6]))
            caps = ", ".join(f"{n} {p.capacity(n):.0%}"
                             for n in ("consciousness", "mobility",
                                       "manipulation", "breathing"))
            print(f"  body:   {caps}")
            if p.injuries:
                for inj in p.injuries:
                    tend = "untreated" if inj.tended < 0 else f"tended {inj.tended:.0%}"
                    print(f"    {inj.kind} to the {inj.part}: "
                          f"{tend}, healed {inj.healed:.0%}, "
                          f"bleeding {inj.bleed_ml_min:.1f} mL/min, "
                          f"infection {inj.infection:.0%}")
            print()
            return
        print()
        for i, p in enumerate(alive):
            best, lvl = p.best_skill()
            print(f"  {i:2d}  {p.name:22s} {p.job or 'idle':10s} "
                  f"{best} {lvl:2d}   {p.status()}")
        print()

    def build_menu(self, args) -> None:
        pal = self.pal
        c = self.g.colony
        if args:
            key = args[0]
            if key not in BUILDINGS:
                print("  no such building")
                return
            x = int(args[1]) if len(args) > 2 else self.cursor[0]
            y = int(args[2]) if len(args) > 2 else self.cursor[1]
            b = c.order_building(key, x, y)
            if b is None:
                d = BUILDINGS[key]
                missing = [f"{k} {v - c.store.amount(k):.0f}"
                           for k, v in d.cost if not c.store.has(k, v)]
                print("  cannot afford; short of " + ", ".join(missing))
            else:
                print(f"  {b.d.name} queued at {x},{y} "
                      f"({b.d.work_min:.0f} person-minutes)")
            return
        print()
        for key, d in BUILDINGS.items():
            cost = ", ".join(f"{k} {v:.0f}" for k, v in d.cost)
            ok = c.can_afford(d)
            line = f"  {key:13s} {d.name:18s} {d.work_min:6.0f} pm   {cost}"
            print(line if ok else pal.dim(line))
        print(pal.dim("  build with:  b <key> [x y]"))
        print()

    def mark_field(self, args) -> None:
        if len(args) < 4:
            print("  usage: f x y w h")
            return
        try:
            x, y, w, h = (int(a) for a in args[:4])
        except ValueError:
            print("  usage: f x y w h")
            return
        f = self.g.colony.add_field(x, y, w, h)
        print(f"  field marked: {f.area_m2} m2 at {x},{y}")
        need = self.g.colony.population * 2400
        print(self.pal.dim(f"  subsistence for {self.g.colony.population} "
                           f"people is about {need:.0f} m2"))

    def show_intel(self) -> None:
        pal = self.pal
        w, _ = render.term_size()
        width = min(96, w - 4)
        print()
        print("  " + render.rule(width, pal, "intercepted planning traffic"))
        lines = self.g.intel_text()
        if not lines:
            print("  " + pal.dim("nothing intercepted yet"))
        for line in lines:
            for out in render.wrap(line, width - 4, "    "):
                print(out)
        print()

    def show_log(self) -> None:
        print()
        for line in self.g.colony.log[-12:]:
            print("  " + line)
        for line in self.g.world.chronicle[-6:]:
            print("  " + self.pal.dim(line))
        print()
