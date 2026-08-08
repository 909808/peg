"""People: bodies, needs, injuries, skills.

A pawn is a body with a metabolism and a set of capacities. Everything that
happens to them flows through those capacities rather than through a hit-point
bar: a shattered femur does not remove 30 health, it removes mobility, and
mobility is what makes you slow to reach cover, which is what gets you shot
again.

The failure modes are the real ones. People die of blood loss in minutes, of
sepsis in days, of hypothermia in hours, of starvation in weeks and of scurvy
in months. Each has its own clock, and a colony can be losing on one of them
while comfortably winning on the others -- which is the situation that makes
the medical decisions interesting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import rng
from . import disease, items

# --------------------------------------------------------------------------
# anatomy
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PartDef:
    key: str
    name: str
    #: Share of the body's frontal area, used to distribute incoming fire.
    hit_share: float
    max_hp: float
    vital: bool
    #: Capacities this part contributes to, and by how much.
    drives: tuple[tuple[str, float], ...]


BODY: tuple[PartDef, ...] = (
    PartDef("head", "head", 0.09, 22, True,
            (("consciousness", 0.45), ("sight", 1.0))),
    PartDef("torso", "torso", 0.36, 45, True,
            (("breathing", 0.6), ("blood_pump", 0.6))),
    PartDef("heart", "heart", 0.02, 12, True, (("blood_pump", 0.4),)),
    PartDef("lung_l", "left lung", 0.02, 14, False, (("breathing", 0.2),)),
    PartDef("lung_r", "right lung", 0.02, 14, False, (("breathing", 0.2),)),
    PartDef("arm_l", "left arm", 0.10, 28, False, (("manipulation", 0.5),)),
    PartDef("arm_r", "right arm", 0.10, 28, False, (("manipulation", 0.5),)),
    PartDef("leg_l", "left leg", 0.145, 32, False, (("mobility", 0.5),)),
    PartDef("leg_r", "right leg", 0.145, 32, False, (("mobility", 0.5),)),
)
BODY_BY_KEY = {p.key: p for p in BODY}

CAPACITIES = ("consciousness", "sight", "breathing", "blood_pump",
              "manipulation", "mobility")


@dataclass
class Part:
    d: PartDef
    hp: float
    destroyed: bool = False

    @property
    def fraction(self) -> float:
        return 0.0 if self.destroyed else max(0.0, self.hp / self.d.max_hp)


# --------------------------------------------------------------------------
# injuries
# --------------------------------------------------------------------------


@dataclass
class Injury:
    kind: str            # cut, gunshot, fracture, burn, bruise, frostbite
    part: str
    severity: float      # damage dealt, in the part's hp units
    #: Millilitres of blood lost per minute. Falls as it clots or is tended.
    bleed_ml_min: float = 0.0
    #: 0-1. Above 1 the infection has become sepsis.
    infection: float = 0.0
    #: Quality of treatment, 0 untreated to 1 surgical.
    tended: float = -1.0
    age_min: float = 0.0
    healed: float = 0.0

    @property
    def pain(self) -> float:
        base = self.severity / 30.0
        if self.kind == "fracture":
            base *= 1.8
        elif self.kind == "burn":
            base *= 1.5
        return base * max(0.15, 1.0 - self.healed)

    @property
    def open(self) -> bool:
        return self.kind in ("cut", "gunshot", "burn") and self.healed < 0.8


# --------------------------------------------------------------------------
# skills and traits
# --------------------------------------------------------------------------

SKILLS = ("farming", "construction", "cooking", "medicine", "shooting",
          "melee", "crafting", "mining", "mechanics", "social", "research")

#: Experience needed to reach each level. Learning is fast at first and then
#: grinds, which is why a colony wants specialists rather than generalists.
def xp_for_level(level: int) -> float:
    return 900.0 * (level ** 1.55)


@dataclass
class Skill:
    level: int = 0
    xp: float = 0.0
    #: Natural aptitude, 0.5 to 1.6. Some people are simply better at things.
    aptitude: float = 1.0

    def gain(self, amount: float) -> bool:
        """Add experience. Returns True on level up."""
        if self.level >= 20:
            return False
        self.xp += amount * self.aptitude
        up = False
        while self.level < 20 and self.xp >= xp_for_level(self.level + 1):
            self.level += 1
            up = True
        return up


@dataclass(frozen=True)
class Trait:
    key: str
    name: str
    desc: str
    #: Multipliers applied to named quantities.
    mods: tuple[tuple[str, float], ...] = ()


TRAITS: dict[str, Trait] = {t.key: t for t in (
    Trait("hard_worker", "hard worker", "works faster, tires sooner",
          (("work_speed", 1.22), ("rest_rate", 1.15))),
    Trait("slow", "slow", "deliberate and unhurried", (("work_speed", 0.82),)),
    Trait("strong", "strong", "carries more, hits harder",
          (("carry", 1.35), ("melee", 1.25))),
    Trait("frail", "frail", "injures easily", (("toughness", 0.75),)),
    Trait("tough", "tough", "shrugs off damage", (("toughness", 1.35),)),
    Trait("night_owl", "night owl", "works better after dark",
          (("night_work", 1.25),)),
    Trait("anxious", "anxious", "morale suffers under pressure",
          (("morale_floor", 0.8),)),
    Trait("steady", "steady", "keeps their head when shot at",
          (("suppression_resist", 1.6), ("morale_floor", 1.15))),
    Trait("asthmatic", "asthmatic", "poor stamina, worse in cold or smoke",
          (("breathing", 0.8),)),
    Trait("quick", "quick learner", "gains skill faster", (("learning", 1.4),)),
    Trait("green_thumb", "green thumb", "plants thrive under their care",
          (("farming", 1.3),)),
    Trait("cold_blooded", "cold-adapted", "tolerates cold well",
          (("cold_tolerance", 1.4),)),
    Trait("heat_adapted", "heat-adapted", "tolerates heat well",
          (("heat_tolerance", 1.4),)),
    Trait("squeamish", "squeamish", "cannot perform surgery",
          (("medicine", 0.5),)),
)}


FIRST_NAMES = (
    "Adaeze", "Ama", "Anouk", "Beatriz", "Chen", "Dalia", "Ekaterina", "Elif",
    "Fatima", "Gita", "Hana", "Ines", "Jarrah", "Kaia", "Lena", "Mira",
    "Noor", "Oksana", "Priya", "Rania", "Sena", "Tamsin", "Uma", "Vera",
    "Wren", "Yara", "Zofia", "Ade", "Bashir", "Caleb", "Dmitri", "Eitan",
    "Faisal", "Gunnar", "Hiro", "Ivan", "Joaquin", "Kwame", "Lars", "Mateo",
    "Nils", "Omar", "Pavel", "Quan", "Rafael", "Soren", "Tomas", "Ugo",
    "Viktor", "Wei", "Xu", "Yusuf", "Zane",
)
SURNAMES = (
    "Abara", "Almeida", "Beaumont", "Castellan", "Dvorak", "Eriksen",
    "Fontaine", "Garang", "Haddad", "Ibarra", "Jansen", "Kovac", "Lindqvist",
    "Moreau", "Nakamura", "Okonkwo", "Petrov", "Quintero", "Ramos",
    "Sandoval", "Tanaka", "Ustinov", "Varga", "Whitlock", "Xiong", "Yilmaz",
    "Zielinski", "Aturi", "Brennan", "Cardoso", "Delacroix", "Espinoza",
)


# --------------------------------------------------------------------------
# the pawn
# --------------------------------------------------------------------------

BLOOD_ML = 5000.0

#: Calorie debt at which the body starts drawing on fat reserves. Below this a
#: pawn is merely hungry and will go and eat; above it, they could not.
FAT_MOBILISE_KCAL = 2600.0

#: Skin temperature. Above this the body cannot shed heat by conduction and
#: must sweat instead. Unlike the cold threshold this barely depends on
#: clothing, because people take clothing off.
HEAT_NEUTRAL_C = 33.0


@dataclass
class Pawn:
    name: str
    age: float
    male: bool
    mass_kg: float
    height_cm: float

    parts: dict[str, Part] = field(default_factory=dict)
    injuries: list[Injury] = field(default_factory=list)
    illnesses: list[disease.Illness] = field(default_factory=list)
    #: Days of acquired immunity remaining, by disease key.
    immunities: dict[str, float] = field(default_factory=dict)
    skills: dict[str, Skill] = field(default_factory=dict)
    traits: list[str] = field(default_factory=list)

    blood_ml: float = BLOOD_ML
    core_temp_c: float = 37.0

    # ---- needs ----
    #: Cumulative calorie deficit. Body fat covers roughly 7700 kcal per kg.
    energy_debt_kcal: float = 0.0
    fat_kg: float = 12.0
    water_debt_l: float = 0.0
    sleep_debt_h: float = 0.0
    #: Days of accumulated vitamin C shortfall.
    vit_c_debt_days: float = 0.0
    protein_debt_g: float = 0.0
    morale: float = 0.7
    #: Net morale contribution from relationships: a partner, friends, enemies
    #: and grief, resolved in :mod:`peg.sim.social` and pushed here as one
    #: number so nothing else in the simulation has to know that module exists.
    social_morale: float = 0.0
    #: Days left of a pregnancy, and by whom. A colony that cannot replace its
    #: dead is on a countdown no amount of good farming fixes.
    pregnant_days: float = 0.0
    pregnant_by: str = ""
    #: What this person will be when grown. Stored so a child grows towards
    #: their own adult size rather than a population average.
    adult_height_cm: float = 170.0
    adult_mass_kg: float = 66.0
    #: Minutes until this person will sit down to another meal. People eat a
    #: few times a day, not continuously; without a refractory period a hungry
    #: colonist retries every tick and, because eating pre-empts work, a
    #: short-of-food colony spends every waking minute chewing scraps instead
    #: of growing, cutting or cooking anything. That is a livelock, and it is
    #: what kept a six-person settlement pinned at zero stores for months.
    meal_cooldown_min: float = 0.0

    # ---- state ----
    x: int = 0
    y: int = 0
    #: Where they are walking to, and what for. Work is costed in
    #: person-minutes regardless, but people who never move look like a pile
    #: of tokens rather than a settlement, and you cannot see what the colony
    #: is doing.
    target_x: int = -1
    target_y: int = -1
    target_job: str = ""
    #: Fractional metres of movement carried between ticks.
    move_credit: float = 0.0
    #: Minutes to keep working this spot before looking for another.
    dwell_min: float = 0.0
    asleep: bool = False
    dead: bool = False
    cause_of_death: str = ""
    #: Insulation currently worn, in clo. 1.0 is a business suit, 4 is arctic.
    clothing_clo: float = 1.2
    #: Suppression from incoming fire, 0-1. Decays over seconds.
    suppression: float = 0.0
    job: str = ""

    def __post_init__(self) -> None:
        if not self.parts:
            self.parts = {p.key: Part(p, p.max_hp) for p in BODY}
        for s in SKILLS:
            self.skills.setdefault(s, Skill())

    # ---- construction ----

    @staticmethod
    def random(seed: int, age_range: tuple[float, float] = (18, 55)) -> "Pawn":
        r = rng.Rng(seed)
        male = r.chance(0.5)
        age = r.uniform(*age_range)
        height = r.clamped_gauss(176 if male else 163, 7.5, 145, 200)
        bmi = r.clamped_gauss(23.0, 2.6, 17.0, 32.0)
        # Children are not small adults, and a newborn built at 176 cm and
        # 71 kg eats like one -- which would quietly double a colony's food
        # bill the moment anyone was born. Height follows roughly the real
        # growth curve to adult stature at eighteen.
        adult_h = height
        adult_m = bmi * (height / 100.0) ** 2
        if age < 18.0:
            height *= 0.28 + 0.72 * (age / 18.0) ** 0.62
            bmi *= 0.72 + 0.28 * (age / 18.0)
        mass = bmi * (height / 100.0) ** 2
        p = Pawn(
            name=f"{r.choice(FIRST_NAMES)} {r.choice(SURNAMES)}",
            age=age, male=male, mass_kg=mass, height_cm=height,
            fat_kg=max(4.0, mass * r.uniform(0.10, 0.24)),
            adult_height_cm=adult_h, adult_mass_kg=adult_m,
        )
        for s in SKILLS:
            sk = p.skills[s]
            sk.aptitude = r.clamped_gauss(1.0, 0.22, 0.5, 1.6)
            # Everyone arrives having done something with their life -- unless
            # they have not had one yet.
            lvl = max(0, int(r.clamped_gauss(2.5, 2.4, 0, 9)))
            if age < 18.0:
                lvl = int(lvl * max(0.0, (age - 5.0) / 13.0))
            sk.level = lvl
            sk.xp = xp_for_level(lvl)
        # One or two traits, never contradictory.
        pool = list(TRAITS)
        for _ in range(r.randint(1, 2)):
            t = r.choice(pool)
            if t not in p.traits:
                p.traits.append(t)
        p.morale = r.uniform(0.55, 0.85)
        return p

    # ---- traits ----

    def mod(self, key: str) -> float:
        m = 1.0
        for t in self.traits:
            for k, v in TRAITS[t].mods:
                if k == key:
                    m *= v
        return m

    # ---- capacities ----

    def capacity(self, name: str) -> float:
        """0-1. How much of a faculty the pawn still has."""
        total = 0.0
        weight = 0.0
        for part in self.parts.values():
            for cap, w in part.d.drives:
                if cap == name:
                    total += part.fraction * w
                    weight += w
        base = (total / weight) if weight > 0 else 1.0

        if name == "consciousness":
            base *= self._blood_factor()
            base *= max(0.0, 1.0 - self.pain * 0.55)
            if self.core_temp_c < 33.0:
                base *= max(0.05, 1.0 - (33.0 - self.core_temp_c) / 6.0)
            if self.severe_dehydration:
                base *= 0.55
            if self.asleep:
                base *= 0.1
        elif name == "breathing":
            base *= self.mod("breathing")
            base *= self._blood_factor()
        elif name == "mobility":
            base *= max(0.1, self.capacity_raw("consciousness"))
            base *= max(0.3, 1.0 - self.pain * 0.4)
        elif name == "manipulation":
            base *= max(0.1, self.capacity_raw("consciousness"))

        # Illness drags on whatever it attacks, in proportion to how far it
        # has run. A colonist with influenza is not incapacitated, they are
        # slow and short of breath -- which is enough to lose a harvest.
        for ill in self.illnesses:
            if not ill.showing:
                continue
            for cap, w in ill.d.hits:
                if cap == name:
                    base *= max(0.05, 1.0 - w * min(1.0, ill.severity))

        return rng.clamp01(base)

    def capacity_raw(self, name: str) -> float:
        """Capacity from body parts alone, without the recursive modifiers.
        Used internally to avoid infinite regress."""
        total = 0.0
        weight = 0.0
        for part in self.parts.values():
            for cap, w in part.d.drives:
                if cap == name:
                    total += part.fraction * w
                    weight += w
        base = (total / weight) if weight > 0 else 1.0
        if name == "consciousness":
            base *= self._blood_factor()
            base *= max(0.0, 1.0 - self.pain * 0.55)
        return rng.clamp01(base)

    def _blood_factor(self) -> float:
        """Circulatory sufficiency. Losing 30% of blood volume is survivable
        but incapacitating; 40% is usually fatal."""
        f = self.blood_ml / BLOOD_ML
        if f > 0.85:
            return 1.0
        return rng.clamp01((f - 0.55) / 0.30)

    @property
    def pain(self) -> float:
        p = sum(i.pain for i in self.injuries)
        if self.energy_debt_kcal > 40000:
            p += 0.15
        return min(1.6, p / max(0.4, self.mod("toughness")))

    @property
    def alive(self) -> bool:
        return not self.dead

    @property
    def incapacitated(self) -> bool:
        return self.capacity("consciousness") < 0.28 or self.dead

    @property
    def work_speed(self) -> float:
        """Multiplier on how fast work gets done."""
        if self.incapacitated or self.asleep:
            return 0.0
        s = self.capacity("manipulation") * 0.6 + self.capacity("consciousness") * 0.4
        s *= self.mod("work_speed")
        s *= 0.55 + 0.45 * self.morale
        if self.sleep_debt_h > 8:
            s *= max(0.35, 1.0 - (self.sleep_debt_h - 8) / 24.0)
        if self.energy_debt_kcal > 20000:
            s *= 0.75
        # Children work, because on a subsistence holding children always
        # worked -- but a six-year-old minding hens is not a field hand, and a
        # colony counting them as one would starve on paper it thought was
        # sound. Nothing under five is any use at all.
        if self.age < 18.0:
            s *= max(0.0, min(1.0, (self.age - 4.0) / 13.0))
        # And the very old slow down.
        elif self.age > 60.0:
            s *= max(0.35, 1.0 - (self.age - 60.0) * 0.022)
        return max(0.0, s)

    @property
    def child(self) -> bool:
        return self.age < 14.0

    @property
    def move_speed_ms(self) -> float:
        """Metres per second on flat clear ground. A fit adult walks 1.4."""
        if self.incapacitated:
            return 0.0
        return 1.45 * self.capacity("mobility") * (0.6 + 0.4 * self.capacity("consciousness"))

    @property
    def carry_kg(self) -> float:
        return 32.0 * self.mod("carry") * self.capacity("manipulation") \
            * (self.mass_kg / 70.0) ** 0.6

    @property
    def bleeding_ml_min(self) -> float:
        return sum(i.bleed_ml_min for i in self.injuries)

    @property
    def severe_dehydration(self) -> bool:
        return self.water_debt_l > 3.5

    @property
    def starving(self) -> bool:
        return self.fat_kg <= 3.0 and self.energy_debt_kcal > 0

    @property
    def scurvy(self) -> float:
        """0-1 severity of vitamin C deficiency."""
        if self.vit_c_debt_days < items.SCURVY_ONSET_DAYS:
            return 0.0
        return min(1.0, (self.vit_c_debt_days - items.SCURVY_ONSET_DAYS) / 45.0)

    def skill(self, key: str) -> int:
        return self.skills[key].level

    def skill_factor(self, key: str) -> float:
        """Work-rate multiplier from a skill. Level 5 is the baseline 1.0."""
        lvl = self.skills[key].level
        return (0.35 + 0.13 * lvl) * self.mod(key)

    def learn(self, key: str, minutes: float) -> None:
        self.skills[key].gain(minutes * 1.6 * self.mod("learning"))

    # ---- metabolism ----

    def daily_kcal_need(self, temp_c: float, work_fraction: float) -> float:
        base = items.bmr_kcal(self.mass_kg, self.height_cm, self.age, self.male)
        activity = 1.25 + 0.95 * work_fraction
        thermal = items.thermal_kcal(temp_c, self.clothing_clo, self.mass_kg)
        return base * activity + thermal

    def tick(self, minutes: float, temp_c: float, work_fraction: float,
             is_night: bool, rand: rng.Rng,
             humidity: float = 0.5) -> list[str]:
        """Advance the body by ``minutes``. Returns notable events."""
        if self.dead:
            return []
        ev: list[str] = []
        days = minutes / 1440.0
        self.meal_cooldown_min = max(0.0, self.meal_cooldown_min - minutes)

        # --- bleeding -----------------------------------------------------
        bleed = self.bleeding_ml_min
        if bleed > 0:
            self.blood_ml -= bleed * minutes
            if self.blood_ml <= BLOOD_ML * 0.35:
                self.dead = True
                self.cause_of_death = "blood loss"
                return [f"{self.name} bled to death"]
        elif self.blood_ml < BLOOD_ML:
            # Regeneration is slow: about 1% of volume per day.
            self.blood_ml = min(BLOOD_ML, self.blood_ml + BLOOD_ML * 0.01 * days)

        # --- energy -------------------------------------------------------
        # Debt accumulates continuously; eating clears it. Body fat is a
        # *reserve*, drawn on only once the debt has gone unfed for the better
        # part of a day -- otherwise a well-stocked colonist would quietly
        # metabolise themselves while standing next to a full granary, having
        # never been hungry enough to reach for it.
        need = self.daily_kcal_need(temp_c, work_fraction) * days
        self.energy_debt_kcal += need
        if self.energy_debt_kcal > FAT_MOBILISE_KCAL:
            excess = self.energy_debt_kcal - FAT_MOBILISE_KCAL
            # Adipose mobilises at roughly 70 kcal per kg of fat per day; you
            # cannot live off your reserves arbitrarily fast.
            cap = self.fat_kg * 70.0 * days
            drawn = min(excess, cap, self.fat_kg * 7700.0)
            burn_kg = drawn / 7700.0
            self.fat_kg -= burn_kg
            self.mass_kg = max(30.0, self.mass_kg - burn_kg)
            self.energy_debt_kcal -= drawn
            if self.fat_kg <= 0.5:
                # Out of fat: the body starts consuming muscle.
                lean = min(self.energy_debt_kcal, 4400.0 * days * 2.0) / 4400.0
                self.mass_kg -= lean
                self.energy_debt_kcal -= lean * 4400.0
                if self.mass_kg < self.height_cm * 0.22:
                    self.dead = True
                    self.cause_of_death = "starvation"
                    return [f"{self.name} starved to death"]

        # --- water --------------------------------------------------------
        self.water_debt_l += items.water_l_per_day(temp_c, work_fraction) * days
        if self.water_debt_l > 7.0:
            self.dead = True
            self.cause_of_death = "dehydration"
            return [f"{self.name} died of thirst"]

        # --- vitamin C ----------------------------------------------------
        self.vit_c_debt_days += days
        if self.scurvy > 0:
            # Scurvy reopens healed wounds; collagen stops holding.
            for inj in self.injuries:
                if inj.healed > 0.3 and rand.chance(0.02 * days * 1440 / 60):
                    inj.healed *= 0.6
                    inj.bleed_ml_min = max(inj.bleed_ml_min, 0.4)
            if self.scurvy >= 1.0 and rand.chance(0.02 * days):
                self.dead = True
                self.cause_of_death = "scurvy"
                return [f"{self.name} died of scurvy"]

        # --- sleep --------------------------------------------------------
        if self.asleep:
            self.sleep_debt_h = max(0.0, self.sleep_debt_h - minutes / 60.0 * 1.0)
        else:
            self.sleep_debt_h += minutes / 60.0 * (8.0 / 16.0) * self.mod("rest_rate")
        if self.sleep_debt_h > 40 and rand.chance(0.05 * days):
            ev.append(f"{self.name} is collapsing from exhaustion")

        # --- temperature --------------------------------------------------
        self._tick_thermal(minutes, temp_c, work_fraction, humidity)
        if self.core_temp_c < 28.0:
            self.dead = True
            self.cause_of_death = "hypothermia"
            return [f"{self.name} froze to death"]
        if self.core_temp_c > 42.0:
            self.dead = True
            self.cause_of_death = "heatstroke"
            return [f"{self.name} died of heatstroke"]

        # --- injuries and illness -----------------------------------------
        ev.extend(self._tick_injuries(minutes, rand))
        ev.extend(self._tick_illness(days))

        # --- morale -------------------------------------------------------
        target = 0.75
        if self.pain > 0.3:
            target -= self.pain * 0.3
        if self.starving:
            target -= 0.3
        if self.sleep_debt_h > 16:
            target -= 0.2
        if temp_c < 0 or temp_c > 33:
            target -= 0.12
        # Who you are with, and who you have lost. A colonist grieving a
        # partner works at about two thirds speed for a season, which is the
        # mechanism by which a raid in November costs you the spring sowing.
        target += self.social_morale
        target *= self.mod("morale_floor")
        self.morale += (target - self.morale) * min(1.0, days * 2.0)
        self.morale = rng.clamp01(self.morale)

        # Suppression bleeds off in seconds, not minutes.
        self.suppression = max(0.0, self.suppression - minutes * 0.9)
        return ev

    def _tick_thermal(self, minutes: float, ambient_c: float,
                      work_fraction: float, humidity: float = 0.5) -> None:
        """Core temperature drifts towards equilibrium with the environment.

        Thermoregulation is strongly asymmetric and modelling it symmetrically
        is wrong in a way that kills people: a colonist in an Iowa June came
        out at 41.9 C and died of heatstroke on a pleasant summer day.

        Cold side: the body can only generate so much heat, so a large deficit
        pulls the core down until it stops. Insulation and hard work are the
        two defences, which is what makes clothing and firewood strategic.

        Heat side: sweating holds the core almost exactly at 37 C across a
        very wide band, and then fails abruptly when the air is too humid to
        evaporate into. That cliff -- not the temperature itself -- is what
        makes humid heat lethal, and it is why 45 C in the Sahara is survivable
        and 40 C in a monsoon is not.
        """
        clo = self.clothing_clo * self.mod("cold_tolerance")
        metabolic = 1.4 + 3.2 * work_fraction
        cold_neutral = 27.0 - 6.5 * clo

        if ambient_c <= cold_neutral:
            drive = (ambient_c - cold_neutral) * 0.18 + metabolic * 0.55
        else:
            # Evaporative capacity, in degrees of excess heat the body can
            # shed. Collapses as the air saturates.
            capacity = 16.0 * (1.0 - 0.80 * rng.clamp01(humidity))
            capacity *= self.mod("heat_tolerance")
            capacity *= max(0.35, 1.0 - 0.25 * work_fraction)
            # Clothing you cannot take off also impedes evaporation.
            capacity *= max(0.5, 1.0 - 0.10 * max(0.0, clo - 1.0))
            excess = max(0.0, ambient_c - HEAT_NEUTRAL_C - capacity)
            drive = excess * 0.30 + metabolic * 0.12
            if self.water_debt_l > 2.0:
                # You cannot sweat what you have not drunk.
                drive += (self.water_debt_l - 2.0) * 0.6

        target = 37.0 + rng.clamp(drive, -16.0, 8.0)
        rate = min(1.0, minutes / 60.0 * 0.55)
        self.core_temp_c += (target - self.core_temp_c) * rate
        self.core_temp_c = rng.clamp(self.core_temp_c, 20.0, 45.0)

    def catch(self, key: str) -> bool:
        """Contract a disease, unless already carrying it or immune to it."""
        if any(i.key == key for i in self.illnesses):
            return False
        if self.immunities.get(key, 0.0) > 0.0:
            return False
        d = disease.DISEASES[key]
        self.illnesses.append(
            disease.Illness(key, incubating_days=d.incubation_days))
        return True

    @property
    def ill(self) -> bool:
        return any(i.showing for i in self.illnesses)

    def _tick_illness(self, days: float) -> list[str]:
        """Run the immunity race.

        Nutrition, rest and warmth all feed the immunity side, which is the
        whole reason this is worth simulating: illness is not an independent
        misfortune, it is the bill for a colony that is already behind.
        """
        for key in list(self.immunities):
            self.immunities[key] -= days
            if self.immunities[key] <= 0.0:
                del self.immunities[key]
        if not self.illnesses:
            return []
        ev: list[str] = []
        fed = 1.0 - min(1.0, self.energy_debt_kcal / 12000.0)
        rested = 1.0 - min(1.0, self.sleep_debt_h / 24.0)
        warm = 1.0 if self.core_temp_c >= 36.0 else max(
            0.0, 1.0 - (36.0 - self.core_temp_c) / 3.0)
        fed *= max(0.35, 1.0 - self.scurvy)

        for ill in list(self.illnesses):
            was_showing = ill.showing
            ill.tick(days, fed=fed, rested=rested, warm=warm)
            if ill.showing and not was_showing:
                ev.append(f"{self.name} has come down with {ill.d.name}")
            if not ill.showing:
                continue
            # Dysentery kills by dehydration, not by the infection itself.
            if ill.d.fluid_l_day:
                self.water_debt_l += ill.d.fluid_l_day * days * ill.severity
            if ill.immunity >= 1.0:
                self.illnesses.remove(ill)
                self.immunities[ill.key] = ill.d.immune_days
                ev.append(f"{self.name} has recovered from {ill.d.name}")
            elif ill.severity >= 1.0:
                self.dead = True
                self.cause_of_death = ill.d.name
                return [f"{self.name} died of {ill.d.name}"]
        return ev

    def _tick_injuries(self, minutes: float, rand: rng.Rng) -> list[str]:
        ev: list[str] = []
        days = minutes / 1440.0
        for inj in list(self.injuries):
            inj.age_min += minutes

            # Clotting: untended wounds slow but do not stop quickly.
            if inj.bleed_ml_min > 0:
                clot = 0.10 if inj.tended < 0 else 0.45 + inj.tended * 0.9
                inj.bleed_ml_min = max(0.0, inj.bleed_ml_min
                                       - clot * minutes / 60.0)

            # Infection. Untended open wounds are the danger; treatment is
            # what turns a survivable cut into a survivable cut.
            if inj.open:
                risk = 0.16 if inj.tended < 0 else 0.16 * (1.0 - inj.tended * 0.92)
                risk *= 1.0 + self.scurvy
                if self.energy_debt_kcal > 30000:
                    risk *= 1.3
                inj.infection += risk * days
                if inj.infection >= 1.0:
                    self.dead = True
                    self.cause_of_death = "sepsis"
                    return [f"{self.name} died of sepsis"]
                if 0.5 < inj.infection < 0.5 + risk * days:
                    ev.append(f"{self.name}'s {inj.part} wound is infected")

            # Healing.
            rate = 0.055 * days * 1440 / 24
            if inj.tended >= 0:
                rate *= 1.0 + inj.tended
            if self.energy_debt_kcal > 20000 or self.starving:
                rate *= 0.5
            rate *= max(0.25, 1.0 - self.scurvy)
            if inj.kind == "fracture":
                rate *= 0.35
            inj.healed = min(1.0, inj.healed + rate * days)

            part = self.parts.get(inj.part)
            if part and not part.destroyed:
                part.hp = min(part.d.max_hp, part.hp + inj.severity * rate * days)

            if inj.healed >= 1.0 and inj.bleed_ml_min <= 0 and inj.infection < 0.2:
                self.injuries.remove(inj)
        return ev

    # ---- damage ----

    def hurt(self, part_key: str, kind: str, severity: float,
             rand: rng.Rng) -> list[str]:
        """Apply an injury. Returns events."""
        if self.dead:
            return []
        part = self.parts.get(part_key)
        if part is None:
            part_key = "torso"
            part = self.parts["torso"]

        severity = severity / max(0.4, self.mod("toughness"))
        part.hp -= severity

        bleed = 0.0
        if kind == "gunshot":
            bleed = severity * 0.85
        elif kind == "cut":
            bleed = severity * 0.55
        elif kind == "fracture":
            bleed = severity * 0.12
        # Limbs bleed less than the torso; the torso has the big vessels.
        if part_key in ("arm_l", "arm_r", "leg_l", "leg_r"):
            bleed *= 0.7
        elif part_key in ("torso", "heart"):
            bleed *= 1.4

        self.injuries.append(Injury(kind=kind, part=part_key,
                                    severity=severity, bleed_ml_min=bleed))
        ev = []
        if part.hp <= 0:
            part.hp = 0.0
            if part.d.vital:
                self.dead = True
                self.cause_of_death = f"{kind} to the {part.d.name}"
                return [f"{self.name} was killed by a {kind} to the {part.d.name}"]
            part.destroyed = True
            ev.append(f"{self.name} lost the use of their {part.d.name}")
        return ev

    def tend(self, quality: float, rand: rng.Rng) -> int:
        """Treat every untended injury. Returns how many were treated.

        Quality comes from the doctor's skill and what medicine is available.
        Treatment mostly buys two things: bleeding stops sooner, and the
        infection clock slows by up to an order of magnitude.
        """
        n = 0
        for inj in self.injuries:
            if inj.tended < quality:
                inj.tended = quality
                inj.bleed_ml_min *= max(0.05, 1.0 - quality)
                n += 1
        return n

    def eat(self, store: items.Store, rand: rng.Rng) -> str | None:
        """Consume food to clear the calorie debt. Returns what was eaten."""
        if self.energy_debt_kcal < 400:
            return None
        key = store.best_food()
        if key is None:
            return None
        stack = store.stacks[key]
        # Take enough to clear the debt, up to a stomach's worth.
        want_kcal = min(self.energy_debt_kcal, 1400.0)
        kg = min(stack.amount, want_kcal / max(1.0, items.ITEMS[key].kcal_kg))
        kg = max(kg, min(stack.amount, 0.05))
        fresh = stack.freshness
        got = store.take(key, kg)
        if got <= 0:
            return None
        kcal, protein, vit_c = items.nutrition_of(key, got, fresh)
        self.energy_debt_kcal = max(0.0, self.energy_debt_kcal - kcal)
        if self.energy_debt_kcal <= 0 and self.fat_kg < self.mass_kg * 0.25:
            self.fat_kg += min(0.05, kcal / 7700.0 * 0.3)
        self.protein_debt_g = max(0.0, self.protein_debt_g - protein)
        if vit_c > items.RDA_VIT_C_MG * 0.5:
            self.vit_c_debt_days = max(0.0, self.vit_c_debt_days
                                       - vit_c / items.RDA_VIT_C_MG)

        # Calories alone will not keep you alive. Left to pick by energy
        # density a colonist eats grain and preserved meat every day, and
        # three months later has scurvy in the middle of a full granary. So
        # once the deficit is real, deliberately go and find something green.
        if self.vit_c_debt_days > 15.0:
            src = store.best_vitamin_c()
            if src is not None:
                sstack = store.stacks[src]
                sfresh = sstack.freshness
                want = min(sstack.amount, 0.30)
                got_c = store.take(src, want)
                if got_c > 0:
                    kc, pr, vc = items.nutrition_of(src, got_c, sfresh)
                    self.energy_debt_kcal = max(0.0, self.energy_debt_kcal - kc)
                    self.protein_debt_g = max(0.0, self.protein_debt_g - pr)
                    self.vit_c_debt_days = max(
                        0.0, self.vit_c_debt_days - vc / items.RDA_VIT_C_MG)
        return items.ITEMS[key].name

    def drink(self, litres_available: float) -> float:
        take = min(litres_available, self.water_debt_l)
        self.water_debt_l -= take
        return take

    # ---- description ----

    def status(self) -> str:
        bits = []
        if self.dead:
            return f"dead ({self.cause_of_death})"
        if self.incapacitated:
            bits.append("incapacitated")
        if self.bleeding_ml_min > 0:
            bits.append(f"bleeding {self.bleeding_ml_min:.0f} mL/min")
        inf = max((i.infection for i in self.injuries), default=0.0)
        if inf > 0.25:
            bits.append(f"infection {inf:.0%}")
        if self.starving:
            bits.append("starving")
        elif self.energy_debt_kcal > 8000:
            bits.append("hungry")
        if self.water_debt_l > 2.0:
            bits.append("dehydrated")
        if self.sleep_debt_h > 16:
            bits.append("exhausted")
        if self.core_temp_c < 35.0:
            bits.append(f"hypothermic {self.core_temp_c:.1f} C")
        elif self.core_temp_c > 39.0:
            bits.append(f"overheating {self.core_temp_c:.1f} C")
        if self.scurvy > 0:
            bits.append(f"scurvy {self.scurvy:.0%}")
        if self.pain > 0.25:
            bits.append(f"pain {self.pain:.0%}")
        return ", ".join(bits) if bits else "well"

    def best_skill(self) -> tuple[str, int]:
        k = max(SKILLS, key=lambda s: self.skills[s].level)
        return k, self.skills[k].level
