import math


def make_noise(seed: int):
    """
    Perlin noise generator matching the JS implementation exactly.
    Uses a Park-Miller LCG for the permutation shuffle.
    Returns a callable noise(x, y) -> float in roughly [-1, 1].
    """
    s = int(seed)
    perm = [0] * 512

    def _next_rand():
        nonlocal s
        s = (s * 16807) % 2147483647
        return s

    p = list(range(256))
    for i in range(255, 0, -1):
        j = _next_rand() % (i + 1)
        p[i], p[j] = p[j], p[i]

    for i in range(512):
        perm[i] = p[i & 255]

    def _fade(t: float) -> float:
        return t * t * t * (t * (t * 6 - 15) + 10)

    def _lerp(a: float, b: float, t: float) -> float:
        return a + t * (b - a)

    def _grad(h: int, x: float, y: float) -> float:
        v = h & 3
        if v == 0: return  x + y
        if v == 1: return -x + y
        if v == 2: return  x - y
        return              -x - y

    def noise(x: float, y: float) -> float:
        X = int(math.floor(x)) & 255
        Y = int(math.floor(y)) & 255
        xf = x - math.floor(x)
        yf = y - math.floor(y)
        u = _fade(xf)
        v = _fade(yf)
        return _lerp(
            _lerp(_grad(perm[perm[X]     + Y],     xf,     yf),
                  _grad(perm[perm[X + 1] + Y],     xf - 1, yf),     u),
            _lerp(_grad(perm[perm[X]     + Y + 1], xf,     yf - 1),
                  _grad(perm[perm[X + 1] + Y + 1], xf - 1, yf - 1), u),
            v,
        )

    return noise


def fbm(noise_fn, x: float, y: float, octaves: int = 6) -> float:
    """Fractional Brownian Motion — sums octaves of noise."""
    value = 0.0
    amplitude = 1.0
    frequency = 1.0
    max_val = 0.0
    for _ in range(octaves):
        value    += noise_fn(x * frequency, y * frequency) * amplitude
        max_val  += amplitude
        amplitude *= 0.5
        frequency *= 2.0
    return value / max_val
