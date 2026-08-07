# Where the Earth comes from

## Source

Everything factual about the planet comes from **Natural Earth**
(naturalearthdata.com), which is in the public domain. No attribution is
required by its licence; it is given here because it should be.

`tools/bake_earth.py` downloads these layers and bakes them into
`peg/world/earth.json.gz` (~120 KiB), which ships in the repository so the game
runs with no network:

| layer | what it gives PEG |
|---|---|
| `ne_50m_land` | 1,312 land rings — the coastline |
| `ne_110m_lakes` | 24 inland water bodies |
| `ne_110m_glaciated_areas` | 12 permanent ice sheets |
| `ne_50m_geography_regions_polys` | 225 mountain range / plateau / basin outlines, 35 deserts |
| `ne_10m_geography_regions_elevation_points` | 711 named summits with surveyed elevations |
| `ne_50m_rivers_lake_centerlines` | 895 river reaches with size ranks |

Coordinates are simplified with Douglas-Peucker, quantised to 0.01° (~1.1 km)
and delta-encoded before gzip. That is far finer than the 0.5° raster it feeds,
and detail below raster scale is synthesised procedurally anyway.

## What is real and what is synthesised

**Real**: where the land is. Where the lakes, glaciers and rivers are. The
outlines of the world's mountain ranges, plateaus, basins and deserts. The
positions and heights of 711 named summits — Everest is 8,848 m, in Nepal,
because the dataset says so.

**Synthesised**: everything between those anchors.

There is no elevation raster in PEG, because there is no free global DEM that
is both reachable from a sandbox and small enough to commit. So
`peg/world/raster.py` builds a height field from the anchors instead:

1. a gentle rise away from the coast, giving continental interiors a base
2. region polygons at their *ground* height — a mountain range polygon covers
   its valleys too, so the floor is well below the crest; a plateau, by
   definition, is not
3. cones around each named summit, stamped after the blur so they keep their
   true height and position
4. fBm and ridged detail, scaled by how rugged the cell already is
5. named depressions punched back down (Death Valley, Qattara, the Dead Sea)

### How wrong is it?

Median error **243 m** against 27 reference cities. Representative:

| place | model | real | error |
|---|---:|---:|---:|
| Denver | 1711 | 1609 | +102 |
| Chicago | 165 | 180 | −15 |
| Greenland interior | 2622 | 2700 | −78 |
| Bogotá | 2451 | 2640 | −189 |
| Lhasa | 4326 | 3650 | +676 |
| Mexico City | 3224 | 2240 | +984 |
| Las Vegas | 1415 | 610 | +805 |

The pattern in the failures is consistent and worth stating plainly: **PEG is
bad at intermontane basins**. A 55 km cell in the Basin and Range averages
ridges and valley floors together and reports something that is neither. Cities
sit in the valleys, so the model runs high — and since temperature follows the
lapse rate, it also runs cold. Las Vegas is 805 m too high and therefore 12 °C
too cold.

Two mitigations, both partial. `settlement_elevation()` biases towards the low
end of a cell's internal relief, on the grounds that people settle in valleys
rather than on ridges. And starting sites are checked for viability, so a
network is never founded somewhere the model has made uninhabitable by accident.

## Climate

Nothing about climate is stored. It is computed, per point, from:

- **temperature** — a zonal sea-level curve fitted to observed zonal means,
  moved by elevation (two-slope lapse rate: 6.5 K/km to a kilometre, 4.2 K/km
  above, because a plateau surface is a heated surface and not free air),
  amplitude set by continentality, offset by ocean currents
- **precipitation** — ITCZ, subtropical subsidence and the mid-latitude storm
  track; seasonal migration of the subtropical high; monsoons with directional
  flow; orographic lift and rain shadow computed against the direction the
  *rain-bearing* air actually arrives from; summer convection over heated
  continents; an equatorward warm-sea moisture pathway; ocean current anomalies

Then classified Köppen-Geiger on the modern 0 °C C/D boundary.

### Accuracy

Against 48 real cities (`tests/cities.py`), regression-guarded in
`tests/test_climate.py`:

- **85%** first-letter (group) accuracy — the letter that decides biome, crops
  and survivability
- **38%** exact three-letter code
- **2.6 °C** median mean-temperature error
- **242 mm** median annual-precipitation error

Known weak spots: intermontane cities inherit the elevation error above;
equatorial East Africa comes out far too wet (the real dryness there depends on
the Congo air boundary and the Turkana jet, neither of which a zonal model
reaches); the high Arctic runs a few degrees warm.

### The fifteen hand-placed currents

`peg/world/climate.py` lists them. They are hand-placed rather than derived
because ocean circulation is a historical accident of basin geometry, not
something that falls out of latitude. Omitting them costs you the Atacama, the
Namib, the Canary and California coastal deserts, and an ice-free Norway —
which between them are some of the most legible facts on the map.

## Reproducibility

Every random quantity in PEG is derived by hashing coordinates and a purpose
tag, never by advancing a shared stream. Two players with the same seed who
visit sites in a different order see the same Earth, and generating a site's
chunks in a different order produces byte-identical tiles. This is enforced by
`tests/test_world.py::TestDeterminism`.
