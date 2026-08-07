# Design notes

Why PEG is built the way it is, and what each decision cost.

## The scale problem

Earth is 5.1 × 10¹⁴ square metres. At one byte per tile that is half a
petabyte, so the world cannot be stored, and there is no point pretending
otherwise. PEG uses an LOD chain instead:

```
Natural Earth vectors  ->  0.5 deg raster  ->  survey  ->  1 m tiles
      (120 KiB)            (259,200 cells)   (per point)   (32 m chunks)
```

The raster is built once and cached. A **survey** is a point query that
combines raster values with exact vector queries and procedural detail. The
1 m layer is generated lazily and positionally, so it never has to exist.

Two decisions follow from this that are worth stating:

**Tiles are metric, not projected.** A single global 1 m grid is impossible —
meridians converge and cells stop being square long before the poles. Faking it
with a Mercator grid, where a "metre" is 1 m at the equator and 30 cm at
Reykjavik, would quietly corrupt every distance, area and yield calculation in
the game. So each site is a tangent plane anchored at a real lat/lon with axes
east and north, graduated in exact metres. Over a few kilometres the error
against the ellipsoid is millimetres. Cross-site travel converts back to
geodetic and uses great-circle maths.

**Randomness is positional, not sequential.** Nothing advances a shared RNG
stream. Everything is `hash(seed, purpose_tag, x, y)`. This is slightly slower
per call and enormously simpler to reason about: order of exploration cannot
change the world, chunks can be generated in any order, and a saved game does
not need to store the world.

## Derive, don't paint

There is no `is_desert` flag anywhere. Climate is computed from physics, biome
from climate, soil from biome and terrain, and what grows from soil and
climate. The chain is longer than a lookup table and much more work to get
right — but it buys the thing that makes the setting worth having: **the map
means something**. A rain shadow you can see is dry. A river valley in an arid
belt is farmland. A cold current offshore makes a coastal desert.

It also makes the model falsifiable, which is why `tests/test_climate.py`
checks it against 48 real cities rather than against itself.

### What this cost

Roughly half the development time, and four rounds of the same lesson: a term I
had left out was not a refinement, it was load-bearing.

- No **east-margin moisture** term, and every humid subtropical climate came out
  as desert: Tokyo, Shanghai, Atlanta, Buenos Aires and Sydney all arid.
- No **summer convection over continents**, and the American Midwest classified
  Mediterranean, because the only warm-season rainfall mechanism was a storm
  track that had walked north for the summer.
- No **equatorward warm-sea pathway**, and Iowa and Ulaanbaatar looked identical
  to the model despite one getting three times the rain. The difference is not
  distance from the sea, it is that Mongolia's fetch is blocked by 2,000 km of
  Asia.
- Growing degree-days computed at a single 10 °C base and compared against crops
  whose base is 2–5 °C, so cool maritime climates supported *no crop at all* and
  starved colonies that had no business starving.

## Simulate what the player decides about

The detailed colony runs on one-minute ticks with work in person-minutes,
because that is the grain at which the player's decisions have consequences:
fourteen adults working ten hours is 8,400 person-minutes, and a winter's
firewood is about 1,200 of them.

Rival factions do **not** get this treatment. Six of them tile by tile would
cost a thousand times what it is worth and the player would never see it. They
advance on the same arithmetic — calories in against calories burned,
person-minutes against work required — at a coarser grain. The two have to
agree closely enough that a player who switches from watching a rival to
fighting one does not discover the numbers were fiction.

When you attack a rival settlement, it is instantiated in full and fought out
metre by metre. That is the one moment the scales meet, and the conversion is
deliberately honest: a strategic strength of 12 becomes about six armed adults,
not a number that scales with difficulty.

## Making the AI legible rather than strong

An unbeatable AI is easy and not interesting. The design goal was that a player
should be able to *read* a rival and act on the reading.

Every Steward scores its options with a small, transparent utility function and
records the components:

```
expand (0.28): found a colony at +32.64,-91.20 (mediterranean shrubland, 98 km)
               [surplus +1.50, crowding +1.00, logistics +0.64, site +0.53]
```

They also have no privileged information. A Steward calls the same
`site.survey()` the player does, and `observe()` limits it to settlements within
scouting range — which is why a distant rival can grow large before anyone
reacts, and why scouting range is worth researching.

### What went wrong, in order

The AI took more debugging than everything else combined, and every failure was
the same shape: a planner that was locally sensible and globally suicidal.

1. **Factions on separate continents.** Twenty years, zero conflict — nobody
   could see anybody. Contact is not a detail of the design, it is the design.
   Everyone now starts in one theatre, 170–620 km apart.
2. **Farms built only once already hungry.** Urgency keyed on current food, so
   nobody planted until the stores ran low, i.e. in October. Urgency now keys on
   the gap between cultivated area and subsistence area.
3. **Farm utility multiplied by land quality.** A network on middling soil rated
   farming below routine construction and never broke ground at all. People
   short of food farm whatever they have; quality sets the yield, not the
   decision.
4. **Full stores read as "safe".** A settlement with a year of grain and no
   fields is not secure, it is on a countdown. Stores now earn capped credit.
5. **One action per faction per week regardless of size.** An eleven-settlement
   network tended each settlement once a quarter and starved of administrative
   neglect. The action budget now scales with the network.
6. **Expansion far too cheap.** With a parallel budget, Stewards expanded every
   week they could and every faction was dead by its second winter. Expansion
   now needs security, spare people, and more than a year of food — and the
   daughter colony leaves with a year's stores, because one sent out with forty
   days arrives, plants nothing in time, and dies.
7. **Attacks that could never clear the odds threshold**, because only the
   nearest settlement contributed force. Concentration from every settlement in
   range made war possible; raiding-while-desperate made it likely.
8. **No food logistics.** These are *logistics AIs*; the first thing any of them
   would do is move grain from the settlement that has it to the one that does
   not. Without it, daughter colonies starved within sight of a full granary.

### What went wrong in the colony, in order

The same shape of failure as the AI, one level down: rules that were locally
reasonable and collectively fatal. Each of these was found by tracing a single
settlement day by day for two years and asking why a number was what it was.

1. **Eating pre-empted work, and could be retried every tick.** A hungry
   colonist ate and their tick ended. With the larder nearly empty everyone's
   calorie debt sits permanently above the threshold, so the whole settlement
   spent every waking minute eating the few grams the foragers brought in, and
   nobody farmed, cut fuel or cooked their way out of it. Six people starved
   with the workforce fully employed. Meals now cost 25 minutes out of the tick
   and people eat about four times a day, like people.
2. **The kitchen minted calories.** Cooking turned 1,260 kcal of grain and
   cabbage into a 2,400 kcal dinner; drying turned 1,140 kcal of vegetables
   into a 3,000 kcal ration. Rations and meals also weighed half a kilogram
   each while their nutrition was counted per kilogram, so every batch was
   worth double on the books. In a game whose entire claim is that the
   arithmetic is real, the kitchen was a perpetual motion machine. A test now
   asserts that no food recipe returns more energy than it consumes.
3. **The main crop could not be kept.** Potatoes keep 120 days and come in in
   August. Nothing preserved them — no recipe took them — and the cook's
   "about to spoil, drop everything" rule only looked at food with a shelf life
   under 60 days, which excluded them by construction. Two tonnes a year went
   to compost while the cook stood next to it deciding nothing was urgent.
4. **Treeless ground had no fuel at all.** Fuel work was gated on the survey's
   standing timber, so on the tallgrass prairie — the best farmland on Earth,
   and genuinely treeless — the job returned zero urgency forever. The colony
   burned the woodpile it arrived with and then froze. Prairie settlers twisted
   hay; so does this one, at 3.5 person-minutes per kilogram against 0.055 for
   felling timber. That ratio is the point: it is why a woodland site is worth
   something.
5. **Fuel was judged against today's weather.** "Days of fuel left" divides by
   the current heating bill, which in July is nearly zero, so a colony in
   midsummer concludes it has centuries of firewood and does no fuel work at
   all — then freezes in January. Cutting fuel is a *summer* job. The target is
   now the heating required to reach the far side of the next cold season,
   computed from the site's own climate.
6. **Drought damage was permanent.** A field's water deficit was never reset,
   and only ever accumulated while something was in the ground, so it was
   inherited by every subsequent crop. Every field on every map decayed
   monotonically towards the yield floor: a colony's tenth harvest was a
   fraction of its first however much it rained, and nothing could bring the
   land back. A season's water balance belongs to that season's crop.
7. **Jobs had no saturation.** A large perishable harvest put every single
   person in the kitchen permanently and nothing else got done. Work types that
   only absorb a couple of hands now say so.

What is left is emergent rather than broken, and worth keeping. A colony that
sows its whole field with a fast autumn catch crop harvests twenty tonnes of
turnips and watches eighteen of them rot in a fortnight, because three people
cannot dry twenty tonnes of roots in two weeks. That is a real lesson about
planting what you can process, and the log says so plainly.

## Combat: geometry over percentages

Hit probability is the shooter's total dispersion — mechanical MOA plus a human
wobble that shrinks with skill — projected to a group size at range, tested
against the target's silhouette. It falls out of that model, rather than being
authored, that:

- getting closer helps enormously, and skill helps more at range than up close
- cover and going prone are worth more than any weapon upgrade
- a pistol at 150 m is bad rather than impossible

There are no hit points, because hit points make every casualty a death. Rounds
carry energy, meet armour, and injure body parts; people stop from shock, blood
loss and broken bones. Most casualties are wounded, which turns a firefight into
a labour and medical problem for the next season rather than a headcount.

The single largest correction here: the first working version resolved a
ten-person engagement in **three seconds**, because nobody took cover or went
prone. Adding posture and manoeuvre took it to 42–96 seconds and, more
interestingly, made terrain matter — the same engagement is a bloodbath in
woodland and a suppression contest on open prairie.

## The GUI, and why it is a browser

A desktop toolkit was the obvious choice and the wrong one. Tk is in the
standard library but is missing from a surprising number of Linux installs,
cannot be tested on a headless machine, and looks like 1995. A browser is on
every machine that exists, draws to a canvas at sixty frames a second, and
costs nothing, because ``http.server`` is stdlib too.

So ``peg gui`` starts a localhost server and opens a page. It is not a web
application; it is a rendering surface that happens to speak HTTP.

Three decisions worth recording:

**The axis boundary is one module.** PEG's simulation stores local tiles as
``(x, y)`` with elevation separate, which is fine internally and confusing the
moment you add a vertical view. ``peg/ui/viewdata.py`` is the single place the
two conventions meet: below it, tiles are ``(x, y)``; above it, everything is
world X (east), Z (north) and Y (up). Nothing else in the codebase has to
think about it, and a test asserts that +X really goes east and +Z really goes
north, because transposing a map looks exactly like a worldgen bug.

**Tiles go over as packed bytes, not JSON.** A 192 m site is 36,864 tiles. As
JSON objects that is several megabytes of punctuation; as five packed bytes per
tile (terrain, object class, object height in decimetres, elevation in
centimetres) it is 184 KiB, base64 included.

**Object heights are real metres.** The cross-sections draw them to scale,
which is the entire reason they exist. It also makes the payload
self-validating: a slice across a 620 stems/hectare conifer forest shows about
thirteen trees per 160 m, which is what that density means.

### Why the first client was unusable

It was, in the player's words, the laggiest and clunkiest thing they had seen,
and they could not tell what they were playing or where they were. Both halves
of that were fair, and they had different causes.

The lag was two mistakes. Every pan and every zoom fetched a fresh viewport
over HTTP, so the camera moved at the speed of a round trip; and every frame
issued about three thousand individual `arc()` calls, one per object. The fix
was to stop treating the network as part of the render loop — the whole site
arrives once as packed bytes, 184 KiB for 160 m — and to stop treating the
canvas as a scene graph. Terrain, hillshade and object tint are baked once into
an offscreen `ImageData` at one pixel per tile and blitted with a single scaled
`drawImage`; objects are drawn above a zoom threshold as one batched path per
class. Frame cost went from 9.62 ms to 0.5–1.1 ms, and panning and zooming now
make zero network requests.

Not knowing where you were was the more interesting complaint, because nothing
was broken — the information genuinely was not there. A grid of coloured
squares is not a place. What fixed it was naming things: the nearest real
landmark and its distance, a world map with your dot on it, an ordered list of
what the colony needs next in plain language, a key that finds your people, and
colonists who walk to their work so the map shows the settlement doing
something. None of that changed a single number in the simulation. All of it
changed whether the simulation was legible, which is the only thing that
matters about an interface.

## Things deliberately left out

- **Real-time play.** The interesting decisions are ones you want to think
  about. A clock that runs while you think converts them into reflexes. The GUI
  advances in explicit steps for the same reason.
- **A true 3D view.** The two cross-sections give you the vertical information
  that matters -- slope, tree height, soil depth, line of sight -- without
  needing a camera, a mesh pipeline or WebGL. A voxel renderer would be a much
  larger project and would not answer a question the sections do not.
- **Tech trees with fictional tiers.** Research is five branches of real
  capability (agriculture, metallurgy, medicine, firearms, logistics) that
  modify real coefficients.
- **Animals, in any depth.** Hunting is folded into foraging.
- **Individual pawn pathing for colony work.** Work is allocated by urgency with
  travel time approximated from distance. Colonists do walk to their work — they
  pick the nearest site for the job, steer towards it and stay a while — but
  that is presentational, and deliberately so: the economy is costed in
  person-minutes either way. A* exists and is used for combat, where the
  metre-by-metre positions actually decide the outcome.
- **Livestock.** The largest genuine gap, and where I would go next. It is also
  now a slightly embarrassing one: the prairie fuel problem has a well-known
  historical answer this model cannot express, which is that you burn dung.
  Animals would change the food model, the fuel model and the soil fertility
  model at once, which is why they are a project rather than an afternoon.
