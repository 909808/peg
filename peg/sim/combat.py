"""Combat: exterior and terminal ballistics, cover, suppression.

Firefights resolve on one-second ticks, because that is the timescale on which
they are actually decided. Hitting is a geometry problem -- a cone of
dispersion projected to range against a target's silhouette -- rather than a
percentage attached to a weapon. That has consequences you can feel: a rifle
that groups 3 MOA covers 26 cm at 300 m, which is most of a torso, so the shot
is genuinely difficult and getting closer genuinely helps.

Nothing here is a hit point. A round that connects carries an energy, meets an
armour, and either penetrates or does not; if it does, it injures the body part
it struck. People are stopped by shock, blood loss and broken bones, which
means most casualties are wounded rather than killed, and a wounded colonist is
a labour problem for the next three months.

Suppression is modelled because it is what actually happens: rounds passing
close make people stop shooting and get down, and a squad that cannot shoot
back loses even if nobody has been hit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import rng
from ..local import terrain
from .pawn import BODY, Pawn

#: Air density at sea level, kg/m^3.
RHO0 = 1.225


@dataclass(frozen=True)
class Weapon:
    key: str
    name: str
    #: Projectile mass in grams and muzzle velocity in m/s.
    bullet_g: float
    muzzle_ms: float
    #: Ballistic coefficient (G1). Higher retains velocity better.
    bc: float
    #: Mechanical dispersion, minutes of angle. 1 MOA is ~2.9 cm at 100 m.
    moa: float
    #: Rounds per minute in practical use, including aiming.
    rpm: float
    #: Rounds before a reload, and how many seconds that takes.
    magazine: int
    reload_s: float
    #: Practical maximum engagement range in metres.
    max_range: float
    #: How much the weapon punishes an unskilled shooter.
    difficulty: float = 1.0
    #: Rounds fired per trigger pull.
    burst: int = 1
    melee: bool = False
    #: Item key consumed per shot.
    ammo: str = "ammo"

    @property
    def muzzle_energy_j(self) -> float:
        return 0.5 * (self.bullet_g / 1000.0) * self.muzzle_ms ** 2


WEAPONS: dict[str, Weapon] = {w.key: w for w in (
    # Improvised and archaic -- what a collapsed world actually fields.
    Weapon("club", "club", 0, 0, 0, 0, 40, 0, 0, 1.5, melee=True, ammo=""),
    Weapon("spear", "spear", 0, 0, 0, 0, 30, 0, 0, 2.0, melee=True, ammo=""),
    Weapon("machete", "machete", 0, 0, 0, 0, 55, 0, 0, 1.6, melee=True, ammo=""),
    Weapon("bow", "recurve bow", 25.0, 60.0, 0.08, 9.0, 10, 1, 0.0, 90,
           difficulty=1.6, ammo="ammo"),
    Weapon("crossbow", "crossbow", 30.0, 95.0, 0.09, 5.0, 3, 1, 0.0, 120,
           difficulty=1.2, ammo="ammo"),
    # Firearms.
    Weapon("pistol", "9 mm pistol", 8.0, 360.0, 0.14, 6.0, 60, 15, 2.5, 60,
           difficulty=1.4),
    Weapon("revolver", "revolver", 10.2, 340.0, 0.16, 5.0, 35, 6, 6.0, 70,
           difficulty=1.3),
    Weapon("shotgun", "12-gauge shotgun", 32.0, 400.0, 0.05, 25.0, 25, 5, 5.0, 45,
           difficulty=0.8),
    Weapon("smg", "9 mm carbine", 8.0, 420.0, 0.15, 8.0, 240, 30, 3.0, 120,
           difficulty=1.3, burst=3),
    Weapon("hunting_rifle", "bolt-action rifle", 9.7, 820.0, 0.40, 1.6, 14, 5, 5.5,
           600, difficulty=1.0),
    Weapon("battle_rifle", "battle rifle", 9.5, 840.0, 0.39, 2.4, 45, 20, 3.0,
           500, difficulty=1.1),
    Weapon("assault_rifle", "assault rifle", 4.0, 920.0, 0.30, 3.0, 90, 30, 3.0,
           400, difficulty=1.0, burst=3),
    Weapon("dmr", "designated marksman rifle", 11.3, 800.0, 0.50, 1.0, 20, 10, 4.0,
           800, difficulty=1.2),
    Weapon("machinegun", "light machine gun", 9.5, 830.0, 0.38, 5.0, 200, 100, 8.0,
           600, difficulty=1.4, burst=6),
)}


@dataclass(frozen=True)
class Armour:
    key: str
    name: str
    #: Rifle-equivalent protection level. Roughly NIJ: 0 none, 2 soft armour,
    #: 3 rifle plates, 4 hardened plates.
    level: float
    #: Kilograms; armour is heavy and heavy is slow.
    kg: float
    #: Fraction of the body it actually covers.
    coverage: float


ARMOURS: dict[str, Armour] = {a.key: a for a in (
    Armour("none", "clothing", 0.0, 0.0, 0.0),
    Armour("padded", "padded coat", 0.35, 3.0, 0.55),
    Armour("mail", "improvised mail", 0.8, 9.0, 0.6),
    Armour("soft", "soft vest", 1.6, 4.5, 0.45),
    Armour("plate", "plate carrier", 3.2, 11.0, 0.40),
)}


# --------------------------------------------------------------------------
# exterior ballistics
# --------------------------------------------------------------------------


def velocity_at(w: Weapon, range_m: float, altitude_m: float = 0.0) -> float:
    """Remaining velocity after drag.

    A flat-fire approximation of the G1 drag model: velocity decays roughly
    exponentially with range over a scale length set by the ballistic
    coefficient. Good enough to make the thing that matters true -- that a
    light high-velocity round sheds energy far faster than a heavy one, so it
    hits hard up close and poorly at distance.
    """
    if w.muzzle_ms <= 0:
        return 0.0
    rho = RHO0 * math.exp(-altitude_m / 8500.0)
    scale = max(20.0, w.bc * 1200.0 * (RHO0 / rho))
    return w.muzzle_ms * math.exp(-range_m / scale)


def energy_at(w: Weapon, range_m: float, altitude_m: float = 0.0) -> float:
    """Kinetic energy in joules at range."""
    v = velocity_at(w, range_m, altitude_m)
    return 0.5 * (w.bullet_g / 1000.0) * v * v


def time_of_flight(w: Weapon, range_m: float) -> float:
    if w.muzzle_ms <= 0:
        return 0.0
    v_avg = (w.muzzle_ms + velocity_at(w, range_m)) * 0.5
    return range_m / max(1.0, v_avg)


#: Target silhouettes in square metres: what you are actually trying to hit.
STANCE_AREA = {
    "standing": 0.62,
    "kneeling": 0.36,
    "prone": 0.14,
    "moving": 0.70,      # harder to lead, but presenting more
}


def hit_chance(w: Weapon, shooter: Pawn, range_m: float, stance: str,
               cover: float, target_moving: bool, light: float = 1.0,
               wind_ms: float = 0.0) -> float:
    """Probability that one aimed round strikes the target.

    Built from geometry rather than a lookup: the shooter's total dispersion
    (mechanical plus human) projects to a group size at range, and the chance
    of a hit is the target's exposed area against that group.
    """
    if w.melee or range_m > w.max_range * 3.0:
        return 0.0

    # Mechanical dispersion, converted from MOA to radians.
    disp = w.moa * 2.908e-4

    # The shooter's own contribution dominates at any realistic skill level.
    # Skill 0 adds about 14 MOA of wobble, skill 20 about 1.
    skill = shooter.skills["shooting"].level
    human_moa = (16.0 - 0.75 * skill) * w.difficulty
    human_moa /= max(0.25, shooter.capacity("manipulation"))
    human_moa /= max(0.25, shooter.capacity("sight"))
    if shooter.suppression > 0:
        human_moa *= 1.0 + shooter.suppression * 2.2
    if shooter.pain > 0.2:
        human_moa *= 1.0 + shooter.pain
    if shooter.sleep_debt_h > 16:
        human_moa *= 1.25
    disp += human_moa * 2.908e-4

    # Group radius at range, in metres.
    group_r = disp * range_m
    # Wind drift grows with time of flight.
    if wind_ms > 0 and not w.melee:
        group_r += wind_ms * time_of_flight(w, range_m) * 0.35

    area = STANCE_AREA["moving" if target_moving else stance]
    # Effective radius of an equivalent circular target.
    target_r = math.sqrt(area / math.pi)

    if group_r <= 1e-4:
        p = 1.0
    else:
        # Bivariate normal: probability of landing inside the target radius
        # when the group's standard deviation is group_r.
        p = 1.0 - math.exp(-(target_r ** 2) / (2.0 * group_r ** 2))

    # Past its practical range a weapon degrades rather than stopping: a
    # pistol can hit a man at 100 m, it simply usually will not. A hard cutoff
    # here produced the absurdity of a 0% chance one metre beyond the limit.
    if range_m > w.max_range:
        over = (range_m - w.max_range) / max(1.0, w.max_range)
        p *= math.exp(-over * 1.6)

    # Cover hides part of the silhouette outright.
    p *= max(0.0, 1.0 - cover * 0.85)
    # Poor light.
    p *= 0.35 + 0.65 * light
    # Nothing is certain. Even point-blank, targets flinch, shift and are
    # briefly obscured; sustained 100% hit rates are an artefact, not a fact.
    return rng.clamp01(min(p, 0.95))


# --------------------------------------------------------------------------
# terminal ballistics
# --------------------------------------------------------------------------


def pick_body_part(r: rng.Rng, aimed_low: bool = False) -> str:
    """Choose which part a hit lands on, weighted by frontal area."""
    parts = list(BODY)
    weights = [p.hit_share for p in parts]
    if aimed_low:
        # Suppressive and hurried fire strikes low.
        for i, p in enumerate(parts):
            if p.key in ("leg_l", "leg_r"):
                weights[i] *= 2.2
            elif p.key == "head":
                weights[i] *= 0.4
    total = sum(weights)
    roll = r.random() * total
    for p, wt in zip(parts, weights):
        roll -= wt
        if roll <= 0:
            return p.key
    return "torso"


def resolve_hit(w: Weapon, target: Pawn, armour: Armour, range_m: float,
                r: rng.Rng, altitude_m: float = 0.0,
                attacker: Pawn | None = None) -> list[str]:
    """Apply one connecting round or blow. Returns events."""
    part = pick_body_part(r)

    if w.melee:
        # Melee energy comes from the swinger, not a cartridge, so it scales
        # with their mass and strength rather than with a muzzle velocity.
        power = 18.0
        if attacker is not None:
            power *= (attacker.mass_kg / 70.0) ** 0.5 * attacker.mod("melee")
            power *= 0.5 + 0.5 * attacker.capacity("manipulation")
        if w.key == "spear":
            power *= 1.4
        severity = power * r.uniform(0.5, 1.4)
        if armour.level > 0 and r.random() < armour.coverage:
            severity *= max(0.15, 1.0 - armour.level * 0.28)
        kind = "cut" if w.key == "machete" else "fracture"
        return target.hurt(part, kind, severity, r)

    e = energy_at(w, range_m, altitude_m)

    # Armour: plates defeat rounds outright below their rating, and even a
    # defeated round transfers blunt trauma.
    if armour.level > 0 and r.random() < armour.coverage \
            and part in ("torso", "heart", "lung_l", "lung_r"):
        # Rough energy threshold each level will stop.
        stop_j = 350.0 * (armour.level ** 1.8)
        if e < stop_j:
            behind = e * 0.10
            if behind > 40:
                return target.hurt(part, "bruise", behind / 14.0, r)
            return []
        e -= stop_j * 0.7

    # Wound severity scales with the square root of energy: doubling energy
    # does not double the wound, it widens the channel.
    severity = 3.4 * math.sqrt(max(0.0, e) / 100.0)
    # Fragmenting high-velocity rounds do disproportionate damage in tissue.
    if velocity_at(w, range_m, altitude_m) > 700:
        severity *= 1.35
    if w.key == "shotgun" and range_m < 20:
        severity *= 1.8

    severity *= r.uniform(0.7, 1.35)
    kind = "gunshot" if w.muzzle_ms > 150 else "cut"
    return target.hurt(part, kind, severity, r)


def suppression_from(w: Weapon, range_m: float, miss_distance_m: float,
                     target: Pawn) -> float:
    """How much a near miss suppresses. Rounds cracking within a metre or two
    are what pin people; a round that lands ten metres away does nothing."""
    if w.melee or miss_distance_m > 6.0:
        return 0.0
    close = math.exp(-miss_distance_m / 1.8)
    loud = min(1.5, w.muzzle_energy_j / 2000.0)
    return close * (0.16 + 0.14 * loud) / max(0.4, target.mod("suppression_resist"))


# --------------------------------------------------------------------------
# combatants and engagements
# --------------------------------------------------------------------------


@dataclass
class Fighter:
    pawn: Pawn
    weapon: Weapon
    armour: Armour
    faction: str
    ammo: int = 60
    stance: str = "standing"
    #: Seconds until this fighter can shoot again.
    cooldown_s: float = 0.0
    in_magazine: int = 0
    target: "Fighter | None" = None
    #: True once the fighter has decided the day is lost.
    routed: bool = False
    moving: bool = False
    #: "assault" closes with the enemy, "hold" fights from where it stands.
    role: str = "hold"
    #: Seconds of movement owed before the next shot.
    move_budget_s: float = 0.0

    def __post_init__(self) -> None:
        if self.in_magazine == 0:
            self.in_magazine = self.weapon.magazine

    @property
    def alive(self) -> bool:
        return self.pawn.alive and not self.pawn.incapacitated

    @property
    def pos(self) -> tuple[int, int]:
        return self.pawn.x, self.pawn.y


@dataclass
class Engagement:
    """One firefight on one map."""

    map: terrain.LocalMap
    fighters: list[Fighter]
    seed: int
    log: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    #: Ambient light 0-1 and wind, both from the colony's weather.
    light: float = 1.0
    wind_ms: float = 0.0

    def side(self, faction: str) -> list[Fighter]:
        return [f for f in self.fighters if f.faction == faction and f.alive
                and not f.routed]

    def factions(self) -> list[str]:
        return sorted({f.faction for f in self.fighters})

    def enemies_of(self, f: Fighter) -> list[Fighter]:
        return [g for g in self.fighters
                if g.faction != f.faction and g.alive and not g.routed]

    def resolve(self, max_seconds: float = 600.0) -> str:
        """Run the firefight to a conclusion. Returns the winning faction, or
        an empty string if both sides broke off."""
        r = rng.Rng(rng.mix(self.seed, rng.tag("fight")))
        while self.elapsed_s < max_seconds:
            self.step(1.0, r)
            standing = [fac for fac in self.factions() if self.side(fac)]
            if len(standing) <= 1:
                return standing[0] if standing else ""
        return ""

    def step(self, dt: float, r: rng.Rng) -> None:
        self.elapsed_s += dt
        for f in self.fighters:
            if not f.alive or f.routed:
                continue
            p = f.pawn
            p.suppression = max(0.0, p.suppression - dt * 0.10)

            # Morale check: people break when hurt, outnumbered or pinned.
            if self._should_rout(f, r, self._pick_target(f) is not None):
                f.routed = True
                self.log.append(f"{p.name} breaks and runs")
                continue

            # Bleeding runs during the fight, not just afterwards.
            if p.bleeding_ml_min > 0:
                p.blood_ml -= p.bleeding_ml_min * dt / 60.0
                if p.blood_ml <= 5000.0 * 0.35:
                    p.dead = True
                    p.cause_of_death = "blood loss"
                    self.log.append(f"{p.name} bleeds out")
                    continue

            enemy = self._pick_target(f)
            f.target = enemy
            self._maneuver(f, enemy or self._nearest_enemy(f), dt, r,
                           in_contact=enemy is not None)

            f.cooldown_s -= dt
            if f.cooldown_s > 0 or enemy is None:
                continue
            self._shoot(f, enemy, r)

    #: Eight-way neighbourhood, reused by the manoeuvre search.
    _NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1),
                   (1, 1), (1, -1), (-1, 1), (-1, -1))

    def _maneuver(self, f: Fighter, enemy: "Fighter | None", dt: float,
                  r: rng.Rng, in_contact: bool = True) -> None:
        """Posture and movement.

        Without this, a firefight is two lines of people standing upright in
        the open shooting at each other, and resolves in about three seconds.
        Real people get down and get behind something, which is why real
        firefights last minutes and most rounds fired hit nothing.
        """
        p = f.pawn

        # Posture. Going prone cuts your silhouette to a fifth of standing,
        # at the cost of being slow to move and slow to see.
        if p.suppression > 0.30 or p.pain > 0.25:
            f.stance = "prone"
        elif p.suppression > 0.10 or in_contact:
            f.stance = "kneeling"
        else:
            f.stance = "standing"

        f.moving = False
        if p.capacity("mobility") < 0.3:
            return

        fx, fy = f.pos
        here = self.map.cover_at(fx, fy)

        # Under fire with nothing to hide behind, look for something. A pawn
        # crawling prone covers about a third of a metre a second.
        speed = p.move_speed_ms * (0.25 if f.stance == "prone" else 0.8)
        f.move_budget_s += dt * speed
        if f.move_budget_s < 1.0:
            return

        # Close if we are the attacker and either out of effective range or
        # simply cannot see anything to shoot at.
        want_closer = False
        if f.role == "assault" and enemy is not None:
            d = math.hypot(enemy.pos[0] - fx, enemy.pos[1] - fy)
            want_closer = (not in_contact) or d > f.weapon.max_range * 0.6

        occupied = {g.pos for g in self.fighters if g.alive and g is not f}
        best = None
        # When closing, distance has to outweigh cover per step or the element
        # simply drifts between trees and never arrives -- one metre of ground
        # gained must be worth more than the difference between two adjacent
        # bushes. When holding, cover is the only thing that matters.
        if want_closer and enemy is not None:
            ex, ey = enemy.pos
            best_score = -(math.hypot(ex - fx, ey - fy)) * 0.5 \
                + self.map.cover_at(fx, fy) * 0.4
        else:
            best_score = here * 2.0
        for dx, dy in self._NEIGHBOURS:
            nx, ny = fx + dx, fy + dy
            if not self.map.passable(nx, ny) or (nx, ny) in occupied:
                continue
            cov = self.map.cover_at(nx, ny)
            if want_closer and enemy is not None:
                ex, ey = enemy.pos
                score = -math.hypot(ex - nx, ey - ny) * 0.5 + cov * 0.4
            else:
                score = cov * 2.0
            if score > best_score:
                best_score = score
                best = (nx, ny)

        if best is not None:
            p.x, p.y = best
            f.moving = True
            f.move_budget_s -= 1.0
            # Moving means not shooting properly.
            f.cooldown_s = max(f.cooldown_s, 0.6)
        else:
            f.move_budget_s = min(f.move_budget_s, 1.0)

    def _should_rout(self, f: Fighter, r: rng.Rng, in_contact: bool) -> bool:
        p = f.pawn
        friends = len(self.side(f.faction))
        foes = len(self.enemies_of(f))
        if foes == 0:
            return False
        # Nobody breaks from an empty field. Routing requires something to
        # rout from: incoming fire, a wound, or an enemy in sight.
        if not in_contact and p.suppression < 0.05 and p.pain < 0.10:
            return False
        pressure = 0.0
        pressure += p.pain * 0.9
        pressure += p.suppression * 0.5
        pressure += max(0.0, (foes - friends) / max(1, foes)) * 0.6
        pressure += (1.0 - p.morale) * 0.4
        if f.ammo <= 0 and f.in_magazine <= 0:
            pressure += 0.8
        pressure /= max(0.4, p.mod("suppression_resist"))
        return r.random() < pressure * 0.02

    def _nearest_enemy(self, f: Fighter) -> "Fighter | None":
        """Nearest enemy regardless of line of sight.

        Manoeuvre needs this and shooting must not: an assault element that
        only knows about enemies it can currently see will stand in the trees
        at 100 m and never advance, because the thing it is advancing on is
        exactly what it cannot see.
        """
        best = None
        best_d = 1e18
        fx, fy = f.pos
        for g in self.enemies_of(f):
            d = (g.pos[0] - fx) ** 2 + (g.pos[1] - fy) ** 2
            if d < best_d:
                best_d = d
                best = g
        return best

    def _pick_target(self, f: Fighter) -> Fighter | None:
        """Nearest visible enemy, preferring wounded ones."""
        fx, fy = f.pos
        reach = f.weapon.max_range * 1.2
        # Sort by distance first and stop at the first enemy actually visible.
        # Testing line of sight to every enemy and then picking the nearest
        # does the same work several times over.
        cands = []
        for g in self.enemies_of(f):
            gx, gy = g.pos
            d = math.hypot(gx - fx, gy - fy)
            if d <= reach:
                # A wounded enemy is worth a little extra walking to shoot.
                cands.append((d - (25.0 if g.pawn.pain > 0.3 else 0.0), g))
        cands.sort(key=lambda c: c[0])
        for _, g in cands:
            if self.map.line_of_sight(fx, fy, g.pos[0], g.pos[1]):
                return g
        return None

    def _shoot(self, f: Fighter, target: Fighter, r: rng.Rng) -> None:
        w = f.weapon
        p = f.pawn
        fx, fy = f.pos
        tx, ty = target.pos
        range_m = math.hypot(tx - fx, ty - fy)

        if w.melee:
            if range_m > 1.8:
                return
            f.cooldown_s = 60.0 / max(1.0, w.rpm)
            skill = p.skills["melee"].level
            chance = rng.clamp01(0.35 + 0.03 * skill) * p.capacity("manipulation")
            chance *= max(0.3, 1.0 - target.pawn.capacity("mobility") * 0.25)
            if r.random() < chance:
                for e in resolve_hit(w, target.pawn, target.armour, range_m, r,
                                     attacker=p):
                    self.log.append(e)
                p.learn("melee", 1.0)
            return

        if f.in_magazine <= 0:
            if f.ammo <= 0:
                f.cooldown_s = 2.0
                return
            take = min(w.magazine, f.ammo)
            f.ammo -= take
            f.in_magazine = take
            f.cooldown_s = w.reload_s
            return

        cover = self.map.cover_at(tx, ty)
        shots = min(w.burst, f.in_magazine)
        f.in_magazine -= shots
        f.cooldown_s = 60.0 / max(1.0, w.rpm) * shots

        chance = hit_chance(w, p, range_m, target.stance, cover,
                            target.moving, self.light, self.wind_ms)
        hits = 0
        for _ in range(shots):
            if r.random() < chance:
                hits += 1
            else:
                # A miss still passes close enough to matter.
                miss_d = r.uniform(0.2, 4.0)
                target.pawn.suppression = min(
                    1.0, target.pawn.suppression
                    + suppression_from(w, range_m, miss_d, target.pawn))

        for _ in range(hits):
            for e in resolve_hit(w, target.pawn, target.armour, range_m, r):
                self.log.append(e)
            if not target.pawn.alive:
                break

        if hits:
            p.learn("shooting", 2.0 * hits)
            if not target.pawn.alive:
                self.log.append(
                    f"{p.name} killed {target.pawn.name} at {range_m:.0f} m")
            elif target.pawn.incapacitated:
                self.log.append(
                    f"{p.name} put {target.pawn.name} down at {range_m:.0f} m")


def make_fighter(p: Pawn, weapon_key: str, armour_key: str, faction: str,
                 ammo: int = 60) -> Fighter:
    return Fighter(pawn=p, weapon=WEAPONS[weapon_key],
                   armour=ARMOURS[armour_key], faction=faction, ammo=ammo)


def estimate_strength(fighters: list[Fighter]) -> float:
    """A rough combat power number, for strategic decisions.

    The rival AIs use this to decide whether an attack is worth making, so it
    has to be cheap and roughly monotonic in what actually wins fights:
    numbers, weapon reach, armour and skill.
    """
    total = 0.0
    for f in fighters:
        if not f.alive:
            continue
        w = f.weapon
        reach = 1.0 if w.melee else (0.6 + min(2.2, w.max_range / 250.0))
        rate = 0.7 + min(2.0, w.rpm / 90.0)
        skill = 0.5 + 0.06 * f.pawn.skills["melee" if w.melee else "shooting"].level
        armour = 1.0 + f.armour.level * 0.22
        health = f.pawn.capacity("consciousness")
        ammo = 1.0 if (w.melee or f.ammo + f.in_magazine > 15) else 0.55
        total += reach * rate * skill * armour * health * ammo
    return total
