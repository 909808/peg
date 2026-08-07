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

## Things deliberately left out

- **Real-time play.** The interesting decisions are ones you want to think
  about. A clock that runs while you think converts them into reflexes.
- **Tech trees with fictional tiers.** Research is five branches of real
  capability (agriculture, metallurgy, medicine, firearms, logistics) that
  modify real coefficients.
- **Animals, in any depth.** Hunting is folded into foraging. This is the
  largest genuine gap; livestock in particular would change the food model
  substantially and is where I would go next.
- **Individual pawn pathing for colony work.** Work is allocated by urgency with
  travel time approximated from distance. A* exists and is used for combat,
  where the metre-by-metre positions actually matter.
