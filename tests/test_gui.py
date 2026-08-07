"""The graphical client's data layer and HTTP endpoints.

The browser itself is not tested here -- what is tested is that the payloads it
draws from are correct, that the axis convention is what it claims to be, and
that every endpoint answers.
"""

import base64
import json
import os
import struct
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from peg.game import Game                              # noqa: E402
from peg.local import terrain                          # noqa: E402
from peg.ui import server, viewdata                    # noqa: E402
from peg.world import site                             # noqa: E402


def _map(size=48):
    s = site.survey(-4.5, 57.0, seed=5)                # wooded, sloping
    return terrain.generate(s, size, seed=5, eager=True)


class TestLegend(unittest.TestCase):
    def test_every_terrain_has_a_colour(self):
        lg = viewdata.legend()
        self.assertEqual(len(lg["terrain_order"]), len(terrain.TERRAIN_BY_ID))
        for key in lg["terrain_order"]:
            rgb = lg["terrain"][key]["rgb"]
            self.assertEqual(len(rgb), 3)
            self.assertTrue(all(0 <= c <= 255 for c in rgb), key)

    def test_terrain_order_matches_tile_ids(self):
        """The client indexes the colour table by the raw tile byte, so the
        order has to be the id order or every map is miscoloured."""
        lg = viewdata.legend()
        for i, key in enumerate(lg["terrain_order"]):
            self.assertEqual(terrain.TERRAIN_BY_ID[i].key, key)

    def test_three_planes_offered(self):
        ids = [p["id"] for p in viewdata.legend()["planes"]]
        self.assertEqual(ids, ["xz", "xy", "zy"])


class TestTilePayload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _map()

    def _unpack(self, payload):
        terr = base64.b64decode(payload["terrain"])
        obj = base64.b64decode(payload["obj"])
        objh = base64.b64decode(payload["objh"])
        ys = base64.b64decode(payload["y"])
        return terr, obj, objh, ys

    def test_shapes_and_sizes(self):
        v = viewdata.Viewport(4, 6, 16, 12)
        p = viewdata.tiles(self.m, v)
        terr, obj, objh, ys = self._unpack(p)
        n = p["w"] * p["h"]
        self.assertEqual(n, 16 * 12)
        self.assertEqual(len(terr), n)
        self.assertEqual(len(obj), n)
        self.assertEqual(len(objh), n)
        self.assertEqual(len(ys), n * 2)

    def test_values_match_the_map(self):
        """Row-major in Z then X: the client relies on exactly this order."""
        v = viewdata.Viewport(3, 5, 8, 7)
        p = viewdata.tiles(self.m, v)
        terr, _obj, _objh, ys = self._unpack(p)
        for dz in range(p["h"]):
            for dx in range(p["w"]):
                i = dz * p["w"] + dx
                idx = self.m.idx(p["x0"] + dx, p["z0"] + dz)
                self.assertEqual(terr[i], self.m.terrain[idx])
                self.assertEqual(struct.unpack_from("<h", ys, i * 2)[0],
                                 self.m.elev_cm[idx])

    def test_viewport_is_clamped_to_the_site(self):
        p = viewdata.tiles(self.m, viewdata.Viewport(-40, -40, 999, 999))
        self.assertGreaterEqual(p["x0"], 0)
        self.assertGreaterEqual(p["z0"], 0)
        self.assertLessEqual(p["x0"] + p["w"], self.m.size)
        self.assertLessEqual(p["z0"] + p["h"], self.m.size)

    def test_object_heights_are_real_metres(self):
        """The cross-section draws these to scale, so they must be metres and
        not an arbitrary sprite size."""
        p = viewdata.tiles(self.m, viewdata.Viewport(0, 0, self.m.size, self.m.size))
        _terr, obj, objh, _ys = self._unpack(p)
        trees = [(objh[i] / 10.0) for i in range(len(obj))
                 if obj[i] in (viewdata.OBJ_TREE, viewdata.OBJ_CONIFER)]
        self.assertTrue(trees, "a conifer forest with no trees in it")
        self.assertGreater(max(trees), 8.0, "no tree taller than 8 m")
        self.assertLess(max(trees), 45.0, "a tree taller than any on Earth")

    def test_conifers_are_distinguished(self):
        p = viewdata.tiles(self.m, viewdata.Viewport(0, 0, self.m.size, self.m.size))
        _t, obj, _oh, _y = self._unpack(p)
        self.assertIn(viewdata.OBJ_CONIFER, set(obj),
                      "no conifers in a temperate conifer forest")


class TestAxisConvention(unittest.TestCase):
    """X is east, Z is north, Y is up. Getting this wrong transposes the map,
    which is the sort of bug that looks like worldgen noise."""

    def test_x_is_east_and_z_is_north(self):
        m = _map()
        lat0, lon0 = m.geo_of(10, 10)
        lat_x, lon_x = m.geo_of(20, 10)      # +X
        lat_z, lon_z = m.geo_of(10, 20)      # +Z
        self.assertGreater(lon_x, lon0, "+X must go east")
        self.assertAlmostEqual(lat_x, lat0, places=6)
        self.assertGreater(lat_z, lat0, "+Z must go north")
        self.assertAlmostEqual(lon_z, lon0, places=6)

    def test_tile_info_reports_y_as_elevation(self):
        m = _map()
        info = viewdata.tile_info(m, 12, 13)
        self.assertEqual(info["x"], 12)
        self.assertEqual(info["z"], 13)
        self.assertAlmostEqual(info["elevation_m"], m.elevation_at(12, 13),
                               places=2)

    def test_actors_are_reported_in_world_axes(self):
        g = Game.new(seed=99, lon=-4.5, lat=57.0, rivals=2, size=48)
        g.colony.pawns[0].x = 7
        g.colony.pawns[0].y = 19
        a = viewdata.actors(g.colony)[0]
        self.assertEqual((a["x"], a["z"]), (7, 19))


class TestTileInfo(unittest.TestCase):
    def test_reports_a_real_place_on_earth(self):
        """The claim the whole project rests on: one tile is one square metre
        of the actual planet, and can say where."""
        m = _map()
        info = viewdata.tile_info(m, 24, 24)
        self.assertAlmostEqual(info["lat"], 57.0, delta=0.01)
        self.assertAlmostEqual(info["lon"], -4.5, delta=0.01)
        self.assertIn("terrain", info)
        self.assertIn("soil", info)

    def test_out_of_bounds_is_empty_not_an_error(self):
        self.assertEqual(viewdata.tile_info(_map(), -5, 900), {})


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = Game.new(seed=4242, lon=-93.5, lat=41.9, rivals=2, size=48)
        cls.httpd, cls.url = server.start_background(cls.game)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path):
        with urllib.request.urlopen(self.url.rstrip("/") + path, timeout=20) as r:
            return r.status, json.loads(r.read())

    def test_page_is_served(self):
        with urllib.request.urlopen(self.url, timeout=20) as r:
            body = r.read().decode("utf-8")
        self.assertEqual(r.status, 200)
        self.assertIn("<canvas", body)
        self.assertIn("__pegReady", body)

    def test_endpoints_answer(self):
        for path in ("/api/health", "/api/legend", "/api/status",
                     "/api/view?x0=0&z0=0&w=8&h=8", "/api/tile?x=5&z=5"):
            code, payload = self._get(path)
            self.assertEqual(code, 200, path)
            self.assertNotIn("error", payload, path)

    def test_unknown_route_is_404_not_a_crash(self):
        try:
            self._get("/api/nope")
            self.fail("expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_status_has_what_the_panels_need(self):
        _code, d = self._get("/api/status")
        st = d["status"]
        for key in ("site", "clock", "weather", "colony", "standings", "intel"):
            self.assertIn(key, st)
        self.assertGreater(st["colony"]["population"], 0)
        self.assertTrue(st["standings"])
        self.assertEqual(len(d["actors"]), st["colony"]["population"])

    def test_advance_moves_the_clock(self):
        _c, before = self._get("/api/status")
        req = urllib.request.Request(
            self.url.rstrip("/") + "/api/advance",
            data=json.dumps({"minutes": 180}).encode(),
            headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.assertEqual(r.status, 200)
        _c, after = self._get("/api/status")
        b, a = before["status"]["clock"], after["status"]["clock"]
        self.assertNotEqual((b["day"], b["minute"]), (a["day"], a["minute"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
