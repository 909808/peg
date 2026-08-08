"""Materials, items and stores, in real units.

Everything is kilograms, kilocalories, litres, kilowatt-hours. That is not
pedantry -- it is what makes the simulation answerable. "Do we have enough
food for winter" is a question with an arithmetic answer when food is measured
in calories and people burn calories at a rate you can compute from their
mass and their workload.

Nutrition tracks three axes because two of them kill you differently. Calories
run out and you starve slowly. Protein runs out and you waste even while
eating. Vitamin C runs out and, about ninety days later, your gums bleed, old
wounds reopen and you die of scurvy -- which is a genuinely realistic failure
mode for a colony living on grain and dried meat through a long winter, and
one that never appears in a game where food is a single number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ItemDef:
    key: str
    name: str
    #: Kilograms per unit.
    kg: float
    #: Whether many units merge into one stack.
    stackable: bool = True
    # ---- nutrition, per kilogram ----
    kcal_kg: float = 0.0
    protein_g_kg: float = 0.0
    vit_c_mg_kg: float = 0.0
    #: Days at 20 C before it is inedible. Zero means it does not spoil.
    shelf_days: float = 0.0
    # ---- physical ----
    #: MJ of heat per kg when burned.
    fuel_mj_kg: float = 0.0
    #: Rough barter value, for trade between factions.
    value: float = 1.0
    tags: tuple[str, ...] = ()


def _d(key, name, kg, **kw) -> ItemDef:
    return ItemDef(key=key, name=name, kg=kg, **kw)


ITEMS: dict[str, ItemDef] = {d.key: d for d in (
    # ---- raw food -------------------------------------------------------
    # Figures are approximately real: wheat is 3400 kcal/kg, potatoes 770,
    # lean meat 1400, fat 9000.
    _d("grain", "grain", 1.0, kcal_kg=3400, protein_g_kg=120, shelf_days=900,
       value=1.2, tags=("food", "raw")),
    _d("potato", "potatoes", 1.0, kcal_kg=770, protein_g_kg=20, vit_c_mg_kg=196,
       shelf_days=120, value=0.7, tags=("food", "raw")),
    _d("beans", "beans", 1.0, kcal_kg=3400, protein_g_kg=215, shelf_days=800,
       value=1.4, tags=("food", "raw")),
    _d("vegetables", "vegetables", 1.0, kcal_kg=280, protein_g_kg=18,
       vit_c_mg_kg=480, shelf_days=14, value=1.0, tags=("food", "raw")),
    _d("berries", "berries", 1.0, kcal_kg=520, protein_g_kg=9, vit_c_mg_kg=530,
       shelf_days=6, value=1.1, tags=("food", "raw")),
    _d("meat", "meat", 1.0, kcal_kg=1900, protein_g_kg=200, shelf_days=3,
       value=2.2, tags=("food", "raw")),
    _d("fish", "fish", 1.0, kcal_kg=1400, protein_g_kg=205, vit_c_mg_kg=10,
       shelf_days=2, value=1.8, tags=("food", "raw")),
    _d("fat", "rendered fat", 1.0, kcal_kg=8800, shelf_days=400, fuel_mj_kg=37,
       value=3.0, tags=("food", "raw")),
    # Milk and eggs are the only foods on a temperate farm that arrive every
    # single day rather than once in autumn, and they carry what stored grain
    # does not. Whole milk is 640 kcal/L and holds about 10 mg of vitamin C;
    # it also goes off in two days without a cold store, which is why every
    # dairying culture on Earth independently invented cheese.
    # Everything is kilograms, including these: a litre of milk is 1.03 kg and
    # an egg is 60 g, and the moment a unit stops being a kilogram the
    # nutrition arithmetic quietly doubles. That bug has been in this file
    # once already.
    _d("milk", "milk", 1.0, kcal_kg=620, protein_g_kg=33, vit_c_mg_kg=10,
       shelf_days=2, value=1.4, tags=("food", "raw")),
    _d("cheese", "cheese", 1.0, kcal_kg=4000, protein_g_kg=250, shelf_days=240,
       value=6.0, tags=("food", "cooked")),
    _d("butter", "butter", 1.0, kcal_kg=7200, protein_g_kg=9, shelf_days=60,
       fuel_mj_kg=33, value=7.0, tags=("food", "cooked")),
    _d("egg", "eggs", 1.0, kcal_kg=1430, protein_g_kg=126, vit_c_mg_kg=0,
       shelf_days=28, value=2.0, tags=("food", "raw")),
    # ---- prepared -------------------------------------------------------
    # These weigh a kilogram per unit like everything else. They used to be
    # 0.6 and 0.5, which quietly broke the books: stack amounts are in units
    # but nutrition is per kilogram, so half-kilo rations were counted at
    # their full per-kilogram calorie value and every batch of them minted
    # food out of nothing.
    _d("meal", "cooked meal", 1.0, kcal_kg=2400, protein_g_kg=130,
       vit_c_mg_kg=120, shelf_days=2, value=3.0, tags=("food", "cooked")),
    _d("preserved", "preserved ration", 1.0, kcal_kg=3000, protein_g_kg=150,
       shelf_days=600, value=4.0, tags=("food", "cooked")),
    # ---- construction and industry --------------------------------------
    _d("wood", "wood", 1.0, fuel_mj_kg=16, value=0.4, tags=("build", "fuel")),
    # Twisted prairie hay. Dry grass is about 16 MJ/kg, but it is bulky, burns
    # fast and never dries fully, so call it 14.5. This is not a curiosity: on
    # the tallgrass prairie there is no timber for hundreds of kilometres, and
    # settlers heated sod houses by twisting hay into hard "cats" for hours a
    # day. Without it the best farmland on the planet is a death sentence.
    _d("hay", "twisted hay", 1.0, fuel_mj_kg=14.5, value=0.1, tags=("fuel",)),
    # Dried dung, at 13 MJ/kg. It is the historical answer to heating a
    # treeless landscape, it costs a third of the labour of twisting hay, and
    # it is the reason cattle are worth more on a prairie than their milk
    # alone would suggest.
    _d("dung", "dried dung", 1.0, fuel_mj_kg=13.0, value=0.1, tags=("fuel",)),
    _d("plank", "planks", 1.0, fuel_mj_kg=16, value=1.0, tags=("build", "fuel")),
    _d("stone", "stone block", 1.0, value=0.5, tags=("build",)),
    _d("clay", "clay", 1.0, value=0.3, tags=("build",)),
    _d("brick", "fired brick", 1.0, value=1.1, tags=("build",)),
    _d("charcoal", "charcoal", 1.0, fuel_mj_kg=30, value=1.6, tags=("fuel",)),
    _d("coal", "coal", 1.0, fuel_mj_kg=27, value=1.4, tags=("fuel",)),
    _d("oil", "crude oil", 1.0, fuel_mj_kg=42, value=2.5, tags=("fuel",)),
    _d("diesel", "diesel", 1.0, fuel_mj_kg=45, value=6.0, tags=("fuel",)),
    _d("iron_ore", "iron ore", 1.0, value=0.6, tags=("ore",)),
    _d("copper_ore", "copper ore", 1.0, value=0.9, tags=("ore",)),
    _d("iron", "iron", 1.0, value=3.5, tags=("metal", "build")),
    _d("steel", "steel", 1.0, value=6.0, tags=("metal", "build")),
    _d("copper", "copper", 1.0, value=8.0, tags=("metal",)),
    _d("component", "machined component", 0.4, value=28.0, tags=("tech",)),
    # ---- soft goods -----------------------------------------------------
    _d("fibre", "plant fibre", 1.0, value=0.6, tags=("soft",)),
    _d("cloth", "cloth", 1.0, value=3.0, tags=("soft",)),
    _d("hide", "raw hide", 1.0, shelf_days=8, value=2.0, tags=("soft",)),
    _d("leather", "leather", 1.0, value=5.0, tags=("soft",)),
    # ---- medical and other ----------------------------------------------
    _d("herbs", "medicinal herbs", 0.2, shelf_days=200, value=3.0, tags=("med",)),
    _d("medicine", "medicine", 0.2, shelf_days=1500, value=14.0, tags=("med",)),
    _d("salt", "salt", 1.0, value=1.5, tags=("preserve",)),
    _d("ammo", "ammunition", 0.025, value=2.2, tags=("military",)),
    _d("powder", "propellant", 1.0, value=9.0, tags=("military",)),
)}

FOOD_KEYS = tuple(k for k, d in ITEMS.items() if "food" in d.tags)
FUEL_KEYS = tuple(k for k, d in ITEMS.items() if "fuel" in d.tags)

#: Daily requirements for an adult doing manual work.
RDA_PROTEIN_G = 56.0
RDA_VIT_C_MG = 75.0
#: Below this many days of accumulated vitamin C deficit, scurvy sets in.
SCURVY_ONSET_DAYS = 75.0


@dataclass
class Stack:
    key: str
    amount: float                 # in units (kg for most things)
    #: Days of spoilage accumulated, scaled by storage temperature.
    age_days: float = 0.0

    @property
    def item(self) -> ItemDef:
        return ITEMS[self.key]

    @property
    def kg(self) -> float:
        return self.amount * self.item.kg

    @property
    def spoiled(self) -> bool:
        d = self.item.shelf_days
        return d > 0 and self.age_days >= d

    @property
    def freshness(self) -> float:
        d = self.item.shelf_days
        if d <= 0:
            return 1.0
        return max(0.0, 1.0 - self.age_days / d)


class Store:
    """A colony's stores. One bucket per item type, not per physical pile --
    the interesting decisions are about totals, not about shelving."""

    def __init__(self) -> None:
        self.stacks: dict[str, Stack] = {}

    def __contains__(self, key: str) -> bool:
        return self.amount(key) > 0

    def amount(self, key: str) -> float:
        s = self.stacks.get(key)
        return s.amount if s else 0.0

    def total_kg(self) -> float:
        return sum(s.kg for s in self.stacks.values())

    def add(self, key: str, amount: float, age_days: float = 0.0) -> None:
        if amount <= 0:
            return
        s = self.stacks.get(key)
        if s is None:
            self.stacks[key] = Stack(key, amount, age_days)
            return
        # Weighted-average age: adding fresh stock to an old pile genuinely
        # does extend how long the pile as a whole lasts.
        total = s.amount + amount
        s.age_days = (s.age_days * s.amount + age_days * amount) / total
        s.amount = total

    def take(self, key: str, amount: float) -> float:
        """Remove up to ``amount``. Returns how much was actually taken."""
        s = self.stacks.get(key)
        if not s:
            return 0.0
        got = min(s.amount, amount)
        s.amount -= got
        if s.amount <= 1e-9:
            del self.stacks[key]
        return got

    def has(self, key: str, amount: float) -> bool:
        return self.amount(key) >= amount

    # ---- nutrition ------------------------------------------------------

    def food_kcal(self) -> float:
        return sum(s.amount * s.item.kcal_kg * (0.4 + 0.6 * s.freshness)
                   for s in self.stacks.values() if "food" in s.item.tags)

    def fuel_mj(self) -> float:
        return sum(s.amount * s.item.fuel_mj_kg
                   for s in self.stacks.values() if s.item.fuel_mj_kg > 0)

    def best_food(self) -> str | None:
        """Pick what to eat next.

        Eats perishable food first, and prefers cooked over raw -- both are
        what a sensible quartermaster does, and both matter: cooking roughly
        doubles the calories a body extracts from grain, and food eaten before
        it spoils is food that was not wasted.
        """
        best = None
        best_score = -1.0
        for s in self.stacks.values():
            it = s.item
            if "food" not in it.tags or s.amount <= 0 or s.spoiled:
                continue
            score = it.kcal_kg / 1000.0
            if "cooked" in it.tags:
                score *= 1.6
            if it.shelf_days > 0:
                # Urgency: the closer to spoiling, the sooner it should go.
                score *= 1.0 + 2.5 * (1.0 - s.freshness)
            if score > best_score:
                best_score = score
                best = s.key
        return best

    def best_vitamin_c(self) -> str | None:
        """The best available source of vitamin C, if any.

        Freshness is squared in the score because vitamin C is the nutrient
        that degrades fastest in storage -- year-old dried vegetables are
        food, but they are not an antiscorbutic.
        """
        best = None
        best_score = 0.0
        for st in self.stacks.values():
            it = st.item
            if it.vit_c_mg_kg <= 0 or st.amount <= 0 or st.spoiled:
                continue
            score = it.vit_c_mg_kg * (st.freshness ** 2)
            if score > best_score:
                best_score = score
                best = st.key
        # Any real source beats none. An absolute threshold of 40 meant that
        # once stored potatoes had aged past about two months they stopped
        # counting at all, and a colony sitting on two tonnes of them died of
        # scurvy rather than eat a slightly weaker antiscorbutic.
        return best if best_score > 8.0 else None

    def tick_spoilage(self, minutes: float, temp_c: float) -> dict[str, float]:
        """Age perishables. Returns what was lost, by item.

        Spoilage roughly doubles per 10 C, the standard Q10 rule, so a cold
        store is worth building and a tropical colony has a real problem.
        """
        rate = 2.0 ** ((temp_c - 20.0) / 10.0)
        rate = max(0.02, min(8.0, rate))
        days = minutes / 1440.0 * rate
        lost: dict[str, float] = {}
        for key in list(self.stacks):
            s = self.stacks[key]
            if s.item.shelf_days <= 0:
                continue
            s.age_days += days
            if s.spoiled:
                lost[key] = s.amount
                del self.stacks[key]
        return lost

    def summary(self, limit: int = 8) -> str:
        rows = sorted(self.stacks.values(), key=lambda s: -s.kg)[:limit]
        return ", ".join(f"{s.item.name} {s.amount:.0f}" for s in rows) or "empty"


# --------------------------------------------------------------------------
# crops
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Crop:
    key: str
    name: str
    yields: str
    #: Growing degree-days above ``base_c`` needed from sowing to harvest.
    gdd_needed: float
    base_c: float
    #: Kills the crop if the temperature drops below this.
    frost_kill_c: float
    #: Kilograms harvested per square metre at full fertility and full water.
    kg_per_m2: float
    #: Millimetres of water needed over the season.
    water_mm: float
    #: Fraction of the harvest that must be kept back as seed.
    seed_fraction: float = 0.08


CROPS: dict[str, Crop] = {c.key: c for c in (
    Crop("wheat", "wheat", "grain", 1500, 5.0, -8.0, 0.45, 450),
    Crop("barley", "barley", "grain", 1200, 3.0, -10.0, 0.40, 350),
    Crop("rice", "rice", "grain", 2200, 10.0, 4.0, 0.60, 1200),
    Crop("maize", "maize", "grain", 2400, 10.0, 0.0, 0.70, 550),
    Crop("potato", "potatoes", "potato", 1300, 4.0, -2.0, 2.60, 500),
    Crop("beans", "beans", "beans", 1400, 8.0, 0.0, 0.28, 400),
    Crop("cabbage", "cabbage", "vegetables", 900, 4.0, -6.0, 3.40, 380),
    Crop("flax", "flax", "fibre", 1250, 5.0, -4.0, 0.75, 400),
    # Cool-climate staples. Without these, maritime and subarctic sites
    # support no crop at all and every colony founded on one starves.
    Crop("oats", "oats", "grain", 950, 4.0, -8.0, 0.32, 420),
    Crop("rye", "rye", "grain", 1000, 2.0, -14.0, 0.30, 320),
    Crop("turnip", "turnips", "vegetables", 550, 3.0, -6.0, 3.00, 340),
)}


def crop_suitability(crop: Crop, clim, irrigated: bool = False,
                     gdd_available: float | None = None) -> float:
    """How well a crop suits a climate, 0-1.

    Zero means it will not ripen at all -- which is the correct answer for
    maize north of the treeline no matter how much labour you throw at it.

    Growing degree-days are accumulated at *this crop's* base temperature.
    Comparing every crop against a single figure computed at 10 C was wrong
    in a way that mattered enormously: rye starts growing at 2 C and barley at
    3, so a cool maritime climate that really does support both scored zero
    for everything and starved colonies that had no business starving.
    """
    gdd = (clim.growing_degree_days(base=crop.base_c)
           if gdd_available is None else gdd_available)
    coldest_c = clim.coldest
    precip_mm = clim.annual_precip
    if gdd < crop.gdd_needed:
        return 0.0
    if coldest_c < crop.frost_kill_c - 6.0 and not irrigated:
        # Hard winters need the crop sown in spring; only very short-season
        # crops manage it.
        if crop.gdd_needed > 1600:
            return 0.0
    heat = min(1.0, gdd / (crop.gdd_needed * 1.5))
    water = precip_mm / crop.water_mm
    if irrigated:
        water = max(water, 1.0)
    water = min(1.0, water) if water < 1.0 else min(1.0, 2.4 - water * 0.7)
    return max(0.0, heat * max(0.0, water))


def crop_value_per_m2(c: Crop, suitability: float) -> float:
    """How much a crop is actually worth planting, per square metre.

    Tonnage is the wrong measure and picking by it is how a colony ends up
    with four tonnes of cabbage that are compost by December. What matters is
    calories that survive to be eaten, so the score is yield times energy
    density times a storability factor. That ordering puts maize, potatoes and
    grain at the top -- which is what people actually grew to overwinter on.
    """
    out = ITEMS[c.yields]
    kcal_m2 = c.kg_per_m2 * out.kcal_kg
    if out.shelf_days <= 0:
        keep = 1.0
    else:
        # Full credit at a year, heavily discounted below a season.
        keep = 0.30 + 0.70 * min(1.0, out.shelf_days / 240.0)
    # A little credit for vitamins: a crop that prevents scurvy is worth
    # growing even if it is not the densest source of energy.
    vit = 1.0 + min(0.35, out.vit_c_mg_kg / 1400.0)
    return suitability * kcal_m2 * keep * vit


def best_crops(clim, irrigated: bool = False,
               season_fraction: float = 1.0) -> list[tuple[Crop, float]]:
    """Rank the crops worth sowing in a climate, best first.

    ``season_fraction`` scales the heat available, for deciding whether it is
    too late in the year to sow at all.
    """
    out = []
    for c in CROPS.values():
        gdd = clim.growing_degree_days(base=c.base_c) * season_fraction
        out.append((c, crop_suitability(c, clim, irrigated, gdd_available=gdd)))
    out = [(c, s) for c, s in out if s > 0.05]
    out.sort(key=lambda cs: -crop_value_per_m2(cs[0], cs[1]))
    return out


# --------------------------------------------------------------------------
# recipes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Recipe:
    key: str
    name: str
    inputs: tuple[tuple[str, float], ...]
    outputs: tuple[tuple[str, float], ...]
    #: Person-minutes of work at skill 5.
    work_min: float
    skill: str
    #: Workshop required, if any.
    station: str = ""
    #: kWh of electricity, if any.
    kwh: float = 0.0
    #: MJ of heat that must be supplied by burning something.
    heat_mj: float = 0.0


RECIPES: dict[str, Recipe] = {r.key: r for r in (
    # Every food recipe below conserves calories. Cooking earns a modest
    # uplift because gelatinised starch and denatured protein really are more
    # digestible than the raw ingredients; drying is a small net loss. The
    # first versions of these turned 1,260 kcal of grain into 2,400 kcal of
    # dinner and 1,140 kcal of cabbage into a 3,000 kcal ration, which is a
    # perpetual motion machine with a kitchen attached.
    Recipe("cook_meal", "cook a meal", (("grain", 0.55), ("vegetables", 0.80)),
           (("meal", 1.0),), 14, "cooking", "kitchen", heat_mj=2.5),
    Recipe("cook_meat", "cook a meat meal", (("meat", 0.95), ("vegetables", 0.90)),
           (("meal", 1.0),), 14, "cooking", "kitchen", heat_mj=2.5),
    Recipe("preserve", "salt and dry rations", (("meat", 1.75), ("salt", 0.05)),
           (("preserved", 1.0),), 28, "cooking", "kitchen"),
    # Drying is what turns an autumn glut into something that survives to
    # spring. It costs most of the vitamin C, which is precisely why a colony
    # living on dried stores through a long winter gets scurvy anyway. It also
    # takes a great deal of fresh produce, because vegetables are mostly water.
    Recipe("dry_produce", "dry produce", (("vegetables", 11.8),),
           (("preserved", 1.0),), 30, "cooking", "kitchen"),
    Recipe("dry_berries", "dry berries", (("berries", 6.4),),
           (("preserved", 1.0),), 24, "cooking", "kitchen"),
    # Roots are the staple a colony actually grows, and until this existed
    # there was no way to carry a potato harvest through to spring: two
    # tonnes came in every August and composted by Christmas. Drying them is
    # real practice -- Andean chuno is exactly this.
    Recipe("dry_potato", "dry roots", (("potato", 4.3),),
           (("preserved", 1.0),), 26, "cooking", "kitchen"),
    # Milk keeps two days; cheese keeps eight months. Every dairying culture
    # on Earth worked this out independently, and a colony with a cow and no
    # cheese press is throwing away most of what she gives. Ten litres to the
    # kilogram is the real ratio for a hard cheese, and it conserves calories
    # almost exactly -- the whey carries off the rest.
    Recipe("make_cheese", "press cheese", (("milk", 7.0), ("salt", 0.02)),
           (("cheese", 1.0),), 34, "cooking", "kitchen"),
    Recipe("churn_butter", "churn butter", (("milk", 12.0),),
           (("butter", 1.0),), 26, "cooking", "kitchen"),
    Recipe("saw_planks", "saw planks", (("wood", 1.4),), (("plank", 1.0),),
           9, "crafting", "sawpit"),
    Recipe("burn_charcoal", "burn charcoal", (("wood", 5.0),),
           (("charcoal", 1.0),), 40, "crafting", "kiln"),
    Recipe("fire_brick", "fire bricks", (("clay", 1.2),), (("brick", 1.0),),
           14, "crafting", "kiln", heat_mj=4.0),
    Recipe("smelt_iron", "smelt iron", (("iron_ore", 2.6), ("charcoal", 0.9)),
           (("iron", 1.0),), 30, "crafting", "smelter", heat_mj=18.0),
    Recipe("make_steel", "make steel", (("iron", 1.05), ("charcoal", 0.25)),
           (("steel", 1.0),), 45, "crafting", "smelter", heat_mj=22.0),
    Recipe("smelt_copper", "smelt copper", (("copper_ore", 3.2), ("charcoal", 0.8)),
           (("copper", 1.0),), 32, "crafting", "smelter", heat_mj=16.0),
    Recipe("weave_cloth", "weave cloth", (("fibre", 1.8),), (("cloth", 1.0),),
           26, "crafting", "workshop"),
    Recipe("tan_leather", "tan leather", (("hide", 1.5),), (("leather", 1.0),),
           40, "crafting", "workshop"),
    Recipe("make_medicine", "compound medicine", (("herbs", 3.0), ("cloth", 0.3)),
           (("medicine", 1.0),), 35, "medicine", "workshop"),
    Recipe("make_component", "machine a component",
           (("steel", 0.8), ("copper", 0.2)), (("component", 1.0),),
           120, "crafting", "machine_shop", kwh=2.2),
    Recipe("make_ammo", "load ammunition",
           (("copper", 0.02), ("powder", 0.006), ("iron", 0.008)),
           (("ammo", 1.0),), 1.4, "crafting", "workshop"),
    Recipe("make_powder", "make propellant",
           (("charcoal", 0.16), ("salt", 0.75)), (("powder", 1.0),),
           55, "crafting", "workshop"),
)}


def nutrition_of(key: str, kg: float, freshness: float = 1.0
                 ) -> tuple[float, float, float]:
    """Calories, protein grams and vitamin C milligrams in a mass of food.

    Freshness costs vitamins fastest, calories barely at all -- which is why a
    colony can be perfectly well fed on old stores and still get scurvy.
    """
    it = ITEMS[key]
    kcal = it.kcal_kg * kg * (0.85 + 0.15 * freshness)
    protein = it.protein_g_kg * kg * (0.7 + 0.3 * freshness)
    vit_c = it.vit_c_mg_kg * kg * (freshness ** 2)
    return kcal, protein, vit_c


def bmr_kcal(mass_kg: float, height_cm: float, age_y: float,
             male: bool) -> float:
    """Basal metabolic rate, Mifflin-St Jeor. Kilocalories per day at rest."""
    b = 10.0 * mass_kg + 6.25 * height_cm - 5.0 * age_y
    return b + (5.0 if male else -161.0)


def thermal_kcal(temp_c: float, insulation_clo: float, mass_kg: float) -> float:
    """Extra daily calories burned staying warm.

    Below the lower critical temperature the body burns fuel to hold 37 C.
    This is why a colony's winter food requirement is not its summer
    requirement, and why clothing is a food-supply decision.
    """
    comfort = 26.0 - 6.5 * max(0.0, insulation_clo)
    if temp_c >= comfort:
        return 0.0
    deficit = comfort - temp_c
    # Roughly 12 kcal per degree per day for a 70 kg adult.
    return deficit * 12.0 * (mass_kg / 70.0) ** 0.75


def water_l_per_day(temp_c: float, work_fraction: float) -> float:
    """Litres of water needed per day."""
    base = 2.4 + 1.9 * work_fraction
    if temp_c > 24.0:
        base += (temp_c - 24.0) * 0.14
    return base


def wind_chill_c(temp_c: float, wind_ms: float) -> float:
    """Apparent temperature in wind. Standard North American formula."""
    if temp_c > 10.0 or wind_ms < 1.3:
        return temp_c
    v = (wind_ms * 3.6) ** 0.16
    return 13.12 + 0.6215 * temp_c - 11.37 * v + 0.3965 * temp_c * v


def heat_index_c(temp_c: float, humidity: float) -> float:
    """Apparent temperature in humid heat. Above about 35 C wet-bulb a human
    cannot shed metabolic heat at all, however much water they drink."""
    if temp_c < 26.0:
        return temp_c
    t = temp_c
    h = humidity * 100.0
    hi = (-8.78469476 + 1.61139411 * t + 2.338548839 * h
          - 0.14611605 * t * h - 0.012308094 * t * t
          - 0.016424828 * h * h + 0.002211732 * t * t * h
          + 0.00072546 * t * h * h - 0.000003582 * t * t * h * h)
    return max(temp_c, hi)
