"""Validate the climate model against real-world normals.

These are regression guards with headroom, not aspirations. The model is a
physical simulation with no DEM behind it, so it will never reproduce station
data exactly; what it must do is keep getting the *structure* of Earth's
climate right -- deserts in the subtropics and behind ranges, monsoons where
monsoons are, maritime climates where currents put them.

Where it is weakest, and why: intermontane basins. A 55 km raster cell in the
Basin and Range averages ridge and valley together, so Las Vegas comes out
800 m too high and 12 C too cold. That is a resolution limit, not a modelling
error, and it is documented in docs/DATA.md.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cities import CITIES                       # noqa: E402
from peg.world import climate                   # noqa: E402


class TestKoppen(unittest.TestCase):
    """The classifier itself, on hand-built inputs -- independent of worldgen."""

    def test_tropical_rainforest(self):
        t = tuple([27.0] * 12)
        p = tuple([200.0] * 12)
        self.assertEqual(climate.koppen(t, p, 1.0), "Af")

    def test_hot_desert(self):
        t = tuple([22.0 + 8 * (m in (5, 6, 7)) for m in range(12)])
        p = tuple([1.0] * 12)
        self.assertEqual(climate.koppen(t, p, 25.0), "BWh")

    def test_ice_cap(self):
        t = tuple([-30.0] * 12)
        p = tuple([2.0] * 12)
        self.assertEqual(climate.koppen(t, p, -80.0), "EF")

    def test_tundra_is_not_ice_cap(self):
        t = (-25, -25, -20, -10, -2, 4, 7, 6, 1, -8, -18, -23)
        p = tuple([10.0] * 12)
        self.assertEqual(climate.koppen(t, p, 71.0), "ET")

    def test_mediterranean_needs_dry_summer(self):
        # Northern hemisphere: wet winter, bone-dry summer.
        t = (9, 10, 12, 15, 19, 23, 27, 27, 24, 19, 13, 10)
        p = (80, 70, 55, 30, 12, 3, 1, 2, 15, 55, 80, 90)
        self.assertEqual(climate.koppen(t, p, 38.0), "Csa")

    def test_southern_hemisphere_seasons_are_flipped(self):
        """The same rainfall pattern is Mediterranean in the north and
        summer-wet in the south, because the months mean opposite things."""
        t_n = (9, 10, 12, 15, 19, 23, 27, 27, 24, 19, 13, 10)
        p_dry_jul = (80, 70, 55, 30, 12, 3, 1, 2, 15, 55, 80, 90)
        north = climate.koppen(t_n, p_dry_jul, 38.0)
        # Southern hemisphere: warm in January, so flip the temperature curve.
        t_s = t_n[6:] + t_n[:6]
        south = climate.koppen(t_s, p_dry_jul, -38.0)
        self.assertEqual(north[1], "s")
        self.assertNotEqual(south[1], "s")


class TestAgainstRealCities(unittest.TestCase):
    """End-to-end: worldgen -> raster -> climate -> classification."""

    @classmethod
    def setUpClass(cls):
        cls.results = []
        for name, lon, lat, elev, code, t_real, p_real in CITIES:
            c = climate.at(lon, lat)
            cls.results.append((name, code, c, t_real, p_real))

    def test_group_letter_accuracy(self):
        """The first Koppen letter -- tropical, arid, temperate, continental,
        polar. This is what decides biome, crops and survivability, so it is
        the number that matters."""
        hits = sum(1 for _, code, c, _, _ in self.results if c.koppen[0] == code[0])
        pct = 100.0 * hits / len(self.results)
        self.assertGreaterEqual(
            pct, 75.0,
            f"group-letter accuracy fell to {pct:.0f}% "
            f"({hits}/{len(self.results)}); was 83% when written",
        )

    def test_full_code_accuracy(self):
        hits = sum(1 for _, code, c, _, _ in self.results if c.koppen == code)
        pct = 100.0 * hits / len(self.results)
        self.assertGreaterEqual(
            pct, 25.0,
            f"full-code accuracy fell to {pct:.0f}%; was 31% when written",
        )

    def test_temperature_error(self):
        errs = sorted(abs(c.mean_temp - t) for _, _, c, t, _ in self.results)
        median = errs[len(errs) // 2]
        self.assertLess(median, 4.0, f"median temperature error {median:.1f} C")

    def test_precipitation_error(self):
        errs = sorted(abs(c.annual_precip - p) for _, _, c, _, p in self.results)
        median = errs[len(errs) // 2]
        self.assertLess(median, 350.0, f"median precipitation error {median:.0f} mm")

    def test_no_city_is_absurd(self):
        """Catch sign errors and unit slips, which produce wrong answers that
        the aggregate statistics can absorb."""
        for name, _, c, _, _ in self.results:
            self.assertGreater(c.mean_temp, -60.0, name)
            self.assertLess(c.mean_temp, 45.0, name)
            self.assertGreaterEqual(c.annual_precip, 0.0, name)
            self.assertLess(c.annual_precip, 12000.0, name)

    def test_seasons_are_opposite_across_the_equator(self):
        """Sydney's warmest month must be in the southern summer."""
        syd = next(c for n, _, c, _, _ in self.results if n == "Sydney")
        tok = next(c for n, _, c, _, _ in self.results if n == "Tokyo")
        self.assertGreater(syd.temp_c[0], syd.temp_c[6], "Sydney warmest in January")
        self.assertGreater(tok.temp_c[6], tok.temp_c[0], "Tokyo warmest in July")

    def test_coastal_deserts_exist(self):
        """Lima and Walvis Bay are deserts *because* of cold currents, at
        latitudes that are otherwise wet. If the current model breaks, these
        turn green and the test catches it."""
        for name in ("Lima", "Walvis Bay"):
            c = next(x for n, _, x, _, _ in self.results if n == name)
            self.assertEqual(c.koppen[0], "B", f"{name} should be arid")
            self.assertLess(c.annual_precip, 250.0, name)

    def test_monsoon_asia_is_wet(self):
        for name in ("Mumbai", "Kolkata", "Bangkok"):
            c = next(x for n, _, x, _, _ in self.results if n == name)
            self.assertGreater(c.annual_precip, 500.0,
                               f"{name} lost its monsoon")
            # Wet season must be the northern summer.
            summer = sum(c.precip_mm[5:9])
            winter = sum(c.precip_mm[11:] + c.precip_mm[:3])
            self.assertGreater(summer, winter * 1.5, f"{name} monsoon out of phase")


class TestDerivedQuantities(unittest.TestCase):
    def test_growing_season_shrinks_polewards(self):
        tropic = climate.at(-60.0, -3.0)     # Amazon
        temperate = climate.at(2.35, 48.86)  # Paris
        arctic = climate.at(-156.79, 71.29)  # Barrow
        self.assertGreater(tropic.growing_days, temperate.growing_days)
        self.assertGreater(temperate.growing_days, arctic.growing_days)

    def test_growing_degree_days_order(self):
        hot = climate.at(46.72, 24.69)       # Riyadh
        cold = climate.at(129.73, 62.03)     # Yakutsk
        self.assertGreater(hot.growing_degree_days(), cold.growing_degree_days())

    def test_aridity_index_flags_deserts(self):
        sahara = climate.at(10.0, 23.0)
        congo = climate.at(15.31, -4.32)
        self.assertLess(sahara.aridity_index, 0.25)
        self.assertGreater(congo.aridity_index, 0.6)

    def test_day_interpolation_is_continuous(self):
        c = climate.at(2.35, 48.86)
        prev = c.temp_on_day(0)
        for d in range(1, 365):
            t = c.temp_on_day(d)
            self.assertLess(abs(t - prev), 2.0, f"temperature jump at day {d}")
            prev = t


if __name__ == "__main__":
    unittest.main(verbosity=2)
