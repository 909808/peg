# PEG

A colony simulation on the actual Earth, one square metre at a time, against
rival AIs that are playing the same game you are.

```
peg                      # a menu, if you would rather not learn the commands
peg gui                  # the graphical client, in your browser
peg play                 # the same game, in a terminal
peg observe              # watch the AI Stewards fight it out, no player
peg survey -93.5 41.9    # what the ground is really like in Iowa
peg map                  # draw the planet
peg site -4.5 57.0       # one hectare of Scotland, at one metre per tile
peg fight                # resolve a single firefight
```

Python 3.11 or newer. No dependencies, no network, nothing to configure.

---

## Getting it running

Pick whichever of these sounds least annoying. They all end up in the same
place.

### 1. Just double-click it

Download the repository, then double-click **`run.sh`** (macOS, Linux) or
**`run.bat`** (Windows). That is it — the launcher finds your Python and starts
the game. From a terminal, `./run.sh` does the same thing.

### 2. One file you can copy anywhere

```
python3 tools/build.py        # writes dist/peg.pyz, about 240 KiB
python3 dist/peg.pyz          # and that is the whole game
```

`peg.pyz` is a single file with everything inside it, Earth included. Put it on
a memory stick, email it to yourself, run it from any folder. Anyone with
Python 3.11 can run it — they do not need the source, and there is nothing to
install.

### 3. Install it properly

```
pip install .
peg
```

That gives you a real `peg` command that works from any directory. To keep it
isolated from the rest of your system — a good habit, and the thing most guides
mean by "use a virtual environment":

```
python3 -m venv .venv
source .venv/bin/activate          # on Windows:  .venv\Scripts\activate
pip install .
peg
```

### 4. Straight from the source folder

```
python3 -m peg
```

No install at all, but it only works from inside the project folder.

---

### If something goes wrong

**`python3: command not found`** — you do not have Python yet. Get it from
[python.org/downloads](https://www.python.org/downloads/). On Windows, tick
*"Add Python to PATH"* during setup.

**`peg: command not found` after `pip install .`** — the install worked, but the
folder it put `peg` into is not on your PATH. `python3 -m peg` always works
instead.

**The first run pauses for a few seconds** — it is building the world raster
from the Earth data. It is cached afterwards (`~/.cache/peg`, or
`%LOCALAPPDATA%\peg` on Windows) and every later run starts instantly. Delete
that folder if you ever want it rebuilt.

**The map looks like mush** — the terminal window is too narrow, or it does not
do colour. Widen the window, or use `peg --no-colour map` for a plain-text
version.

---

## The interface

`peg gui` opens a graphical client in your browser. No install, no toolkit —
PEG runs a small local server and the browser is the window.

**You should always know where you are and what you are doing.** The title bar
names the biome you are standing in, your latitude and longitude, and the
nearest real place — *"57.000°N 4.500°W · Cfb · near Ben Nevis (1343 m) 38 km
away"*. A world map in the corner shows the same thing at planetary scale, with
your site as a gold dot and every rival network beside it. A **what to do next**
panel keeps a short, ordered list of what the colony actually needs — *"2 beds
for 8 people. Sleeping out costs calories and morale."* — and **Find my people**
snaps the camera to wherever your colonists have wandered off to. A one-screen
briefing explains the premise the first time you open the page.

**It has textures, and they ship inside the page.** Colonists, animals, trees,
rocks and ore seams are hand-drawn pixel sprites; ground is baked at six
subpixels a tile with a surface pattern that belongs to its material, so grass
has tufts, gravel has chips and water has ripple bands. Nothing is fetched from
a CDN and no image files ship: a sprite is rows of characters indexing a
palette, painted once into a cached canvas. That keeps the whole game one
dependency-free file that works offline — and it means a colonist's skin, hair
and coat can be recoloured per person from one drawing, so eight people look
like eight people. Colonists face the way they are walking.

**It runs at sixty frames a second.** The whole site is fetched once as packed
bytes and baked to an offscreen canvas; panning and zooming redraw locally and
make **no network requests at all**. A frame costs 1–3 ms against a 16.7 ms
budget, and the page is interactive in about a second. Sprites are drawn only
above the zoom where they are legible — below that the object tint baked into
the ground already reads as woodland, and there can be twenty thousand trees on
screen. An earlier version fetched a viewport on every keystroke and rendered
three thousand individual `arc()` calls per frame, which is exactly as bad as
it sounds.

**Axes are X / Z / Y.** X is east, Z is north, **Y is up**. The default view is
the **X/Z plane**, top-down, looking at the ground. The switch at the top left
(or <kbd>Tab</kbd>) changes the coordinate plane to one of two elevation views:

| view | what it shows |
|---|---|
| **X / Z** | top-down. The ground, hillshaded, with trees, boulders, ore, buildings, fields and people. |
| **X / Y** | a cross-section looking north, cut at a chosen Z. |
| **Z / Y** | a cross-section looking east, cut at a chosen X. |

The elevation views are drawn to scale in metres, which is where "one metre per
tile" stops being a claim and starts being visible: a 28 m spruce next to a
1.7 m colonist, on ground that actually slopes, over a soil horizon of the
depth the soil model says it has, on bedrock. `[` and `]` walk the cutting
plane through the site.

Clicking any tile in the top-down view reports what is on it and **where on
Earth it is** — down to that individual square metre:

```
X / Z            30, 31
Y (elevation)    135.93 m
lat / lon        56.99956, -4.500823
terrain          soil
fertility        0.28
```

Controls: <kbd>WASD</kbd> or arrows to pan, <kbd>+</kbd>/<kbd>−</kbd> or the
wheel to zoom, drag to pan, <kbd>Tab</kbd> to switch plane, click to inspect.
The metre grid appears once you are zoomed in far enough for it to mean
something.

Colonists walk to their work and stand where they are working, so the top-down
view reads as a settlement rather than a pile of tokens: you can see who is out
in the field, who is at the woodpile and who is inside. Labels appear on hover
rather than permanently, because eight names stacked on one cabin is not
information.

The **Your people** panel is the roster: name, age, best skill, traits, what
they are doing right now, whether they are hurt, ill or expecting, and who they
are close to — *"Zane Beaumont, 53 · construction 6 · asthmatic, anxious ·
dislikes Lars Espinoza (−19)"*. All of that was already in the simulation and
none of it was reaching the screen.

There is still a full terminal client (`peg play`) — it is genuinely useful
over SSH, and it is what the headless `peg observe` mode is built on.

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

So are the problems. Two tonnes of potatoes come in every August and keep for
four months, so the question is not whether you can grow food but whether you
can still eat it in March — dried into rations, or held in a cold store, or
composted. And Iowa, the best cropland on the planet, has no trees on it: a
prairie colony heats itself by cutting and twisting hay at sixty times the
labour per kilogram of felling timber, which is what actually happened to the
people who settled it.

**The animals close the loops.** Livestock is not a side activity here, it is
the thing that joins the other systems together. Grass becomes hay; hay
becomes a cow through a winter; the cow becomes milk every morning, dung for
the fire, manure for the field, wool for the coats and, eventually, meat. Take
any one of those away and something else stops working — which is exactly why
a smallholding kept animals it could barely feed.

The numbers are the real ones and the consequences follow from them without
being authored. A dairy cow eats 12 kg of dry matter a day, so a four-month
winter is a tonne and a half of hay per cow, which at 3.5 person-minutes a
kilogram is why you keep three cattle and not thirty. When autumn comes and
the hayrick will not cover the herd, the colony counts the mouths against the
hay and butchers the difference — in November, which is what Martinmas *was*.

**People are attached to each other.** Colonists form opinions from working
alongside one another, and the opinions are asymmetric, because unrequited
regard is a real thing. Long high regard makes couples; couples and enough
food make children; children cost fifteen years and then become the colony.
And when someone dies, everyone who cared about them grieves for about four
months — which lowers morale, which slows work, which is the mechanism by
which a raid in November costs you the spring sowing.

**Illness comes from your own decisions, not from an event deck.** There are
two diseases and both are bills for something the player chose. Enteric
infection comes from drinking surface water you did not spend the fuel to
boil. Influenza comes from cold, exhaustion and too many people in too few
beds. Each runs as a race between severity and immunity, where the immunity
side is set by how well fed, rested and warm the patient is — so an outbreak
passes through a well-provisioned colony in a week and empties a badly
provisioned one. Nursing does not cure anyone; it shifts the rate, which is
what nursing did.

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
peg/sim/pawn.py       bodies, capacities, injuries, metabolism, growing up
peg/sim/livestock.py  the animal economy: feed, milk, wool, dung, manure
peg/sim/social.py     opinions, couples, children, grief
peg/sim/disease.py    the immunity race, and the two diseases that killed people
peg/sim/colony.py     work allocation, building, farming, heating, water
peg/sim/combat.py     ballistics, cover, suppression, tactics
peg/meta/faction.py   factions, doctrines, settlements
peg/meta/steward.py   the rival planner
peg/meta/world.py     the strategic layer
peg/game.py           binds the two scales together
peg/ui/render.py      ANSI rendering for the terminal client
peg/ui/app.py         the terminal client
peg/ui/viewdata.py    payload builders for the GUI (X/Z/Y axis boundary)
peg/ui/server.py      the GUI's local HTTP server
peg/ui/web/           the browser client: one page, canvas, no dependencies
tools/bake_earth.py   build-time: refresh the Earth dataset (needs network)
tools/build.py        build-time: bundle everything into dist/peg.pyz
run.sh, run.bat       double-click launchers
```

```bash
python3 -m unittest discover -s tests -t .    # 128 tests
python3 tools/build.py                        # single-file build
python3 tools/bake_earth.py                   # refresh Earth data (needs network)
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
