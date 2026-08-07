"""Geodesy, determinism and worldgen."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from peg import geo, rng                          # noqa: E402
from peg.local import terrain                     # noqa: E402
from peg.world import biome, raster, site         # noqa: E402


class TestGeodesy(unittest.TestCase):
    def test_degree_lengths(self):
        # A degree of latitude is ~110.6 km at the equator, ~111.7 at the pole.
        self.assertAlmostEqual(geo.meters_per_degree_lat(0), 110574, delta=60)
        self.assertAlmostEqual(geo.meters_per_degree_lat(90), 111694, delta=60)
        # A degree of longitude shrinks to nothing at the pole.
        self.assertAlmostEqual(geo.meters_per_degree_lon(0), 111320, delta=60)
        self.assertLess(geo.meters_per_degree_lon(89.9), 250)

    def test_known_distances(self):
        # London to New York, great circle, ~5570 km.
        d = geo.haversine_m(51.5, -0.13, 40.71, -74.0) / 1000.0
        self.assertAlmostEqual(d, 5570, delta=40)
        # Quito to Singapore, both near the equator, ~19_000 km.
        d = geo.haversine_m(-0.18, -78.47, 1.35, 103.82) / 1000.0
        self.assertAlmostEqual(d, 19729, delta=200)

    def test_offset_round_trip(self):
        for lat, lon in ((51.5, -0.13), (-33.9, 151.2), (64.1, -21.9)):
            for bearing in (0, 45, 137, 270):
                la2, lo2 = geo.offset_deg(lat, lon, bearing, 40000.0)
                back = geo.haversine_m(lat, lon, la2, lo2)
                self.assertAlmostEqual(back, 40000.0, delta=60)

    def test_local_frame_is_metric(self):
        """A tile really is a square metre, not a projected approximation."""
        for lat in (0.0, 45.0, 60.0, -34.0):
            f = geo.LocalFrame(lat, 10.0)
            la2, lo2 = f.to_geo(100.0, 100.0)
            d = geo.haversine_m(lat, 10.0, la2, lo2)
            self.assertAlmostEqual(d, math.hypot(100, 100), delta=0.5)
            # And the inverse recovers the offsets.
            x, y = f.from_geo(la2, lo2)
            self.assertAlmostEqual(x, 100.0, delta=0.05)
            self.assertAlmostEqual(y, 100.0, delta=0.05)

    def test_solar_geometry(self):
        # Polar day and polar night at the solstices.
        self.assertAlmostEqual(geo.day_length_h(80.0, 172), 24.0, delta=0.01)
        self.assertAlmostEqual(geo.day_length_h(80.0, 355), 0.0, delta=0.01)
        # Twelve hours at the equator, all year.
        for day in (1, 90, 180, 270):
            self.assertAlmostEqual(geo.day_length_h(0.0, day), 12.0, delta=0.2)
        # Insolation peaks in the summer hemisphere.
        self.assertGreater(geo.daily_insolation(45, 172),
                           geo.daily_insolation(45, 355))

    def test_earth_area_constant(self):
        """The headline number: how many 1 m tiles Earth would have."""
        self.assertAlmostEqual(geo.EARTH_AREA_M2, 5.1e14, delta=1e13)


class TestDeterminism(unittest.TestCase):
    """The whole world must be reproducible from a seed, in any order."""

    def test_noise_is_positional(self):
        a = rng.fbm2(1234, 3.5, -7.25, 4)
        b = rng.fbm2(1234, 3.5, -7.25, 4)
        self.assertEqual(a, b)
        self.assertNotEqual(a, rng.fbm2(1235, 3.5, -7.25, 4))

    def test_survey_is_order_independent(self):
        """Surveying B then A must give the same A as surveying A first."""
        a1 = site.survey(-93.5, 41.9, seed=7)
        _ = site.survey(15.3, -4.3, seed=7)
        _ = site.survey(139.7, 35.7, seed=7)
        a2 = site.survey(-93.5, 41.9, seed=7)
        self.assertEqual(a1.biome.key, a2.biome.key)
        self.assertEqual(a1.soil.order.key, a2.soil.order.key)
        self.assertAlmostEqual(a1.soil.fertility, a2.soil.fertility, places=9)
        self.assertAlmostEqual(a1.clim.annual_precip, a2.clim.annual_precip,
                               places=6)
        self.assertEqual(a1.minerals, a2.minerals)

    def test_local_map_chunks_are_independent(self):
        """Generating chunks in a different order must not change tiles."""
        s = site.survey(-93.5, 41.9, seed=7)
        eager = terrain.generate(s, 96, seed=7, eager=True)
        lazy = terrain.generate(s, 96, seed=7)
        # Touch chunks in a deliberately awkward order.
        for (x, y) in ((90, 90), (5, 5), (60, 10), (10, 60), (33, 77)):
            lazy.ensure(x, y)
        lazy.ensure_all()
        self.assertEqual(bytes(eager.terrain), bytes(lazy.terrain))
        self.assertEqual(list(eager.elev_cm), list(lazy.elev_cm))
        self.assertEqual(sorted(eager.objects), sorted(lazy.objects))

    def test_rng_stream_reproducible(self):
        a = [rng.Rng(99).random() for _ in range(5)]
        b = [rng.Rng(99).random() for _ in range(5)]
        self.assertEqual(a, b)
        r1, r2 = rng.Rng(5), rng.Rng(5)
        self.assertEqual([r1.next_u64() for _ in range(20)],
                         [r2.next_u64() for _ in range(20)])


class TestRaster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = raster.get()

    def test_land_and_sea(self):
        land = [(-93.5, 41.9), (2.35, 48.86), (10.0, 23.0), (135.0, -25.0)]
        sea = [(-30.0, 20.0), (-140.0, 0.0), (80.0, -40.0)]
        for lon, lat in land:
            self.assertTrue(self.r.is_land(lon, lat), f"{lon},{lat}")
        for lon, lat in sea:
            self.assertFalse(self.r.is_land(lon, lat), f"{lon},{lat}")

    def test_oceans_are_deep_and_land_is_not(self):
        self.assertLess(self.r.elevation(-30.0, 20.0), -2000)
        self.assertGreater(self.r.elevation(86.9, 28.0), 4000)   # Himalaya

    def test_coast_distance_sign(self):
        self.assertGreater(self.r.coast_distance_km(10.0, 23.0), 500)   # Sahara
        self.assertLess(self.r.coast_distance_km(-30.0, 20.0), 0)       # mid-ocean

    def test_mountains_are_where_mountains_are(self):
        """Real ranges, real heights, from the Natural Earth anchors."""
        for lon, lat, floor in ((86.9, 28.0, 4000),     # Himalaya
                                (-70.0, -32.0, 2500),   # Andes
                                (7.9, 46.0, 1500)):     # Alps
            self.assertGreater(self.r.elevation(lon, lat), floor,
                               f"{lon},{lat}")
        for lon, lat in ((-93.5, 41.9), (2.35, 48.86)):  # Iowa, Paris
            self.assertLess(self.r.elevation(lon, lat), 500)


class TestBiomeAndSoil(unittest.TestCase):
    def test_famous_places_read_correctly(self):
        cases = {
            (10.0, 23.0): "desert",           # Sahara
            (-93.5, 41.9): "grassland",       # Iowa prairie
            (15.3, -4.3): None,               # Congo: forest of some kind
        }
        for (lon, lat), expect in cases.items():
            s = site.survey(lon, lat)
            if expect:
                self.assertEqual(s.biome.key, expect, f"{lon},{lat}")

    def test_prairie_soil_beats_rainforest_soil(self):
        """The point of the soil model: lush is not the same as fertile.

        The Amazon is the most productive ecosystem on Earth and among the
        worst farmland, because a century of rain has leached the nutrients
        into the river. A colony that clears it gets two harvests.

        Compared at the level of soil orders, because a *floodplain* anywhere
        is alluvial and fertile -- which is also true, and is why the one
        strip of the Amazon that is good farmland is the river bank.
        """
        mollisol = biome.SOIL_ORDERS["mollisol"]
        oxisol = biome.SOIL_ORDERS["oxisol"]
        self.assertGreater(mollisol.fertility, oxisol.fertility * 3)

        prairie = site.survey(-93.5, 41.9)
        self.assertEqual(prairie.soil.order.key, "mollisol")
        rainforest = site.survey(-64.5, -5.5)     # interior, off the river
        self.assertGreater(prairie.arable_fraction,
                           rainforest.arable_fraction)

    def test_soil_orders_are_plausible(self):
        self.assertEqual(site.survey(-93.5, 41.9).soil.order.key, "mollisol")

    def test_desert_has_no_timber(self):
        self.assertLess(site.survey(10.0, 23.0).timber_m3_ha, 5.0)


class TestSiteScoring(unittest.TestCase):
    def test_ocean_scores_zero(self):
        s = site.survey(-30.0, 20.0)
        self.assertEqual(site.score(s)[0], 0.0)

    def test_farmland_beats_desert_for_an_agrarian(self):
        iowa = site.score(site.survey(-93.5, 41.9), "agrarian")[0]
        sahara = site.score(site.survey(10.0, 23.0), "agrarian")[0]
        self.assertGreater(iowa, sahara)

    def test_doctrines_disagree(self):
        """The competition depends on rivals wanting different ground."""
        s = site.survey(-4.5, 57.0)      # rugged, wooded, poor farmland
        agr = site.score(s, "agrarian")[0]
        mil = site.score(s, "militarist")[0]
        self.assertNotAlmostEqual(agr, mil, places=1)

    def test_unfeedable_ground_is_discounted(self):
        """Food is a precondition, not one weighted factor among many."""
        s = site.survey(10.0, 23.0)      # deep Sahara
        _, parts = site.score(s, "industrial")
        self.assertLess(parts["viability"], 0.75)

    def test_water_gates_farmland(self):
        """Desert soil plus a 365-day growing season is still not farmland."""
        sahara = site.survey(10.0, 23.0)
        self.assertLess(sahara.arable_fraction, 0.15)


class TestLocalTerrain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = site.survey(-4.5, 57.0)
        cls.m = terrain.generate(cls.s, 64, seed=3, eager=True)

    def test_every_tile_has_a_real_position(self):
        """A tile is a place on Earth, not a cell in a level."""
        lat, lon = self.m.geo_of(0, 0)
        lat2, lon2 = self.m.geo_of(63, 0)
        d = geo.haversine_m(lat, lon, lat2, lon2)
        self.assertAlmostEqual(d, 63.0, delta=0.5)

    def test_terrain_is_populated(self):
        kinds = {terrain.TERRAIN_BY_ID[v].key for v in self.m.terrain}
        self.assertGreater(len(kinds), 1, "map is a single texture")

    def test_forest_has_trees(self):
        trees = sum(1 for o in self.m.objects.values()
                    if isinstance(o, terrain.Plant) and o.species.height_m > 4)
        self.assertGreater(trees, 5)

    def test_line_of_sight_is_symmetric_in_the_open(self):
        for _ in range(20):
            a = (5, 5)
            b = (40, 30)
            self.assertEqual(self.m.line_of_sight(*a, *b),
                             self.m.line_of_sight(*b, *a))

    def test_walk_cost_is_finite_on_open_ground(self):
        passable = sum(1 for y in range(64) for x in range(64)
                       if self.m.passable(x, y))
        self.assertGreater(passable, 64 * 64 * 0.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
