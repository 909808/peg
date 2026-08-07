"""PEG command line.

    python3 -m peg survey  <lon> <lat>     what is the ground like there
    python3 -m peg map                     draw the planet
    python3 -m peg site    <lon> <lat>     draw one hectare of it at 1 m
    python3 -m peg observe                 watch the Stewards compete, headless
    python3 -m peg play                    run a colony
    python3 -m peg fight                   run a firefight
"""

from __future__ import annotations

import argparse
import sys
import time

from . import geo
from .ui import render


def _pal(args) -> render.Palette:
    return render.Palette(False if getattr(args, "no_colour", False) else None)


# --------------------------------------------------------------------------
# survey
# --------------------------------------------------------------------------


def cmd_survey(args) -> int:
    from .world import site as site_mod
    pal = _pal(args)
    s = site_mod.survey(args.lon, args.lat, args.seed)
    print()
    print(pal.bold(f"  Survey: {render.compass_note(s.lat, s.lon)}"))
    print()
    for line in s.summary().splitlines():
        print("  " + line)
    print()
    width = min(76, render.term_size()[0] - 4)
    print("  " + render.rule(width, pal, "climate by month"))
    c = s.clim
    from .world import climate as climate_mod
    hdr = "         " + "".join(f"{m:>6s}" for m in climate_mod.MONTH_NAMES)
    print("  " + pal.dim(hdr))
    print("  temp   " + "".join(f"{t:6.1f}" for t in c.temp_c))
    print("  precip " + "".join(f"{p:6.0f}" for p in c.precip_mm))
    print()
    print(f"  growing season {c.growing_days} days, "
          f"{c.growing_degree_days():.0f} GDD, "
          f"frost-free {c.frost_free_days} days")

    from .sim import items
    crops = items.best_crops(c)
    if crops:
        print("  viable crops:  " + ", ".join(
            f"{cr.name} ({su:.0%})" for cr, su in crops[:5]))
    else:
        print("  viable crops:  " + pal.fg(203, "none -- nothing will ripen here"))

    print()
    print("  " + render.rule(width, pal, "settlement scores by doctrine"))
    for doc in ("agrarian", "industrial", "militarist", "mercantile", "survivalist"):
        sc, parts = site_mod.score(s, doc)
        bits = " ".join(f"{k[:4]} {v:.2f}" for k, v in sorted(parts.items()))
        print(f"  {doc:12s} {sc:5.1f}  {pal.dim(bits)}")
    print()
    return 0


# --------------------------------------------------------------------------
# map
# --------------------------------------------------------------------------


def cmd_map(args) -> int:
    from .world import raster as raster_mod
    pal = _pal(args)
    r = raster_mod.get(args.res, args.seed, verbose=True)
    w, h = render.term_size()
    width = args.width or min(w - 2, 160)
    height = args.height or max(18, min(h - 6, width // 3))
    focus = (args.lon, args.lat) if args.lon is not None else None
    for row in render.world_map(r, width, height, pal, focus=focus):
        print(row)
    print(pal.dim(f"  {width}x{height} view of Earth "
                  f"({raster_mod.DEFAULT_RES if not args.res else args.res} deg raster, "
                  f"{r.cols*r.rows:,} cells). "
                  f"Earth has {geo.EARTH_AREA_M2:.3g} square metres."))
    return 0


# --------------------------------------------------------------------------
# site
# --------------------------------------------------------------------------


def cmd_site(args) -> int:
    from .local import terrain
    from .world import site as site_mod
    pal = _pal(args)
    s = site_mod.survey(args.lon, args.lat, args.seed)
    if s.is_ocean:
        print("That point is at sea. Pick somewhere with ground on it.")
        return 1
    m = terrain.generate(s, args.size, seed=args.seed)
    tw, th = render.term_size()
    width = min(args.size, tw - 2)
    height = min(args.size, th - 8)
    print()
    print("  " + pal.bold(f"{s.biome.name} at {render.compass_note(s.lat, s.lon)}"))
    print("  " + pal.dim(render.scale_note(m)))
    print()
    for row in render.site_map(m, args.size // 2, args.size // 2,
                               width, height, pal):
        print(row)
    print()
    print(f"  soil {s.soil.name} (fertility {s.soil.fertility:.2f}), "
          f"slope {s.slope_deg:.1f} deg, "
          f"timber {s.timber_m3_ha:.0f} m3/ha")
    print()
    return 0


# --------------------------------------------------------------------------
# observe
# --------------------------------------------------------------------------


def cmd_observe(args) -> int:
    """Run the rival Stewards against each other with nobody playing.

    This is the mode that shows the competition for what it is: no player, no
    scripting, just several planners with different doctrines on one continent,
    and whatever comes of that.
    """
    from .meta import world as world_mod
    pal = _pal(args)

    print(pal.bold(f"\n  PEG -- {args.years} years, {args.rivals} Stewards, "
                   f"seed {args.seed}\n"))
    t0 = time.time()
    w = world_mod.new_world(args.seed, rivals=args.rivals, verbose=True)

    # In observer mode the player's own network is run by a Steward too,
    # otherwise it simply sits there and starves.
    from . import rng
    from .meta.steward import Steward
    player = w.player
    if player is not None:
        w.stewards.append(Steward(player, rng.mix(args.seed, 999)))

    print()
    width = min(96, render.term_size()[0] - 2)
    milestone = max(1, args.years // 10)
    for year in range(args.years):
        for _ in range(365):
            w.advance(1)
        if year % milestone == 0 or year == args.years - 1:
            print(render.rule(width, pal, f"year {w.year}"))
            for f, score in w.standings():
                if not f.alive_settlements:
                    continue
                pop = f.population
                print("  " + pal.fg(f.colour, f"{f.glyph} {f.name:12s}")
                      + f" {f.doctrine.name:12s} "
                      f"{len(f.alive_settlements):2d} settlements  "
                      f"pop {pop:6.0f}  score {score:7.0f}")

    print()
    print(render.rule(width, pal, "chronicle"))
    conflicts = [c for c in w.chronicle
                 if any(k in c.lower() for k in ("raid", "seiz", "threw back",
                                                 "align", "collapsed"))]
    for line in conflicts[-14:]:
        print("  " + line)
    if not conflicts:
        print("  " + pal.dim("no wars. the networks divided the ground and "
                             "left each other alone."))

    print()
    print(render.rule(width, pal, "why the leader did what it did"))
    lead = w.leader()
    if lead is not None and lead.intel:
        for chunk in lead.intel[-1].split(" | "):
            for line in render.wrap(chunk, width - 4, "    "):
                print(line)
    print()
    print(pal.dim(f"  simulated {args.years} years in {time.time()-t0:.1f}s"))
    print()
    return 0


# --------------------------------------------------------------------------
# play
# --------------------------------------------------------------------------


def cmd_play(args) -> int:
    from .game import Game
    g = Game.new(seed=args.seed, lon=args.lon, lat=args.lat,
                 rivals=args.rivals, size=args.size)
    if args.days:
        g.run_headless(args.days)
        return 0
    from .ui.app import App
    return App(g, _pal(args)).run()


# --------------------------------------------------------------------------
# fight
# --------------------------------------------------------------------------


def cmd_fight(args) -> int:
    from .local import terrain
    from .sim import combat
    from .sim.pawn import Pawn
    from .world import site as site_mod
    pal = _pal(args)

    s = site_mod.survey(args.lon, args.lat, args.seed)
    m = terrain.generate(s, 128, seed=args.seed, eager=True)
    fighters = []
    for i in range(args.defenders):
        p = Pawn.random(args.seed * 31 + i)
        p.x, p.y = 24 + i * 3, 24
        f = combat.make_fighter(p, args.defender_weapon, "padded", "defenders", 60)
        f.role = "hold"
        fighters.append(f)
    for i in range(args.attackers):
        p = Pawn.random(args.seed * 71 + i)
        p.x, p.y = 100 + i * 3, 100
        f = combat.make_fighter(p, args.attacker_weapon, "none", "attackers", 120)
        f.role = "assault"
        fighters.append(f)

    e = combat.Engagement(map=m, fighters=fighters, seed=args.seed)
    print(f"\n  {args.defenders} defenders ({combat.WEAPONS[args.defender_weapon].name}) "
          f"vs {args.attackers} attackers "
          f"({combat.WEAPONS[args.attacker_weapon].name})")
    print(f"  {s.biome.name} at {render.compass_note(s.lat, s.lon)}\n")
    winner = e.resolve(900.0)
    for line in e.log[:40]:
        print("   " + line)
    print()
    print(f"  {pal.bold(winner or 'stalemate')} after {e.elapsed_s:.0f} seconds")
    dead = sum(1 for f in fighters if not f.pawn.alive)
    down = sum(1 for f in fighters if f.pawn.alive and f.pawn.incapacitated)
    routed = sum(1 for f in fighters if f.routed and f.pawn.alive)
    hurt = sum(1 for f in fighters
               if f.pawn.alive and not f.pawn.incapacitated and f.pawn.injuries)
    print(f"  {dead} killed, {down} incapacitated, {hurt} wounded, {routed} broke\n")
    return 0


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="peg",
        description="PEG -- a colony simulation on the actual Earth, "
                    "one metre at a time.")
    p.add_argument("--seed", type=int, default=20410,
                   help="world seed (default 20410)")
    p.add_argument("--no-colour", action="store_true", dest="no_colour")
    # Not required: running "peg" bare opens the menu instead of printing a
    # usage error, which is the friendlier answer for anyone who has just
    # installed it and does not yet know the subcommands.
    sub = p.add_subparsers(dest="cmd", required=False)

    s = sub.add_parser("survey", help="report on a point of the Earth")
    s.add_argument("lon", type=float)
    s.add_argument("lat", type=float)
    s.set_defaults(func=cmd_survey)

    s = sub.add_parser("map", help="draw the planet")
    s.add_argument("--res", type=float, default=0.5)
    s.add_argument("--width", type=int, default=0)
    s.add_argument("--height", type=int, default=0)
    s.add_argument("--lon", type=float, default=None)
    s.add_argument("--lat", type=float, default=None)
    s.set_defaults(func=cmd_map)

    s = sub.add_parser("site", help="draw one site at 1 m per tile")
    s.add_argument("lon", type=float)
    s.add_argument("lat", type=float)
    s.add_argument("--size", type=int, default=192)
    s.set_defaults(func=cmd_site)

    s = sub.add_parser("observe", help="watch the Stewards compete, no player")
    s.add_argument("--years", type=int, default=25)
    s.add_argument("--rivals", type=int, default=5)
    s.set_defaults(func=cmd_observe)

    s = sub.add_parser("play", help="run a colony")
    s.add_argument("--lon", type=float, default=None)
    s.add_argument("--lat", type=float, default=None)
    s.add_argument("--rivals", type=int, default=5)
    s.add_argument("--size", type=int, default=160)
    s.add_argument("--days", type=int, default=0,
                   help="run headless for N days instead of interactively")
    s.set_defaults(func=cmd_play)

    s = sub.add_parser("fight", help="resolve one firefight")
    s.add_argument("--lon", type=float, default=-4.5)
    s.add_argument("--lat", type=float, default=57.0)
    s.add_argument("--defenders", type=int, default=5)
    s.add_argument("--attackers", type=int, default=7)
    s.add_argument("--defender-weapon", default="hunting_rifle")
    s.add_argument("--attacker-weapon", default="assault_rifle")
    s.set_defaults(func=cmd_fight)
    return p


# --------------------------------------------------------------------------
# the menu you get when you just run "peg"
# --------------------------------------------------------------------------

#: A few places worth looking at, so nobody has to know coordinates to start.
PLACES = (
    ("Iowa prairie",       -93.5,  41.9, "the best farmland on Earth"),
    ("Scottish glen",       -4.5,  57.0, "timber, rain, and nothing to eat"),
    ("Congo basin",         15.3,  -4.3, "lush, and terrible soil"),
    ("Sahara",              10.0,  23.0, "as bad as it sounds"),
    ("Nile valley",         31.2,  27.0, "desert plus a river"),
    ("Pampas",             -60.0, -34.0, "grassland, mild winters"),
    ("Anchorage, Alaska",  -149.9, 61.2, "hard: short season, scurvy country"),
    ("Ukrainian steppe",    32.0,  49.0, "black earth"),
)


def _ask(prompt: str, default: str = "") -> str:
    try:
        got = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(0)
    return got or default


def _pick_place(pal: render.Palette) -> tuple[float, float]:
    print()
    for i, (name, lon, lat, note) in enumerate(PLACES, 1):
        print(f"   {i}) {name:20s} {pal.dim(note)}")
    print(f"   {len(PLACES)+1}) somewhere else (enter coordinates)")
    choice = _ask("\n  where? [1] ", "1")
    if choice.isdigit() and 1 <= int(choice) <= len(PLACES):
        _, lon, lat, _ = PLACES[int(choice) - 1]
        return lon, lat
    lon = float(_ask("  longitude (-180 to 180): ", "-93.5"))
    lat = float(_ask("  latitude  (-90 to 90):  ", "41.9"))
    return lon, lat


MENU = """
  1) Play           run a colony, with rivals competing on the same continent
  2) Watch          let the AI Stewards fight it out, no player
  3) Survey a place what the ground, climate and soil are really like
  4) See the world  draw the planet
  5) See a site     one hectare of it, at one metre per tile
  6) A firefight    resolve a single engagement
  q) Quit
"""


def menu(argv_seed: int, no_colour: bool) -> int:
    """Interactive launcher. Everything here is also a subcommand."""
    pal = render.Palette(False if no_colour else None)
    print(pal.bold("\n  PEG"))
    print("  A colony simulation on the actual Earth, one square metre at a time.")
    print(pal.dim("  Everything below is also a command, e.g.  peg survey -93.5 41.9"))
    print(MENU)

    choice = _ask("  what would you like to do? [1] ", "1").lower()
    argv: list[str] = ["--seed", str(argv_seed)]
    if no_colour:
        argv.append("--no-colour")

    if choice in ("q", "quit", "exit"):
        return 0
    if choice == "1":
        lon, lat = _pick_place(pal)
        argv += ["play", "--lon", str(lon), "--lat", str(lat)]
    elif choice == "2":
        years = _ask("  how many years? [25] ", "25")
        argv += ["observe", "--years", years]
    elif choice == "3":
        lon, lat = _pick_place(pal)
        argv += ["survey", str(lon), str(lat)]
    elif choice == "4":
        argv += ["map"]
    elif choice == "5":
        lon, lat = _pick_place(pal)
        argv += ["site", str(lon), str(lat)]
    elif choice == "6":
        argv += ["fight"]
    else:
        print("  didn't understand that, sorry.")
        return 1
    return main(argv)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "cmd", None) is None:
        if not sys.stdin.isatty():
            # Piped or redirected: a menu would hang waiting for input.
            build_parser().print_help()
            return 0
        try:
            return menu(args.seed, args.no_colour)
        except KeyboardInterrupt:
            print("\ninterrupted")
            return 130
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
