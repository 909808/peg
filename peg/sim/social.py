"""Who likes whom, who is married to whom, and what it costs when they die.

The colony simulation was, until this file, a model of fourteen metabolisms
that happened to share a granary. People worked, ate, froze and bled entirely
independently of one another. That is a defensible way to model calories and a
poor way to model a settlement, because the thing that actually decides
whether a small isolated group holds together is not its food supply.

So: opinions, which move slowly and for legible reasons; couples, which form
out of high opinion and change what each of them can bear; children, who cost
a great deal for fifteen years and then become the colony's future; and grief,
which is the mechanism that makes a death matter to anyone other than the
person who died.

Everything here is deliberately cheap. Opinions live in a dict keyed by pawn
name, interactions are resolved a few times a day rather than continuously,
and none of it touches the tick loop's inner arithmetic. The point is not a
relationship simulator -- it is that when a raid kills someone, the colony
should feel it for a season.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import rng

#: Opinion runs -100 (loathing) to +100 (devotion). Zero is a stranger.
OPINION_MIN = -100.0
OPINION_MAX = 100.0

#: Traits that make someone easier or harder to get on with. These are the
#: same traits that drive work and combat -- there is no separate "social"
#: personality, because people do not have one.
LIKEABLE = {"steady": 6.0, "hard_worker": 4.0, "quick": 2.0,
            "green_thumb": 2.0, "tough": 3.0}
ABRASIVE = {"anxious": -5.0, "slow": -3.0, "squeamish": -2.0}

#: How far apart two people can be and still count as working together.
TOGETHER_M = 12.0


@dataclass
class Bond:
    """One person's view of another. Asymmetric on purpose.

    Unrequited regard is a real thing and it is one of the few social facts
    that is genuinely interesting in a small group, so opinion is stored per
    direction rather than as a shared number.
    """

    opinion: float = 0.0
    #: Days spent working within sight of each other. Familiarity, roughly.
    shared_days: float = 0.0
    kind: str = ""            # "", "partner", "parent", "child", "sibling"

    @property
    def close(self) -> bool:
        return self.kind in ("partner", "parent", "child", "sibling")


@dataclass
class Society:
    """The colony's relationships, kept beside it rather than inside it.

    Keyed by name because that is what the rest of the simulation and the
    interface both use, and because a pawn object may be replaced (death,
    migration) while the memory of them should not be.
    """

    bonds: dict[str, dict[str, Bond]] = field(default_factory=dict)
    #: Names of people who have died, and who is still grieving them.
    grief: dict[str, dict[str, float]] = field(default_factory=dict)

    def bond(self, a: str, b: str) -> Bond:
        return self.bonds.setdefault(a, {}).setdefault(b, Bond())

    def opinion(self, a: str, b: str) -> float:
        return self.bonds.get(a, {}).get(b, _NEUTRAL).opinion

    def partner_of(self, name: str) -> str | None:
        for other, bd in self.bonds.get(name, {}).items():
            if bd.kind == "partner":
                return other
        return None

    def relatives(self, name: str) -> list[tuple[str, str]]:
        return [(o, b.kind) for o, b in self.bonds.get(name, {}).items()
                if b.close]

    def describe(self, name: str) -> list[str]:
        """Human-readable relationships, strongest first, for the interface."""
        out = []
        for other, b in sorted(self.bonds.get(name, {}).items(),
                               key=lambda kv: -abs(kv[1].opinion)):
            if b.kind:
                out.append(f"{b.kind} of {other} ({b.opinion:+.0f})")
            elif b.opinion >= 45:
                out.append(f"close friend of {other} ({b.opinion:+.0f})")
            elif b.opinion >= 18:
                out.append(f"friendly with {other} ({b.opinion:+.0f})")
            elif b.opinion <= -35:
                out.append(f"cannot stand {other} ({b.opinion:+.0f})")
            elif b.opinion <= -15:
                out.append(f"dislikes {other} ({b.opinion:+.0f})")
        return out[:6]

    # ---- the day ---------------------------------------------------------

    def tick(self, pawns: list, minutes: float, r: rng.Rng) -> list[str]:
        """Advance relationships. Returns notable events.

        Called once a colony tick with everyone who is alive. The arithmetic
        is proportional to the square of the population, which for a colony of
        fourteen is ninety-one pairs and entirely fine; it would not be for a
        city, and a city is not what this is.
        """
        if len(pawns) < 2:
            return []
        days = minutes / 1440.0
        ev: list[str] = []

        for i, a in enumerate(pawns):
            for b in pawns[i + 1:]:
                near = (abs(a.x - b.x) + abs(a.y - b.y)) <= TOGETHER_M
                if not near:
                    continue
                ab, ba = self.bond(a.name, b.name), self.bond(b.name, a.name)
                ab.shared_days += days
                ba.shared_days += days
                # Compatibility is a fixed property of the pair, so two people
                # who grate on each other go on grating however long they are
                # thrown together -- which is the interesting case.
                fit = self._fit(a, b)
                # Being cold, hungry and in pain makes anyone worse company.
                strain = 1.0 - 0.5 * min(1.0, (1.0 - a.morale) + (1.0 - b.morale))
                drift = fit * strain * days * 3.0
                ab.opinion = _clamp(ab.opinion + drift)
                ba.opinion = _clamp(ba.opinion + drift)

        ev += self._pair_up(pawns, days, r)
        ev += self._tick_grief(pawns, days)
        self._apply_morale(pawns)
        return ev

    def _fit(self, a, b) -> float:
        """How well two people get on, per day of proximity.

        Trait-driven and mildly random per pair, so the same two colonists
        always drift the same way and the player can learn it.
        """
        s = 0.0
        for t in a.traits:
            s += ABRASIVE.get(t, 0.0) * 0.5
        for t in b.traits:
            s += LIKEABLE.get(t, 0.0) * 0.5 + ABRASIVE.get(t, 0.0) * 0.5
        for t in a.traits:
            s += LIKEABLE.get(t, 0.0) * 0.5
        # A stable per-pair offset: some people simply do not take to each
        # other, and no amount of shared work fixes it.
        key = rng.mix(hash(a.name) & 0xffff, hash(b.name) & 0xffff, 0x50C1A1)
        s += ((key % 1000) / 1000.0 - 0.42) * 9.0
        return s * 0.1

    def _pair_up(self, pawns: list, days: float, r: rng.Rng) -> list[str]:
        """Couples form out of long, high regard. Nothing else.

        No compatibility scoring beyond opinion, no preference model: this is
        a game about a settlement, and the only thing it needs from pairing is
        that some people have someone whose death would break them.
        """
        ev = []
        for i, a in enumerate(pawns):
            if self.partner_of(a.name):
                continue
            for b in pawns[i + 1:]:
                if self.partner_of(b.name) or a.age < 18 or b.age < 18:
                    continue
                ab, ba = self.bond(a.name, b.name), self.bond(b.name, a.name)
                if ab.close or ab.opinion < 62 or ba.opinion < 62:
                    continue
                if ab.shared_days < 90:
                    continue
                if r.random() > days * 0.05:
                    continue
                ab.kind = ba.kind = "partner"
                ev.append(f"{a.name} and {b.name} became partners")
                break
        return ev

    def record_birth(self, child: str, parents: tuple[str, str]) -> None:
        for p in parents:
            self.bond(p, child).kind = "parent"
            self.bond(p, child).opinion = 85.0
            self.bond(child, p).kind = "child"
            self.bond(child, p).opinion = 85.0
        # Siblings.
        for p in parents:
            for other, b in list(self.bonds.get(p, {}).items()):
                if b.kind == "parent" and other != child:
                    self.bond(child, other).kind = "sibling"
                    self.bond(other, child).kind = "sibling"

    def record_death(self, name: str, survivors: list) -> list[str]:
        """Everyone who cared about the dead starts grieving.

        Grief is proportional to what the bond was worth, decays over months,
        and is subtracted straight from morale -- which in turn slows work,
        which is how one death in November becomes a bad winter.
        """
        ev = []
        mourners: dict[str, float] = {}
        for q in survivors:
            b = self.bonds.get(q.name, {}).get(name)
            if b is None:
                continue
            weight = max(b.opinion, 0.0) / 100.0
            if b.kind == "partner":
                weight = max(weight, 1.0)
            elif b.close:
                weight = max(weight, 0.75)
            if weight < 0.12:
                continue
            mourners[q.name] = weight
            if weight >= 0.75:
                ev.append(f"{q.name} is devastated by {name}'s death")
        if mourners:
            self.grief[name] = mourners
        # The bond itself is kept: the colony remembers its dead, and a
        # surviving partner should still read as widowed.
        return ev

    def _tick_grief(self, pawns: list, days: float) -> list[str]:
        for dead in list(self.grief):
            m = self.grief[dead]
            for who in list(m):
                # About four months from devastation back to level, which is
                # slow enough that a bad raid shows up in the harvest.
                m[who] -= days / 115.0
                if m[who] <= 0:
                    del m[who]
            if not m:
                del self.grief[dead]
        return []

    def grief_of(self, name: str) -> float:
        return sum(m.get(name, 0.0) for m in self.grief.values())

    def _apply_morale(self, pawns: list) -> None:
        """Push the social state into each pawn's morale floor.

        Kept as a single scalar on the pawn so that nothing else in the
        simulation has to know this module exists.
        """
        for p in pawns:
            mates = self.bonds.get(p.name, {})
            best = max((b.opinion for b in mates.values()), default=0.0)
            worst = min((b.opinion for b in mates.values()), default=0.0)
            partner = self.partner_of(p.name)
            social = 0.0
            if partner:
                social += 0.10
            social += max(0.0, best) / 100.0 * 0.08
            social += min(0.0, worst) / 100.0 * 0.06
            social -= min(1.0, self.grief_of(p.name)) * 0.32
            p.social_morale = social


_NEUTRAL = Bond()


def _clamp(v: float) -> float:
    return OPINION_MIN if v < OPINION_MIN else (
        OPINION_MAX if v > OPINION_MAX else v)
