"""The simulation: metabolism, injuries, ballistics, colonies and Stewards."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from peg import rng                                      # noqa: E402
from peg.local import terrain                            # noqa: E402
from peg.meta import world as world_mod                  # noqa: E402
from peg.sim import combat, disease, items                # noqa: E402
from peg.sim import livestock, social                    # noqa: E402
from peg.sim.colony import (Colony, daily_weather,       # noqa: E402
                            PERISHABLE_DAYS)
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

    def test_hunger_does_not_stop_the_colony_working(self):
        """A short-of-food colony must still be able to work its way out.

        Eating used to consume a pawn's entire tick and could be retried every
        tick, so once everyone's calorie debt sat above the threshold the whole
        settlement spent every waking minute chewing scraps and nobody farmed,
        cut fuel or cooked. It starved with its workforce fully employed.
        """
        c = _colony(people=1)
        c.store.add("vegetables", 50.0)
        p = c.alive[0]
        p.energy_debt_kcal = 2200.0
        c._run_pawn(p, 60, False, rng.Rng(1))
        self.assertLess(p.energy_debt_kcal, 2200.0, "did not eat when hungry")
        self.assertNotEqual(p.job, "eat", "a whole hour went on a single meal")

        # And over six days on a trickle of food -- which is what foraging
        # supplies -- the colony must spend most of its time on work.
        c = _colony(people=6)
        c.store.add("wood", 200)
        c.add_field(10, 10, 40, 40)
        for q in c.alive:
            q.energy_debt_kcal = 2200.0
        worked = awake = 0
        for _ in range(6 * 24):
            c.store.add("vegetables", 1.0)
            c.tick(60)
            for q in c.alive:
                if q.job != "rest":
                    awake += 1
                    worked += q.job != "eat"
        self.assertGreater(worked, awake * 0.6,
                           f"only {worked}/{awake} waking ticks did any work")

    def test_cooking_does_not_create_calories(self):
        """No food recipe may produce more energy than it consumes.

        Cooking earns a modest digestibility uplift; drying is a net loss.
        Anything above that is a perpetual motion machine with a kitchen.
        """
        for key, rec in items.RECIPES.items():
            out = sum(items.ITEMS[k].kcal_kg * n for k, n in rec.outputs)
            if out <= 0:
                continue
            inp = sum(items.ITEMS[k].kcal_kg * n for k, n in rec.inputs)
            self.assertGreater(inp, 0, f"{key} makes food out of nothing")
            self.assertLessEqual(out / inp, 1.25,
                                 f"{key} multiplies calories {out / inp:.2f}x")

    def test_the_root_harvest_can_be_kept(self):
        """Potatoes must have a route into storage.

        A temperate colony's main crop keeps for 120 days and is harvested in
        August. With no recipe taking it and a 60-day "perishable" threshold
        that excluded it, two tonnes came in every year and composted by
        Christmas while the cook decided nothing was urgent.
        """
        self.assertIn("potato", [k for r in items.RECIPES.values()
                                 for k, _ in r.inputs])
        self.assertLess(items.ITEMS["potato"].shelf_days, PERISHABLE_DAYS,
                        "the main crop does not count as worth preserving")

    def test_a_treeless_site_can_still_heat_itself(self):
        """Iowa is the best farmland on Earth and has no trees on it.

        Fuel work was gated on the survey's standing timber, so a prairie
        colony never gathered anything, burned its starting woodpile and
        froze. Real settlers twisted prairie hay; so does this one.
        """
        c = _colony(people=6)
        self.assertLess(c.survey.timber_m3_ha, 5.0, "test site grew trees")
        c.store.add("wood", 40)
        p = c.alive[0]
        c._work_chop(p, 600.0, rng.Rng(1))
        self.assertGreater(c.store.amount("hay"), 0.0,
                           "nothing to burn and nothing gathered")

    def test_fuel_is_cut_for_the_winter_ahead(self):
        """Stockpiling is a summer job, so the target cannot be today's need.

        Judged on days-of-fuel-left a colony in July divides by nearly zero,
        concludes it has centuries of firewood and does no fuel work at all.
        """
        c = _colony(people=6)
        c.store.add("wood", 500)
        c.day = 180                                    # midsummer
        self.assertGreater(c.winter_fuel_mj, 1000.0,
                           "no heating requirement seen from midsummer")

    def test_drought_damage_does_not_outlive_the_crop(self):
        """A field's water deficit belongs to one season, not to the land.

        It was never reset, and only ever grew while something was in the
        ground, so every field decayed monotonically towards the yield floor:
        a colony's tenth harvest was a fraction of its first however much it
        rained, and nothing could bring the land back.
        """
        c = _colony()
        f = c.add_field(10, 10, 40, 40)
        f.crop = items.CROPS["wheat"]
        f.gdd = f.crop.gdd_needed + 10
        f.tended = 1.0
        f.water_deficit_mm = 300.0
        p = c.alive[0]
        for _ in range(200):                           # reap it all, then sow
            c._work_farm(p, 600.0, rng.Rng(1))
            if f.crop is not None and f.crop.key != "wheat":
                break
        self.assertEqual(f.water_deficit_mm, 0.0,
                         "last season's drought carried into the new crop")

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


class TestLivestock(unittest.TestCase):
    """The animal economy, which exists to close three loops at once: fuel on
    treeless ground, soil fertility, and food that arrives daily."""

    def test_breed_figures_are_real(self):
        cow = livestock.BREEDS["cow"]
        # A dairy cow: half a tonne, 12 kg of dry matter a day, 15 L of milk.
        self.assertAlmostEqual(cow.feed_kg_day / cow.mass_kg, 0.022, delta=0.006)
        self.assertGreater(cow.milk_l_day, 10.0)
        hen = livestock.BREEDS["chicken"]
        self.assertAlmostEqual(hen.eggs_day * 365, 255, delta=50)

    def test_a_cow_eats_a_winter_of_hay(self):
        """The sum that decides how many animals a place can carry."""
        h = livestock.Herd()
        h.add("cow", 1)
        winter = 130
        self.assertGreater(h.feed_kg_day * winter, 1200.0,
                           "a cow through a winter should be over a tonne")

    def test_milk_and_eggs_arrive_daily(self):
        h = livestock.Herd()
        h.add("cow", 2)
        h.add("chicken", 6)
        st = items.Store()
        h.harvest(1440, st)
        self.assertGreater(st.amount("milk"), 10.0)
        self.assertGreater(st.amount("egg"), 0.1)

    def test_underfed_animals_lose_condition_and_die(self):
        h = livestock.Herd()
        h.add("sheep", 3)
        st = items.Store()
        r = rng.Rng(1)
        for _ in range(120):
            h.tick(1440, grass_available_kg=0.0, store=st, temp_c=-5.0,
                   sheltered=False, r=r)
        self.assertEqual(len(h.alive), 0, "sheep fed nothing survived a winter")

    def test_breeding_is_bounded_by_fodder(self):
        """Eight hens must not become a hundred and then starve together.

        A farmer does not let the flock breed past what the hayrick carries;
        without that gate the flock exploded every summer and died every
        winter, which is a thing that happens to nobody who keeps hens.
        """
        def run(capacity):
            h = livestock.Herd()
            h.add("chicken", 8)
            st = items.Store()
            r = rng.Rng(7)
            for _ in range(400):
                st.add("hay", 40.0)     # never short of feed, only of judgement
                h.tick(1440, grass_available_kg=20.0, store=st, temp_c=14.0,
                       sheltered=True, r=r, fodder_capacity_kg_day=capacity)
            return len(h.alive)

        bounded = run(2.0)
        unbounded = run(1e9)
        self.assertLess(bounded, unbounded * 0.5,
                        f"gate did nothing: {bounded} vs {unbounded}")
        self.assertLess(bounded, 45, f"flock ran away to {bounded}")

    def test_manure_lifts_yield_but_not_without_limit(self):
        area = 1000.0
        none = livestock.fertility_bonus(0.0, area)
        some = livestock.fertility_bonus(area * 1.2, area)
        lots = livestock.fertility_bonus(area * 50.0, area)
        self.assertEqual(none, 1.0)
        self.assertGreater(some, 1.05)
        self.assertLessEqual(lots, livestock.MANURE_CEILING + 1e-9)

    def test_dung_is_fuel_on_treeless_ground(self):
        self.assertGreater(items.ITEMS["dung"].fuel_mj_kg, 10.0)
        h = livestock.Herd()
        h.add("cow", 3)
        st = items.Store()
        h.tick(1440, grass_available_kg=999.0, store=st, temp_c=15.0,
               sheltered=True, r=rng.Rng(3))
        self.assertGreater(h.dung_kg, 5.0)
        self.assertGreater(h.dry_dung(600.0), 0.0)

    def test_grazing_and_haymaking_compete(self):
        """Every kilogram a cow eats in August is a kilogram not in the rick.

        Giving them separate pools was the first draft and removed the only
        real decision in keeping animals.
        """
        c = _colony(people=1)
        before = livestock.grass_biomass_kg(c.map)
        self.assertGreater(before, 100.0, "test site has no grass on it")
        livestock.graze_down(c.map, before * 0.5)
        after = livestock.grass_biomass_kg(c.map)
        self.assertLess(after, before * 0.75)


class TestSociety(unittest.TestCase):
    def test_working_together_builds_opinion(self):
        c = _colony(people=2)
        a, b = c.alive
        r = rng.Rng(1)
        for _ in range(60):
            c.society.tick(c.alive, 1440, r)
        op = c.society.opinion(a.name, b.name)
        self.assertNotEqual(op, 0.0, "two months side by side changed nothing")

    def test_opinion_is_asymmetric_in_principle(self):
        """Stored per direction, because unrequited regard is a real thing."""
        s = social.Society()
        s.bond("A", "B").opinion = 70.0
        s.bond("B", "A").opinion = -20.0
        self.assertEqual(s.opinion("A", "B"), 70.0)
        self.assertEqual(s.opinion("B", "A"), -20.0)

    def test_grief_costs_the_colony_work(self):
        """A death in November is a slow spring. That is the whole point."""
        c = _colony(people=3)
        a, b, d = c.alive
        for x, y in ((a, d), (b, d)):
            c.society.bond(x.name, y.name).opinion = 90.0
            c.society.bond(x.name, y.name).kind = "partner"
        before = a.work_speed
        c.society.record_death(d.name, [a, b])
        c.society._apply_morale([a, b])
        for _ in range(40):
            a.tick(60, 18.0, 0.6, False, rng.Rng(2))
        self.assertLess(a.work_speed, before,
                        "losing a partner cost the colony nothing")

    def test_children_are_not_small_adults(self):
        kid = Pawn.random(11, age_range=(1.0, 1.0))
        adult = Pawn.random(11, age_range=(30.0, 30.0))
        self.assertLess(kid.height_cm, adult.height_cm * 0.5)
        self.assertLess(kid.daily_kcal_need(18.0, 0.0),
                        adult.daily_kcal_need(18.0, 0.0) * 0.6)
        self.assertEqual(kid.work_speed, 0.0, "a one-year-old was put to work")


class TestDisease(unittest.TestCase):
    def test_illness_comes_from_decisions_not_dice(self):
        """No event deck: boil the water and build the beds and nobody gets ill."""
        clean = disease.infection_pressure(drinking_raw=False, crowding=0.9,
                                           cold=True, filth=0.0)
        self.assertEqual(clean, {})
        dirty = disease.infection_pressure(drinking_raw=True, crowding=4.0,
                                           cold=True, filth=0.5)
        self.assertIn("enteric", dirty)
        self.assertIn("influenza", dirty)

    def test_the_immunity_race_favours_the_well_fed(self):
        fed = disease.Illness("influenza")
        starved = disease.Illness("influenza")
        for _ in range(20):
            fed.tick(0.5, fed=1.0, rested=1.0, warm=1.0)
            starved.tick(0.5, fed=0.0, rested=0.0, warm=0.0)
        self.assertGreater(fed.immunity, starved.immunity)
        self.assertGreater(fed.immunity - fed.severity,
                           starved.immunity - starved.severity)

    def test_recovery_confers_immunity(self):
        """Without this an outbreak is a closed loop that empties the colony.

        The first version had none, so whoever recovered first was reinfected
        by whoever recovered last, for a hundred and fifty days.
        """
        p = Pawn.random(5)
        st = items.Store()
        st.add("grain", 400.0)
        st.add("berries", 200.0)
        r = rng.Rng(1)
        self.assertTrue(p.catch("influenza"))
        for _ in range(60):
            # Fed, watered and warm: the immunity side of the race runs on
            # exactly those, so a patient left to starve would never recover
            # and the test would be measuring the wrong thing.
            p.eat(st, r)
            p.water_debt_l = 0.0
            p.tick(1440, 20.0, 0.2, False, r)
        self.assertFalse(p.ill, "a fed, rested patient never recovered")
        self.assertFalse(p.catch("influenza"), "reinfected the moment they got up")

    def test_the_starving_do_not_beat_an_infection(self):
        """The reason illness is worth simulating: it is a bill, not a dice roll."""
        p = Pawn.random(5)
        p.catch("influenza")
        r = rng.Rng(1)
        for _ in range(60):
            p.water_debt_l = 0.0
            p.tick(1440, 20.0, 0.2, False, r)   # nothing to eat
            if p.dead:
                break
        self.assertTrue(p.ill or p.dead,
                        "shrugged off influenza on an empty stomach")

    def test_untreated_water_makes_a_colony_ill(self):
        c = _colony(people=6)
        c.store.add("grain", 400)
        c.water_l = 400.0
        c.water_boiled = False
        r = rng.Rng(4)
        for _ in range(120):
            c._tick_disease(1440, r)
        self.assertTrue(any(p.illnesses for p in c.alive),
                        "four months on raw water and nobody got sick")
