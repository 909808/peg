# PEG

A colony simulation on the actual Earth, one square metre at a time, against
rival AIs that are playing the same game you are.

```
python3 -m peg survey -93.5 41.9      # what is the ground like in Iowa
python3 -m peg map                    # draw the planet
python3 -m peg site -4.5 57.0         # draw one hectare of Scotland at 1 m
python3 -m peg observe                # watch the AI Stewards compete, no player
python3 -m peg play                   # run a colony
python3 -m peg fight                  # resolve one firefight
```

Python 3.11+. No dependencies, no install, no network. Clone and run.

---

## The premise

It is 2041. The networks went down two years ago and the logistics AIs that ran
the supply chains did not — they kept optimising, with nobody left to optimise
for. Each now commands a scatter of settlements somewhere on a real continent,
competing for the same rivers and the same ore.

You are one of them. So are five others.

## What makes it different

**It is the real Earth, at one metre.** Not Earth-like, not a heightmap that
resembles Earth — the actual planet, from public-domain Natural Earth vector
data: 1,312 land rings, 225 mountain range and plateau outlines, 711 named
summits at their surveyed heights, 895 river reaches. Survey a point and you
get the climate, soil and biology of that point. Land on it and you get 1 m
tiles, each of which has a real latitude and longitude.

Earth has 5.1 × 10¹⁴ square metres, so nothing is stored. There is a coarse
global raster for the strategic layer, and 1 m tiles are synthesised on demand
in 32 m chunks, positionally hashed so that tile (x, y) of a site depends only
on the world seed and where it is — never on when or in what order you looked.

**Nothing about the world is painted on.** There is no "desert" flag anywhere
in the codebase. The Sahara is dry because that is where the Hadley cell
subsides; the Atacama is dry because the Humboldt current chills the air over
it; the Gobi is dry because it is 2,000 km from any sea with the Himalayas in
the way. Climate is computed from insolation, general circulation, monsoons,
orography and ocean currents, then classified Köppen-Geiger. Biome and soil
fall out of climate. That means a rain shadow you can see on the map really is
dry, and a colony sited in one really does starve.

Validated against 48 real cities: **85% Köppen group-letter accuracy**, 2.6 °C
median temperature error, 242 mm median precipitation error.

**Soil fertility is close to inverted from lushness.** The Amazon is the most
productive ecosystem on Earth and among its worst farmland, because a century
of rain has leached the nutrients out of the ground and into the trees. It gets
oxisol at 0.22. The treeless Ukrainian steppe gets mollisol at 0.92. Clear
rainforest for cropland and you get two good harvests and then nothing — which
is what happens to people who try it.

**The failure modes are the real ones.** People die of blood loss in minutes,
sepsis in days, hypothermia in hours, starvation in weeks, and scurvy in about
three months. Each runs on its own clock, and a colony can be comfortably
winning on four of them while losing on the fifth. A larder full of grain and
dried meat is a scurvy death; the answer is potatoes and a cold store, which is
the answer people actually found.

**Combat is geometry, not percentages.** A weapon has a bullet mass, a muzzle
velocity, a ballistic coefficient and a dispersion in MOA. Your shooter's own
wobble — 16 MOA at skill 0, about 1 at skill 20 — dominates the weapon's.
Together they project to a group size at range, and hitting is that group
against the target's silhouette. A 9 mm carries 518 J at the muzzle and 158 J
at 100 m; a .308 carries 3,261 J and 2,150 J. Both fall out of the drag model.

There are no hit points. A round that connects meets an armour and either
defeats it or delivers blunt trauma behind it. People stop fighting from shock,
blood loss and broken bones, so most casualties are wounded rather than killed
— and a wounded colonist is a labour problem for the next three months.

**Terrain decides the character of a fight.** Twenty trials, five dug-in
defenders against seven raiders: in a wooded glen the raiders win 18/20 in 96
seconds with 30 dead and 90 incapacitated; on open prairie they win 20/20 in 42
seconds with *no deaths at all* and 62 broken and run, because open ground is
decided by volume of fire and suppression.

**The rivals are not a difficulty setting.** A Steward calls
`peg.world.site.survey()` with the same arguments you do and gets the same
numbers. It has no map knowledge you could not get by walking there, and it
only knows about settlements inside its scouting range. What differs is
*doctrine* — how it weighs food against ore, defensibility against reach.

Every decision records the utilities that produced it, and `observe` and the
in-game intel view print them:

```
expand (0.28): found a colony at +32.64,-91.20 (mediterranean shrubland, 98 km)
               [surplus +1.50, crowding +1.00, logistics +0.64, site +0.53]
```

You can read that, work out that HARROW is agrarian, that it values water above
ore, and that the valley upstream is worth claiming before it gets there.

## Running it

```
$ python3 -m peg survey -93.5 41.9

  +41.900, -93.500  166 m  temperate grassland  Dfa (hot-summer continental)
    soil     mollisol, fertility 0.81 (deep black grassland soil; the best
             cropland on Earth)
    climate  12.2 C mean, -6.5 to 31.1, 469 mm/yr, 230 growing days
    land     slope 0.0 deg, arable 81%, timber 2 m3/ha
    minerals coal 0.59, clay 0.46, salt 0.46

  growing season 230 days, 2549 GDD, frost-free 267 days
  viable crops:  wheat (100%), potatoes (94%), barley (100%), oats (100%)
```

`observe` is the mode that shows the competition for what it is — no player, no
scripting, several planners with different doctrines on one continent and
whatever comes of that. On seed 11 the militarist COMPASS won a twenty-year war
with eight settlements and 403 conflict events. On seed 22 nobody could take
anybody and three networks were still standing.

The interactive client is turn-based on purpose. The interesting decisions —
what to plant, what to build before the frost, whether to answer a raid or
absorb it — are ones you want to think about, and a game that ticks while you
think turns them into reflexes.

## Layout

```
peg/rng.py            positional hashing, Perlin/fBm/ridged/worley noise
peg/geo.py            WGS84 geodesy, tangent-plane metre frames, solar geometry
peg/world/earth.py    loader for the baked Natural Earth dataset (120 KiB)
peg/world/raster.py   global raster: landmask, coast distance, elevation
peg/world/climate.py  the climate model and Köppen classification
peg/world/biome.py    19 biomes, 13 soil orders
peg/world/site.py     the survey — the shared perception of the planet
peg/local/terrain.py  1 m tiles, chunked and lazy
peg/sim/items.py      materials, nutrition, crops, recipes, all in real units
peg/sim/pawn.py       bodies, capacities, injuries, metabolism
peg/sim/colony.py     work allocation, building, farming, heating, water
peg/sim/combat.py     ballistics, cover, suppression, tactics
peg/meta/faction.py   factions, doctrines, settlements
peg/meta/steward.py   the rival planner
peg/meta/world.py     the strategic layer
peg/game.py           binds the two scales together
peg/ui/               ANSI rendering and the terminal client
tools/bake_earth.py   build-time: refresh the Earth dataset (needs network)
```

```
python3 -m unittest discover -s tests -t .     # 86 tests
```

## Honest limits

See `docs/DATA.md` for provenance and `docs/DESIGN.md` for the modelling
decisions. The short version of what this does *not* do well:

- **Elevation between the anchors is plausible, not surveyed.** There is no
  free global DEM that is reachable and small enough to ship, so heights are
  synthesised from real range outlines and real summits. Median error against
  27 reference cities is 243 m. It is worst in intermontane basins, where a
  55 km raster cell averages ridge and valley together — Las Vegas comes out
  800 m too high, and therefore 12 °C too cold.
- **Ocean currents are hand-placed.** Fifteen of them. They are the one part of
  Earth's climate that is a historical accident of basin shape rather than
  something a zonal model derives, and without them you lose the Atacama, the
  Namib and an ice-free Norway.
- **Rival settlements are abstracted.** Simulating six factions tile by tile
  would cost a thousand times what it is worth. They advance on the same
  arithmetic the detailed colony obeys, at a coarser grain; when you attack one
  it is instantiated in full and fought out metre by metre.
- **The world is harsh.** Networks fail. In a twenty-year run it is common for
  two or three of six to be gone, usually to a bad first winter. That is
  intended for a post-collapse setting, but the balance is the least-tested
  part of the game and the number most likely to want tuning.

## Data

Earth comes from [Natural Earth](https://www.naturalearthdata.com/), which is
public domain. The baked dataset ships in the repository so the game runs
offline; `tools/bake_earth.py` regenerates it.
