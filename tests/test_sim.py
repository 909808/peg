"""The simulation: metabolism, injuries, ballistics, colonies and Stewards."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from peg import rng                                      # noqa: E402
from peg.local import terrain                            # noqa: E402
from peg.meta import world as world_mod                  # noqa: E402
from peg.sim import combat, items                        # noqa: E402
from peg.sim.colony import Colony, daily_weather         # noqa: E402
from peg.sim.pawn import Pawn                            # noqa: E402
from peg.world import site                               # noqa: E402


def _colony(lon=-93.5, lat=41.9, people=6, size=64):
    s = site.survey(lon, lat, seed=5)
    m = terrain.generate(s, size, seed=5, eager=True)
    c = Colony(name="Test", faction="player", survey=s, map=m, seed=5)
    for i in range(people):
        p = Pawn.random(400 + i)
        p.x, p.y = size // 2, size // 2
        c.pawns.append(p)
    return c


class TestNutrition(unittest.TestCase):
    def test_bmr_is_realistic(self):
        # 70 kg, 175 cm, 30 y male: Mifflin-St Jeor gives ~1649 kcal/day.
        self.assertAlmostEqual(items.bmr_kcal(70, 175, 30, True), 1649, delta=5)

    def test_cold_costs_calories(self):
        warm = items.thermal_kcal(22.0, 1.0, 70)
        cold = items.thermal_kcal(-10.0, 1.0, 70)
        self.assertEqual(warm, 0.0)
        self.assertGreater(cold, 300)

    def test_spoilage_follows_q10(self):
        """Ten degrees colder should roughly halve the rate of spoiling."""
        warm = items.Store()
        warm.add("meat", 10.0)
        cold = items.Store()
        cold.add("meat", 10.0)
        warm.tick_spoilage(1440, 20.0)
        cold.tick_spoilage(1440, 10.0)
        self.assertGreater(warm.stacks["meat"].age_days,
                           cold.stacks["meat"].age_days * 1.7)

    def test_store_prefers_perishables(self):
        st = items.Store()
        st.add("grain", 100.0)          # keeps for years
        st.add("meal", 5.0, age_days=1.5)  # about to turn
        self.assertEqual(st.best_food(), "meal")

    def test_vitamin_c_source_is_found(self):
        st = items.Store()
        st.add("grain", 100.0)
        self.assertIsNone(st.best_vitamin_c())
        st.add("berries", 5.0)
        self.assertEqual(st.best_vitamin_c(), "berries")


class TestCrops(unittest.TestCase):
    def test_maize_will_not_ripen_in_the_arctic(self):
        arctic = site.survey(-156.79, 71.29).clim
        maize = items.CROPS["maize"]
        self.assertEqual(items.crop_suitability(maize, arctic), 0.0)

    def test_cool_maritime_climates_can_still_farm(self):
        """Rye starts growing at 2 C. Scoring every crop against a single
        base-10 figure said otherwise, and starved colonies that should have
        been fine."""
        scotland = site.survey(-4.5, 57.0).clim
        crops = items.best_crops(scotland)
        self.assertTrue(crops, "nothing at all will grow in Scotland")

    def test_crop_choice_prefers_storable_calories(self):
        """Ranking by tonnage grows cabbage, which is compost by December."""
        iowa = site.survey(-93.5, 41.9).clim
        best = items.best_crops(iowa)[0][0]
        self.assertIn(best.key, ("maize", "potato", "wheat", "barley", "rye"))


class TestPawn(unittest.TestCase):
    def test_capacities_come_from_body_parts(self):
        p = Pawn.random(1)
        self.assertAlmostEqual(p.capacity("mobility"), 1.0, delta=0.05)
        p.parts["leg_l"].hp = 0.0
        p.parts["leg_l"].destroyed = True
        self.assertLess(p.capacity("mobility"), 0.6)

    def test_bleeding_kills(self):
        p = Pawn.random(2)
        r = rng.Rng(1)
        p.hurt("torso", "gunshot", 25.0, r)
        self.assertGreater(p.bleeding_ml_min, 0)
        for _ in range(400):
            p.tick(1.0, 20.0, 0.0, False, r)
            if p.dead:
                break
        self.assertTrue(p.dead)
        self.assertEqual(p.cause_of_death, "blood loss")

    def test_tending_stops_bleeding(self):
        p = Pawn.random(3)
        r = rng.Rng(1)
        p.hurt("arm_l", "cut", 12.0, r)
        before = p.bleeding_ml_min
        p.tend(0.9, r)
        self.assertLess(p.bleeding_ml_min, before * 0.3)

    def test_eats_before_burning_fat(self):
        """The bug that starved colonies beside full granaries."""
        p = Pawn.random(4)
        st = items.Store()
        st.add("grain", 50.0)
        r = rng.Rng(1)
        fat0 = p.fat_kg
        for _ in range(60):
            if p.energy_debt_kcal > 600:
                p.eat(st, r)
            p.tick(60.0, 18.0, 0.6, False, r)
        self.assertGreater(p.fat_kg, fat0 * 0.85, "burned fat despite food")
        self.assertLess(st.amount("grain"), 50.0, "never ate anything")

    def test_starves_without_food(self):
        p = Pawn.random(5)
        r = rng.Rng(1)
        for _ in range(400):
            p.tick(1440.0, 18.0, 0.6, False, r)
            if p.dead:
                break
        self.assertTrue(p.dead)
        self.assertIn(p.cause_of_death, ("starvation", "dehydration"))

    def test_hypothermia(self):
        p = Pawn.random(6)
        p.clothing_clo = 0.3
        r = rng.Rng(1)
        for _ in range(200):
            p.tick(10.0, -35.0, 0.0, False, r)
            if p.dead:
                break
        self.assertTrue(p.dead)
        self.assertEqual(p.cause_of_death, "hypothermia")

    def test_scurvy_needs_months(self):
        p = Pawn.random(7)
        self.assertEqual(p.scurvy, 0.0)
        p.vit_c_debt_days = items.SCURVY_ONSET_DAYS + 20
        self.assertGreater(p.scurvy, 0.0)


class TestBallistics(unittest.TestCase):
    def test_muzzle_energies_are_real(self):
        # 9 mm ~500 J, 5.56 ~1700 J, .308 ~3300 J.
        self.assertAlmostEqual(combat.WEAPONS["pistol"].muzzle_energy_j,
                               518, delta=30)
        self.assertAlmostEqual(combat.WEAPONS["assault_rifle"].muzzle_energy_j,
                               1693, delta=60)
        self.assertAlmostEqual(combat.WEAPONS["hunting_rifle"].muzzle_energy_j,
                               3261, delta=90)

    def test_energy_falls_with_range(self):
        w = combat.WEAPONS["assault_rifle"]
        e = [combat.energy_at(w, d) for d in (0, 100, 300, 600)]
        self.assertTrue(all(e[i] > e[i + 1] for i in range(3)))
        self.assertLess(e[3] / e[0], 0.15)

    def test_light_bullets_shed_energy_faster(self):
        light = combat.WEAPONS["assault_rifle"]     # 4.0 g
        heavy = combat.WEAPONS["dmr"]               # 11.3 g
        r_light = combat.energy_at(light, 400) / combat.energy_at(light, 0)
        r_heavy = combat.energy_at(heavy, 400) / combat.energy_at(heavy, 0)
        self.assertLess(r_light, r_heavy)

    def test_hit_chance_falls_with_range_and_rises_with_skill(self):
        p = Pawn.random(1)
        w = combat.WEAPONS["assault_rifle"]
        p.skills["shooting"].level = 10
        near = combat.hit_chance(w, p, 25, "standing", 0.0, False)
        far = combat.hit_chance(w, p, 300, "standing", 0.0, False)
        self.assertGreater(near, far)
        p.skills["shooting"].level = 2
        poor = combat.hit_chance(w, p, 150, "standing", 0.0, False)
        p.skills["shooting"].level = 18
        good = combat.hit_chance(w, p, 150, "standing", 0.0, False)
        self.assertGreater(good, poor * 1.5)

    def test_cover_and_stance_matter(self):
        p = Pawn.random(1)
        p.skills["shooting"].level = 10
        w = combat.WEAPONS["assault_rifle"]
        exposed = combat.hit_chance(w, p, 100, "standing", 0.0, False)
        behind = combat.hit_chance(w, p, 100, "standing", 0.8, False)
        prone = combat.hit_chance(w, p, 100, "prone", 0.0, False)
        self.assertLess(behind, exposed * 0.45)
        self.assertLess(prone, exposed * 0.5)

    def test_range_degrades_rather_than_cutting_off(self):
        """A pistol past its practical range is bad, not impossible."""
        p = Pawn.random(1)
        p.skills["shooting"].level = 16
        w = combat.WEAPONS["pistol"]
        self.assertGreater(combat.hit_chance(w, p, w.max_range + 30,
                                             "standing", 0.0, False), 0.0)

    def test_armour_stops_pistols_and_not_rifles(self):
        plate = combat.ARMOURS["plate"]
        r = rng.Rng(1)
        pistol_hurt = rifle_hurt = 0
        for i in range(120):
            a = Pawn.random(1000 + i)
            b = Pawn.random(2000 + i)
            combat.resolve_hit(combat.WEAPONS["pistol"], a, plate, 20.0,
                               rng.Rng(i))
            combat.resolve_hit(combat.WEAPONS["hunting_rifle"], b, plate, 20.0,
                               rng.Rng(i))
            pistol_hurt += sum(inj.severity for inj in a.injuries)
            rifle_hurt += sum(inj.severity for inj in b.injuries)
        self.assertGreater(rifle_hurt, pistol_hurt * 1.5)


class TestEngagement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s = site.survey(-93.5, 41.9, seed=5)
        cls.m = terrain.generate(s, 96, seed=5, eager=True)

    def _run(self, n_def, n_att, seed):
        fs = []
        for i in range(n_def):
            p = Pawn.random(seed * 13 + i)
            p.x, p.y = 20 + i * 2, 20
            f = combat.make_fighter(p, "hunting_rifle", "padded", "def", 60)
            f.role = "hold"
            fs.append(f)
        for i in range(n_att):
            p = Pawn.random(seed * 29 + i)
            p.x, p.y = 70 + i * 2, 72
            f = combat.make_fighter(p, "assault_rifle", "none", "att", 90)
            f.role = "assault"
            fs.append(f)
        e = combat.Engagement(map=self.m, fighters=fs, seed=seed)
        return e, e.resolve(600.0), fs

    def test_engagements_conclude(self):
        for seed in range(6):
            e, winner, fs = self._run(3, 5, seed)
            self.assertLessEqual(e.elapsed_s, 600.0)

    def test_numbers_usually_win(self):
        wins = 0
        for seed in range(12):
            _, winner, _ = self._run(2, 7, seed)
            if winner == "att":
                wins += 1
        self.assertGreaterEqual(wins, 7, "overwhelming numbers lost too often")

    def test_people_actually_get_shot(self):
        touched = 0
        for seed in range(6):
            _, _, fs = self._run(3, 5, seed)
            touched += sum(1 for f in fs if f.pawn.injuries or not f.pawn.alive)
        self.assertGreater(touched, 6, "nobody was ever hit")

    def test_most_casualties_are_not_deaths(self):
        dead = hurt = 0
        for seed in range(8):
            _, _, fs = self._run(4, 6, seed)
            for f in fs:
                if not f.pawn.alive:
                    dead += 1
                elif f.pawn.injuries:
                    hurt += 1
        self.assertGreater(hurt, dead, "firefights should wound more than kill")

    def test_determinism(self):
        a = self._run(3, 5, 42)
        b = self._run(3, 5, 42)
        self.assertEqual(a[1], b[1])
        self.assertEqual(a[0].log, b[0].log)


class TestColony(unittest.TestCase):
    def test_a_year_on_good_land(self):
        """Iowa with stores, a field and a roof should survive a year."""
        c = _colony(people=6)
        for k, v in (("wood", 1400), ("grain", 500), ("vegetables", 150),
                     ("stone", 300), ("herbs", 20)):
            c.store.add(k, v)
        c.order_building("cabin", 20, 20)
        c.order_building("hearth", 24, 20)
        c.order_building("kitchen", 26, 20)
        c.add_field(34, 10, 45, 45)
        c.water_l = 300
        for _ in range(365 * 24):
            c.tick(60)
        self.assertGreater(c.population, 0, "colony wiped out on prime farmland")

    def test_water_can_be_fetched(self):
        c = _colony()
        c.store.add("wood", 500)
        c.water_l = 0.0
        p = c.alive[0]
        c._work_water(p, 60.0)
        self.assertGreater(c.water_l, 0.0)

    def test_felling_costs_time_proportional_to_wood(self):
        c = _colony(lon=-4.5, lat=57.0)
        p = c.alive[0]
        c._work_chop(p, 60.0, rng.Rng(1))
        got = c.store.amount("wood")
        # 60 person-minutes at 0.055 min/kg is about a tonne, not a forest.
        self.assertLess(got, 2500)

    def test_harvest_is_resumable(self):
        """Reaping part of a field must not destroy the rest."""
        c = _colony()
        f = c.add_field(10, 10, 40, 40)
        f.crop = items.CROPS["wheat"]
        f.gdd = f.crop.gdd_needed + 10
        f.tended = 1.0
        p = c.alive[0]
        c._work_farm(p, 20.0, rng.Rng(1))
        self.assertIsNotNone(f.crop, "field cleared after a partial harvest")
        self.assertGreater(f.harvested_m2, 0)

    def test_weather_is_correlated_not_random(self):
        s = site.survey(-93.5, 41.9)
        temps = [daily_weather(s.clim, d, 5).temp_c for d in range(120, 140)]
        jumps = [abs(temps[i + 1] - temps[i]) for i in range(len(temps) - 1)]
        self.assertLess(sum(jumps) / len(jumps), 6.0,
                        "weather jumps around like independent draws")

    def test_winter_burns_fuel(self):
        """A Moscow January costs real firewood.

        Measured against the heating call directly rather than net stores,
        because colonists also cut wood, and a test that watched the pile
        watched two effects at once.
        """
        c = _colony(lon=37.6, lat=55.75)     # Moscow
        c.store.add("wood", 3000)
        c.order_building("cabin", 20, 20)
        for b in c.buildings:
            b.work_left = 0
        c.day = 5
        c.weather = daily_weather(c.survey.clim, 5, c.seed)
        self.assertLess(c.weather.temp_c, 5.0, "January is not cold")
        before = c.store.amount("wood")
        for _ in range(30):
            c._burn_fuel(1440)
        burned = before - c.store.amount("wood")
        self.assertGreater(burned, 50.0,
                           "a Moscow January burned almost no fuel")

    def test_shelter_reduces_the_fuel_bill(self):
        bare = _colony(lon=37.6, lat=55.75)
        bare.store.add("wood", 3000)
        bare.day = 5
        bare.weather = daily_weather(bare.survey.clim, 5, bare.seed)

        housed = _colony(lon=37.6, lat=55.75)
        housed.store.add("wood", 3000)
        housed.store.add("stone", 200)
        housed.order_building("cabin", 20, 20)
        for b in housed.buildings:
            b.work_left = 0
        housed.day = 5
        housed.weather = housed.weather.__class__(**vars(bare.weather))

        # Measure from after construction: the cabin itself costs 900 kg of
        # wood, which is not fuel burned.
        bare_start = bare.store.amount("wood")
        housed_start = housed.store.amount("wood")
        for _ in range(30):
            bare._burn_fuel(1440)
            housed._burn_fuel(1440)
        self.assertLess(housed_start - housed.store.amount("wood"),
                        bare_start - bare.store.amount("wood"))


class TestStewards(unittest.TestCase):
    def test_world_builds_and_advances(self):
        w = world_mod.new_world(seed=4242, rivals=4)
        self.assertGreaterEqual(len(w.factions), 4)
        for _ in range(365 * 3):
            w.advance(1)
        self.assertTrue(any(f.alive_settlements for f in w.factions))

    def test_networks_start_somewhere_survivable(self):
        for seed in (1, 2, 3):
            w = world_mod.new_world(seed=seed, rivals=4)
            for f in w.factions:
                sv = f.settlements[0].survey
                self.assertTrue(world_mod.viable_start(sv),
                                f"{f.name} founded somewhere it cannot live")

    def test_stewards_farm_before_they_starve(self):
        """The failure that killed every faction: planners that armed and
        researched while their fields stayed empty."""
        w = world_mod.new_world(seed=4242, rivals=4)
        for _ in range(365):
            w.advance(1)
        farmed = [s.farm_m2 for f in w.factions for s in f.alive_settlements]
        self.assertTrue(farmed)
        self.assertGreater(max(farmed), 0.0, "not one field in a whole year")

    def test_stewards_record_their_reasoning(self):
        w = world_mod.new_world(seed=4242, rivals=4)
        for _ in range(60):
            w.advance(1)
        told = [f for f in w.factions if f.intel]
        self.assertTrue(told, "no Steward explained itself")
        self.assertIn("(", told[0].intel[-1])   # utilities are in the record

    def test_stewards_only_know_what_they_can_see(self):
        w = world_mod.new_world(seed=4242, rivals=4)
        st = w.stewards[0]
        st.observe(w)
        others = sum(len(f.alive_settlements) for f in w.factions
                     if f.key != st.f.key)
        self.assertLessEqual(len(st.known), others)

    def test_doctrines_differ_in_outcome(self):
        """Different priorities must produce different behaviour, or the
        competition is cosmetic."""
        w = world_mod.new_world(seed=777, rivals=5)
        for _ in range(365 * 6):
            w.advance(1)
        counts = {f.doctrine.key: len(f.alive_settlements)
                  for f in w.factions if not f.is_player}
        self.assertGreater(len(set(counts.values())), 1,
                           "every doctrine behaved identically")


if __name__ == "__main__":
    unittest.main(verbosity=2)
