"""Illness: the immunity race, and the two diseases that actually killed people.

Wounds already had an infection model. What the colony lacked was the thing
that killed far more settlers than any wound did — water and winter.

Both diseases here are chosen because they are *consequences of decisions the
player is already making*, rather than random misfortune bolted on:

**Enteric infection** comes from drinking surface water that has not been
boiled. The colony already stores litres and already burns megajoules; boiling
is the trade between the two, and a colony short of fuel that drinks from the
stream anyway is making a real historical mistake with a real historical
result.

**Respiratory infection** comes from cold, crowding and exhaustion. The colony
already tracks beds against population and indoor temperature. Fourteen people
in a two-bed cabin through a January is how influenza went through a
settlement, and the fix is the fix that worked: build more shelter.

The course of an illness is a race, which is both how it feels and roughly
what is happening: severity climbs on its own, immunity climbs at a rate set
by how well fed, rested and cared-for the patient is. Whichever reaches one
first decides it. Bed rest and a competent doctor do not cure anyone — they
shift the rate, which is exactly what nursing did before antibiotics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import rng


@dataclass(frozen=True)
class DiseaseDef:
    key: str
    name: str
    #: Severity gained per day with no treatment at all.
    severity_per_day: float
    #: Immunity gained per day by a healthy, fed, rested adult.
    immunity_per_day: float
    #: Days between catching it and showing symptoms.
    incubation_days: float
    #: How readily it passes to someone sharing quarters, per day.
    contagion: float
    #: Which capacities it drags down while it runs.
    hits: tuple[tuple[str, float], ...]
    #: Extra water lost per day, litres. Dysentery kills by dehydration.
    fluid_l_day: float = 0.0
    #: Days of acquired immunity after recovering. Without this an outbreak
    #: never ends: the first people to recover are immediately reinfected by
    #: the last, and the colony dies in a loop that has no real-world
    #: counterpart. Influenza gives most of a year against that strain.
    immune_days: float = 300.0


DISEASES: dict[str, DiseaseDef] = {d.key: d for d in (
    DiseaseDef(
        "enteric", "enteric infection",
        severity_per_day=0.30, immunity_per_day=0.34,
        incubation_days=2.0, contagion=0.05,
        hits=(("digestion", 0.45), ("consciousness", 0.15)),
        fluid_l_day=3.2, immune_days=210.0),
    DiseaseDef(
        "influenza", "influenza",
        severity_per_day=0.24, immunity_per_day=0.30,
        incubation_days=1.5, contagion=0.16,
        hits=(("breathing", 0.35), ("consciousness", 0.25))),
)}


@dataclass
class Illness:
    """One infection, running its course."""

    key: str
    severity: float = 0.0
    immunity: float = 0.0
    incubating_days: float = 0.0
    #: Quality of nursing currently applied, 0-1.
    tended: float = 0.0

    @property
    def d(self) -> DiseaseDef:
        return DISEASES[self.key]

    @property
    def showing(self) -> bool:
        return self.incubating_days <= 0.0

    @property
    def severe(self) -> bool:
        return self.showing and self.severity > 0.55

    def tick(self, days: float, *, fed: float, rested: float,
             warm: float) -> None:
        """Advance the race.

        Immunity is the interesting side. It is not a fixed rate: it is what
        the body can spare, and a starving, exhausted, freezing person has
        very little to spare. That is why the same illness passes through a
        well-provisioned colony in a week and empties a badly provisioned one.
        """
        if self.incubating_days > 0:
            self.incubating_days -= days
            return
        d = self.d
        self.severity += d.severity_per_day * days * (1.0 - 0.45 * self.tended)
        rate = d.immunity_per_day * (0.35 + 0.35 * fed + 0.20 * rested
                                     + 0.10 * warm)
        rate *= 1.0 + 0.55 * self.tended
        self.immunity += rate * days


def infection_pressure(*, drinking_raw: bool, crowding: float,
                       cold: bool, filth: float) -> dict[str, float]:
    """Daily chance of each disease appearing in the colony at all.

    Deliberately a function of the colony's own state and nothing else. There
    is no event deck: if nobody is drinking untreated water and everybody has
    a bed, nobody gets ill, and that is a thing the player can achieve.
    """
    out = {}
    if drinking_raw:
        out["enteric"] = 0.055 + 0.10 * filth
    if crowding > 1.0:
        # Crowding is people per bed. Two to a bed is a bad winter; seven to a
        # bed is how a settlement loses a third of itself.
        p = 0.012 * (crowding - 1.0) ** 1.5
        out["influenza"] = p * (2.2 if cold else 1.0)
    return out


def treatment_quality(skill: float, has_medicine: bool, has_herbs: bool,
                      in_bed: bool) -> float:
    """What nursing is worth, 0-1.

    A bed and someone to bring water is most of it. That is not a game
    concession -- before antibiotics, nursing *was* the treatment, and the
    difference between good and absent nursing in an enteric outbreak is most
    of the difference between recovering and not.
    """
    q = 0.12 + 0.55 * skill
    if in_bed:
        q += 0.25
    if has_medicine:
        q = min(1.0, q + 0.30)
    elif has_herbs:
        q = min(1.0, q + 0.10)
    return max(0.0, min(1.0, q))
