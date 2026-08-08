"""Animals, and the economy that runs through them.

Livestock was the largest genuine gap in this simulation, and closing it fixes
three separate models at once rather than adding a fourth. That is the reason
it is worth the trouble:

**Fuel.** A prairie colony has no timber. It cuts hay, at sixty times the
labour per kilogram of felling a tree. What it actually did, historically, was
burn dung -- and dung requires an animal at the other end of the grass.

**Fertility.** Crop yield ran off the soil order alone, so a field was as good
in its twentieth year as its first. Real subsistence farming is a nutrient
loop: grass feeds the animal, the animal manures the field, the field feeds the
people. Without stock in the middle the loop is open and the ground runs down.

**Food.** Milk and eggs are the only foods on a temperate farm that arrive
*daily* rather than once in autumn, and they carry the vitamins that grain does
not. The historical answer to a winter of stored grain is not clever storage,
it is a cow.

So the numbers here are as real as the rest of the project. A dairy cow eats
about 12 kg of dry matter a day and gives 15 litres of milk; a hen eats 120 g
and lays about 250 eggs a year; a ewe carries a fleece of some 2.5 kg. None of
that is tuned for play balance, and the consequences of it -- that a cow eats
four tonnes of hay over a winter, which is a great deal of scything -- are the
interesting part rather than an obstacle to be smoothed away.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import rng
from . import items


@dataclass(frozen=True)
class Breed:
    key: str
    name: str
    #: One animal, one word. Used by the log and the interface.
    singular: str
    plural: str
    #: Mature live mass, kg.
    mass_kg: float
    #: Dry matter eaten per day at maturity, kg. Roughly 2.5% of body mass for
    #: ruminants, rather more for poultry per unit mass.
    feed_kg_day: float
    #: Share of its feed the animal can take by grazing, given standing grass.
    #: Poultry and pigs forage but cannot live on grass alone; cattle can.
    graze_fraction: float
    #: Days from birth to productive maturity.
    mature_days: float
    #: Days of gestation, and how many young per birth.
    gestation_days: float
    litter: float
    #: What the log should call it. A hen does not give birth.
    birth_verb: str = "gave birth to"
    #: Litres of milk a day once mature and recently freshened. Zero for most.
    milk_l_day: float = 0.0
    #: Eggs per day, averaged over the year.
    eggs_day: float = 0.0
    #: Kilograms of fleece per shearing, and how often it can be taken.
    wool_kg: float = 0.0
    #: Dressed meat as a share of live mass, and hide per animal.
    meat_fraction: float = 0.5
    hide_kg: float = 0.0
    #: Dry dung produced per day, kg. Cattle dung is the classic fuel; it dries
    #: to about a fifth of its wet mass and burns at roughly 13 MJ/kg.
    dung_kg_day: float = 0.0
    #: Minimum apparent temperature it survives unsheltered.
    cold_floor_c: float = -25.0


BREEDS: dict[str, Breed] = {b.key: b for b in (
    # Chickens are the cheapest possible entry into daily food: 40 g of grain
    # a day, an egg most days, and they will eat things nothing else will.
    Breed("chicken", "chickens", "hen", "hens", 2.2, 0.12, 0.35,
          150, 21, 6.0, birth_verb="hatched", eggs_day=0.7,
          meat_fraction=0.70, dung_kg_day=0.02, cold_floor_c=-15.0),
    Breed("goat", "goats", "goat", "goats", 55, 1.6, 0.90,
          300, 150, 1.8, milk_l_day=3.0, meat_fraction=0.48, hide_kg=3.5,
          dung_kg_day=0.5, cold_floor_c=-22.0),
    Breed("sheep", "sheep", "sheep", "sheep", 70, 1.9, 0.95,
          330, 147, 1.4, wool_kg=2.5, meat_fraction=0.50, hide_kg=4.0,
          dung_kg_day=0.6, cold_floor_c=-28.0),
    Breed("pig", "pigs", "pig", "pigs", 110, 2.4, 0.25,
          240, 114, 9.0, meat_fraction=0.72, hide_kg=5.0, dung_kg_day=0.8,
          cold_floor_c=-12.0),
    Breed("cow", "cattle", "cow", "cattle", 550, 12.0, 0.95,
          700, 283, 1.05, milk_l_day=15.0, meat_fraction=0.55, hide_kg=30.0,
          dung_kg_day=5.5, cold_floor_c=-30.0),
)}

#: Dry dung burns at about 13 MJ/kg -- less than wood, and it is what a
#: treeless colony actually has.
DUNG_MJ_KG = 13.0

#: Kilograms of manure that meaningfully improves one square metre of cropland
#: for a season. Roughly 25 t/ha, which is a real application rate.
MANURE_KG_PER_M2 = 2.5

#: A field's fertility can be lifted this far above the soil's own figure by
#: manuring, and no further. You cannot make the Sahara into Iowa with dung.
MANURE_CEILING = 1.35


@dataclass
class Animal:
    breed: Breed
    #: Days since birth.
    age_days: float = 0.0
    female: bool = True
    #: Body condition, 0-1. Falls when underfed; below 0.25 the animal dies.
    condition: float = 0.8
    #: Days until it gives birth, or -1 if not pregnant.
    pregnant_days: float = -1.0
    #: Days since it last gave birth. Milk yield falls away over a lactation.
    lactation_days: float = -1.0
    #: Days of fleece growth since the last shearing.
    fleece_days: float = 0.0
    #: Where it is standing, for the map.
    x: int = 0
    y: int = 0
    dead: bool = False

    @property
    def mature(self) -> bool:
        return self.age_days >= self.breed.mature_days

    @property
    def mass_kg(self) -> float:
        """Live mass. Young animals are smaller, thin ones lighter."""
        grown = min(1.0, 0.10 + 0.90 * self.age_days / self.breed.mature_days)
        return self.breed.mass_kg * grown * (0.55 + 0.45 * self.condition)

    @property
    def feed_kg_day(self) -> float:
        return self.breed.feed_kg_day * self.mass_kg / self.breed.mass_kg

    @property
    def milk_l_day(self) -> float:
        """Milk, if she is in lactation.

        Yield peaks a few weeks after calving and tails off over about ten
        months, which is why a colony that wants milk all year needs more than
        one cow and has to think about when they calve.
        """
        b = self.breed
        if b.milk_l_day <= 0 or not self.female or not self.mature:
            return 0.0
        d = self.lactation_days
        if d < 0 or d > 305:
            return 0.0
        curve = min(1.0, 0.55 + d / 45.0) if d < 45 else max(
            0.0, 1.0 - (d - 45) / 300.0)
        return b.milk_l_day * curve * self.condition

    @property
    def eggs_day(self) -> float:
        b = self.breed
        if b.eggs_day <= 0 or not self.female or not self.mature:
            return 0.0
        return b.eggs_day * self.condition


def _name_for(breed: Breed, n: int) -> str:
    return f"{n} {breed.singular if n == 1 else breed.plural}"


@dataclass
class Herd:
    """Every animal a colony owns, and the arithmetic that runs them.

    Kept as one object rather than scattered through the colony because the
    interesting questions are about the herd -- can it be fed through the
    winter, is it breeding faster than it is eaten -- and those are answered in
    aggregate.
    """

    animals: list[Animal] = field(default_factory=list)
    #: Kilograms of dung collected but not yet dried into fuel.
    dung_kg: float = 0.0
    #: Kilograms of manure available to spread on fields.
    manure_kg: float = 0.0

    def __len__(self) -> int:
        return len(self.animals)

    @property
    def alive(self) -> list[Animal]:
        return [a for a in self.animals if not a.dead]

    def count(self, key: str) -> int:
        return sum(1 for a in self.alive if a.breed.key == key)

    def summary(self) -> str:
        by: dict[str, int] = {}
        for a in self.alive:
            by[a.breed.key] = by.get(a.breed.key, 0) + 1
        if not by:
            return "no animals"
        return ", ".join(_name_for(BREEDS[k], n) for k, n in sorted(by.items()))

    @property
    def feed_kg_day(self) -> float:
        return sum(a.feed_kg_day for a in self.alive)

    def add(self, key: str, n: int = 1, age_days: float | None = None,
            female: bool = True) -> list[Animal]:
        b = BREEDS[key]
        out = []
        for i in range(n):
            a = Animal(b, age_days=b.mature_days if age_days is None
                       else age_days,
                       female=female if n == 1 else (i % 4 != 0))
            # A milker acquired as an adult is bought in milk -- nobody sells
            # you a dry cow and calls it a dairy animal. Leaving them at
            # lactation -1 meant a colony's cattle gave nothing at all until
            # they happened to calve, which is most of a year of feeding an
            # animal for its dung.
            if a.female and a.mature and b.milk_l_day > 0:
                a.lactation_days = 30.0 + 25.0 * i
            self.animals.append(a)
            out.append(a)
        return out

    # ---- the day ---------------------------------------------------------

    def tick(self, minutes: float, *, grass_available_kg: float,
             store: items.Store, temp_c: float, sheltered: bool,
             r: rng.Rng, fodder_capacity_kg_day: float = 1e9,
             ) -> tuple[list[str], float]:
        """Advance the herd. Returns notable events and grass actually eaten.

        The feeding order is the whole model: an animal grazes what it can,
        then eats fodder from the store, and only if both fall short does it
        lose condition. A colony discovers in its first January that a cow
        eats twelve kilograms a day whatever the weather is doing.
        """
        if not self.animals:
            return [], 0.0
        days = minutes / 1440.0
        ev: list[str] = []
        grazed = 0.0
        grass_left = grass_available_kg

        for a in self.alive:
            need = a.feed_kg_day * days
            got = 0.0
            # Graze first -- it is free, and it is what the animal prefers.
            if temp_c > 2.0 and grass_left > 0:
                take = min(need * a.breed.graze_fraction, grass_left)
                got += take
                grass_left -= take
                grazed += take
            if got < need:
                # Then fodder: hay first, then grain, which is expensive and a
                # colony feeling wealthy enough to feed grain to a pig usually
                # is not.
                for key, value in (("hay", 1.0), ("vegetables", 1.0),
                                   ("potato", 1.0), ("grain", 1.0)):
                    if got >= need:
                        break
                    want = (need - got) / value
                    got += store.take(key, want) * value
            if got >= need * 0.98:
                a.condition = min(1.0, a.condition + 0.03 * days)
            else:
                short = 1.0 - got / max(1e-6, need)
                a.condition -= 0.055 * short * days

            # Cold kills thin animals first, and shelter is the difference.
            floor = a.breed.cold_floor_c + (12.0 if not sheltered else 0.0)
            if temp_c < floor:
                a.condition -= 0.05 * days * min(3.0, (floor - temp_c) / 8.0)

            if a.condition <= 0.25:
                a.dead = True
                ev.append(f"a {a.breed.singular} died "
                          f"({'cold' if temp_c < floor else 'starved'})")
                continue

            a.age_days += days
            if a.lactation_days >= 0:
                a.lactation_days += days
            a.fleece_days += days
            if a.pregnant_days > 0:
                a.pregnant_days -= days
                if a.pregnant_days <= 0:
                    ev += self._give_birth(a, r)

            self.dung_kg += a.breed.dung_kg_day * days * (
                a.mass_kg / a.breed.mass_kg)

        self.animals = [a for a in self.animals if not a.dead]
        self._breed(days, r, fodder_capacity_kg_day)
        return ev, grazed

    def _give_birth(self, mother: Animal, r: rng.Rng) -> list[str]:
        b = mother.breed
        n = max(1, int(b.litter + (1 if r.random() < b.litter % 1 else 0)))
        if mother.condition < 0.45:
            n = max(0, n - 1)          # a thin mother loses the litter
        for _ in range(n):
            self.animals.append(Animal(b, age_days=0.0,
                                       female=r.random() < 0.5,
                                       condition=0.7))
        mother.pregnant_days = -1.0
        mother.lactation_days = 0.0
        mother.condition = max(0.3, mother.condition - 0.12)
        if n <= 0:
            return [f"a {b.singular} lost her young"]
        return [f"a {b.singular} {b.birth_verb} {n}"]

    def _breed(self, days: float, r: rng.Rng, capacity_kg_day: float) -> None:
        """Animals reproduce, given a male, condition, and fodder to spare.

        This is what makes livestock an investment rather than a supply: two
        goats become eight in three years if they are fed, and nothing if they
        are not.

        The capacity gate is not an artificial population cap, it is the
        stockman. A farmer does not let the flock breed past what the hayrick
        will carry -- eggs are eaten rather than set, and the ram is kept away
        from the ewes. Without it eight hens became a hundred in one summer
        and then all starved together, which is a thing that happens to
        nobody who has ever kept hens.
        """
        room = capacity_kg_day - self.feed_kg_day
        if room <= 0:
            return
        eagerness = min(1.0, room / max(1.0, capacity_kg_day * 0.35))
        for key in BREEDS:
            mob = [a for a in self.alive if a.breed.key == key and a.mature]
            if not any(not a.female for a in mob):
                continue
            for a in mob:
                if not a.female or a.pregnant_days > 0 or a.condition < 0.55:
                    continue
                if 0 <= a.lactation_days < 60:
                    continue
                # Roughly one conception per oestrus cycle in good condition.
                if r.random() < days / 30.0 * (a.condition - 0.4) * eagerness:
                    a.pregnant_days = a.breed.gestation_days

    # ---- what the colony takes off them ----------------------------------

    def harvest(self, minutes: float, store: items.Store) -> dict[str, float]:
        """Milk and eggs, which arrive every single day.

        That is their whole point. Grain arrives once, in August, and has to
        last; a cow arrives every morning, and carries the vitamin C and the
        protein that a winter of stored grain does not.
        """
        days = minutes / 1440.0
        out: dict[str, float] = {}
        # The store keeps kilograms, so convert: a litre of milk is 1.03 kg
        # and an egg is 60 grams.
        milk = sum(a.milk_l_day for a in self.alive) * days * 1.03
        eggs = sum(a.eggs_day for a in self.alive) * days * 0.06
        if milk > 0:
            store.add("milk", milk)
            out["milk"] = milk
        if eggs > 0:
            store.add("egg", eggs)
            out["egg"] = eggs
        return out

    def shear(self, effort_min: float) -> float:
        """Take fleece from anything that has grown one. Returns kilograms.

        About 25 minutes an animal by hand, once a year -- a real shearer is
        far quicker, but a colonist with hand shears is not a real shearer.
        """
        got = 0.0
        for a in self.alive:
            if effort_min < 25.0:
                break
            if a.breed.wool_kg <= 0 or not a.mature or a.fleece_days < 300:
                continue
            effort_min -= 25.0
            got += a.breed.wool_kg * a.condition
            a.fleece_days = 0.0
        return got

    def slaughter(self, key: str, store: items.Store,
                  keep_breeding: bool = True) -> str | None:
        """Kill one animal for meat, choosing the one a farmer would.

        Surplus males first, then the oldest and thinnest. Never the last
        breeding female, because a herd you have eaten is not a herd.
        """
        mob = [a for a in self.alive if a.breed.key == key]
        if not mob:
            return None
        females = [a for a in mob if a.female and a.mature]
        males = [a for a in mob if not a.female and a.mature]

        def pick() -> Animal | None:
            if len(males) > 1:
                return max(males, key=lambda a: a.age_days)
            young = [a for a in mob if not a.mature]
            if young:
                return max(young, key=lambda a: -a.condition)
            if keep_breeding and len(females) <= 1:
                return None
            return max(mob, key=lambda a: a.age_days)

        a = pick()
        if a is None:
            return None
        b = a.breed
        meat = a.mass_kg * b.meat_fraction
        if meat < 0.5:
            return None        # a chick is not a dinner; leave it to grow
        store.add("meat", meat)
        if b.hide_kg > 0:
            store.add("hide", b.hide_kg * a.mass_kg / b.mass_kg)
        # Tallow is a real and useful by-product: fat is 8,800 kcal/kg and
        # burns, which matters to a colony short of both food and light.
        store.add("fat", a.mass_kg * 0.06)
        a.dead = True
        self.animals = [q for q in self.animals if not q.dead]
        return f"slaughtered a {b.singular} for {meat:.1f} kg of meat"

    def dry_dung(self, effort_min: float) -> float:
        """Turn collected dung into burnable fuel. Returns kilograms.

        Roughly 1.2 person-minutes a kilogram to gather, form and stack it --
        far cheaper than twisting hay, and the reason a treeless colony wants
        cattle before it wants anything else.
        """
        take = min(self.dung_kg, effort_min / 1.2)
        if take <= 0:
            return 0.0
        self.dung_kg -= take
        # Two thirds of the collected mass is water and goes up in the drying.
        return take * 0.34

    def collect_manure(self, effort_min: float) -> float:
        take = min(self.dung_kg, effort_min / 0.5)
        if take <= 0:
            return 0.0
        self.dung_kg -= take
        self.manure_kg += take
        return take


def grass_biomass_kg(map_obj) -> float:
    """Standing grass on a site, in kilograms of dry matter.

    Shared with the hay-cutting model so that a colony cannot both graze a
    pasture bare and cut it for winter fodder, which is precisely the trade a
    real smallholder is making.
    """
    from ..local import terrain
    total = 0.0
    for o in map_obj.objects.values():
        if isinstance(o, terrain.Plant) and o.species.hay_kg > 0:
            total += o.hay_kg
    return total


def graze_down(map_obj, kg: float) -> None:
    """Remove grazed biomass from the map, so grazing is visible and finite."""
    from ..local import terrain
    if kg <= 0:
        return
    stand = [o for o in map_obj.objects.values()
             if isinstance(o, terrain.Plant) and o.species.hay_kg > 0
             and o.growth > 0.05]
    if not stand:
        return
    total = sum(o.hay_kg for o in stand)
    if total <= 0:
        return
    share = min(1.0, kg / total)
    for o in stand:
        o.growth *= 1.0 - share


def fertility_bonus(manure_kg: float, area_m2: float) -> float:
    """How much a manured field out-yields an unmanured one, as a multiplier.

    Diminishing, and capped: dung improves ground, it does not transform it.
    """
    if area_m2 <= 0 or manure_kg <= 0:
        return 1.0
    rate = manure_kg / (area_m2 * MANURE_KG_PER_M2)
    return 1.0 + (MANURE_CEILING - 1.0) * (1.0 - math.exp(-2.2 * rate))
