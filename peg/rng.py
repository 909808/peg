"""Deterministic pseudo-randomness and coherent noise.

Everything in PEG must be reproducible from a single 64-bit world seed. That
rules out global RNG state: two players with the same seed who visit sites in a
different order must still see the same Earth. So all randomness here is
*positional* -- derived by hashing coordinates and a purpose tag, never by
advancing a shared stream.

The one exception is `Rng`, a stateful stream used for things that genuinely
happen in sequence (a firefight, a surgery). Those get seeded from a positional
hash of the event, so replaying a saved game reproduces them exactly.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence, TypeVar

M64 = 0xFFFFFFFFFFFFFFFF
_INV53 = 1.0 / float(1 << 53)

T = TypeVar("T")


# --------------------------------------------------------------------------
# integer hashing
# --------------------------------------------------------------------------


def splitmix64(x: int) -> int:
    """SplitMix64 finalizer. Good avalanche, four ops, no state."""
    x = (x + 0x9E3779B97F4A7C15) & M64
    z = x
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
    return z ^ (z >> 31)


def mix(*values: int) -> int:
    """Order-dependent hash of any number of integers."""
    h = 0xCBF29CE484222325
    for v in values:
        h = splitmix64(h ^ (v & M64))
    return h


def tag(text: str) -> int:
    """Stable 64-bit tag for a string, so call sites can read as
    ``mix(seed, TAG_ORE, x, y)`` instead of magic numbers."""
    h = 0x100000001B3
    for ch in text.encode("utf-8"):
        h = ((h ^ ch) * 0x100000001B3) & M64
    return splitmix64(h)


def unit(h: int) -> float:
    """Hash -> float in [0, 1)."""
    return (h >> 11) * _INV53


def sunit(h: int) -> float:
    """Hash -> float in [-1, 1)."""
    return unit(h) * 2.0 - 1.0


def chance(h: int, p: float) -> bool:
    return unit(h) < p


# --------------------------------------------------------------------------
# stateful stream
# --------------------------------------------------------------------------


class Rng:
    """A small deterministic stream. Cheap to create; make one per event."""

    __slots__ = ("state",)

    def __init__(self, seed: int = 0):
        self.state = splitmix64(seed & M64)

    @classmethod
    def at(cls, *values: int) -> "Rng":
        return cls(mix(*values))

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & M64
        return splitmix64(self.state)

    def random(self) -> float:
        return unit(self.next_u64())

    def uniform(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self.random()

    def randint(self, lo: int, hi: int) -> int:
        """Inclusive on both ends."""
        if hi <= lo:
            return lo
        return lo + self.next_u64() % (hi - lo + 1)

    def chance(self, p: float) -> bool:
        return self.random() < p

    def choice(self, seq: Sequence[T]) -> T:
        return seq[self.next_u64() % len(seq)]

    def shuffled(self, seq: Iterable[T]) -> list[T]:
        items = list(seq)
        for i in range(len(items) - 1, 0, -1):
            j = self.next_u64() % (i + 1)
            items[i], items[j] = items[j], items[i]
        return items

    def weighted(self, options: Sequence[tuple[T, float]]) -> T:
        """Pick from ``[(value, weight), ...]``. Weights need not sum to 1."""
        total = 0.0
        for _, w in options:
            if w > 0:
                total += w
        if total <= 0:
            return options[0][0]
        roll = self.random() * total
        for value, w in options:
            if w <= 0:
                continue
            roll -= w
            if roll <= 0:
                return value
        return options[-1][0]

    def gauss(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        """Box-Muller. We only keep one of the two samples; the stream is
        cheap enough that caching the spare is not worth the state."""
        u1 = max(self.random(), 1e-12)
        u2 = self.random()
        return mu + sigma * math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def clamped_gauss(self, mu: float, sigma: float, lo: float, hi: float) -> float:
        return min(hi, max(lo, self.gauss(mu, sigma)))


# --------------------------------------------------------------------------
# interpolation
# --------------------------------------------------------------------------


def smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def quintic(t: float) -> float:
    """Perlin's improved fade curve: zero 1st and 2nd derivatives at the ends,
    which keeps octave seams invisible."""
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def remap(v: float, in_lo: float, in_hi: float, out_lo: float, out_hi: float) -> float:
    if in_hi == in_lo:
        return out_lo
    return out_lo + (out_hi - out_lo) * clamp01((v - in_lo) / (in_hi - in_lo))


# --------------------------------------------------------------------------
# coherent noise
# --------------------------------------------------------------------------

# 8 evenly spaced unit gradients. A power-of-two table lets us index with a
# mask instead of a modulo, which matters -- this is the hottest loop in
# worldgen.
_GRAD2 = [
    (1.0, 0.0), (0.7071067811865476, 0.7071067811865476),
    (0.0, 1.0), (-0.7071067811865476, 0.7071067811865476),
    (-1.0, 0.0), (-0.7071067811865476, -0.7071067811865476),
    (0.0, -1.0), (0.7071067811865476, -0.7071067811865476),
]


def perlin2(seed: int, x: float, y: float) -> float:
    """2D gradient noise in roughly [-1, 1]."""
    xi = math.floor(x)
    yi = math.floor(y)
    xf = x - xi
    yf = y - yi
    xi = int(xi)
    yi = int(yi)

    u = quintic(xf)
    v = quintic(yf)

    def dot(gx: int, gy: int) -> float:
        g = _GRAD2[splitmix64(mix(seed, gx, gy)) & 7]
        return g[0] * (xf - (gx - xi)) + g[1] * (yf - (gy - yi))

    n00 = dot(xi, yi)
    n10 = dot(xi + 1, yi)
    n01 = dot(xi, yi + 1)
    n11 = dot(xi + 1, yi + 1)

    a = n00 + u * (n10 - n00)
    b = n01 + u * (n11 - n01)
    return (a + v * (b - a)) * 1.4142135623730951


def fbm2(
    seed: int,
    x: float,
    y: float,
    octaves: int = 4,
    lacunarity: float = 2.0,
    gain: float = 0.5,
) -> float:
    """Fractional Brownian motion. Result normalised to about [-1, 1]."""
    total = 0.0
    amp = 1.0
    norm = 0.0
    freq = 1.0
    for i in range(octaves):
        total += perlin2(seed + i * 0x9E37, x * freq, y * freq) * amp
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm if norm else 0.0


def ridged2(
    seed: int,
    x: float,
    y: float,
    octaves: int = 4,
    lacunarity: float = 2.0,
    gain: float = 0.5,
) -> float:
    """Ridged multifractal in [0, 1]. Produces creases rather than blobs --
    what mountain spurs and river valleys actually look like."""
    total = 0.0
    amp = 1.0
    norm = 0.0
    freq = 1.0
    for i in range(octaves):
        n = 1.0 - abs(perlin2(seed + i * 0x85EB, x * freq, y * freq))
        total += n * n * amp
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm if norm else 0.0


def billow2(seed: int, x: float, y: float, octaves: int = 4) -> float:
    """Absolute-value fBm in [0, 1]. Good for dunes and cloud cover."""
    total = 0.0
    amp = 1.0
    norm = 0.0
    freq = 1.0
    for i in range(octaves):
        total += abs(perlin2(seed + i * 0xC2B2, x * freq, y * freq)) * amp
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm if norm else 0.0


def warp2(seed: int, x: float, y: float, strength: float = 1.0) -> tuple[float, float]:
    """Domain warp. Bends the sampling grid so features stop looking like they
    were drawn on graph paper."""
    wx = fbm2(seed ^ 0x1234567, x, y, 2)
    wy = fbm2(seed ^ 0x89ABCDE, x + 5.2, y + 1.3, 2)
    return x + wx * strength, y + wy * strength


def worley2(seed: int, x: float, y: float, jitter: float = 1.0) -> tuple[float, float]:
    """Cellular noise. Returns ``(distance to nearest feature point, cell id)``.

    Used for ore bodies and boulder fields, which are patchy rather than
    smoothly varying -- fBm gets that wrong in a way you can see.
    """
    xi = int(math.floor(x))
    yi = int(math.floor(y))
    best = 1e9
    best_id = 0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            cx = xi + dx
            cy = yi + dy
            h = mix(seed, cx, cy)
            px = cx + 0.5 + sunit(h) * 0.5 * jitter
            py = cy + 0.5 + sunit(splitmix64(h)) * 0.5 * jitter
            d = (px - x) ** 2 + (py - y) ** 2
            if d < best:
                best = d
                best_id = h
    return math.sqrt(best), best_id
