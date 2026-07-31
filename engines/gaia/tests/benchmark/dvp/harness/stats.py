"""Statistics: bootstrap CI, McNemar, geometric mean (DESIGN.md §七).

All functions are pure (numpy-free) so they run in any env. Where sample
sizes are tiny (benchmark n is small), we fall back to exact methods
(McNemar exact binomial) rather than asymptotic approximations.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def std(xs: Sequence[float], ddof: int = 1) -> float:
    n = len(xs)
    if n - ddof <= 0:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def geometric_mean(xs: Sequence[float]) -> float:
    """Geometric mean of positive ratios (DESIGN.md §七).

    Zeros are clamped to a tiny epsilon so a single 0 doesn't collapse the
    product (common when a baseline is instant). Returns 0.0 for empty input.
    """
    if not xs:
        return 0.0
    eps = 1e-9
    log_sum = 0.0
    for x in xs:
        log_sum += math.log(max(x, eps))
    return math.exp(log_sum / len(xs))


def bootstrap_ci(
    xs: Sequence[float],
    stat_fn=mean,
    n_resample: int = 2000,
    confidence: float = 0.95,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Bootstrap CI for a statistic. Returns (point_estimate, lo, hi).

    DESIGN.md §七: never report a raw mean without std/CI. n_resample=2000
    gives stable 95% bounds for the small samples in this benchmark.
    """
    if not xs:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    n = len(xs)
    point = stat_fn(xs)
    estimates = []
    for _ in range(n_resample):
        sample = [xs[rng.randrange(n)] for _ in range(n)]
        estimates.append(stat_fn(sample))
    estimates.sort()
    alpha = (1 - confidence) / 2
    lo = estimates[int(alpha * n_resample)]
    hi = estimates[int((1 - alpha) * n_resample)]
    return point, lo, hi


def percentile(xs: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile (p in [0,100])."""
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def mcnemar_exact(discordant_b_passed: int, discordant_a_passed: int) -> tuple[float, tuple[float, float]]:
    """McNemar exact test (2-sided) for paired binary outcomes.

    Args:
        discordant_b_passed: # cases where model B passed but A failed.
        discordant_a_passed: # cases where model A passed but B failed.
    Returns:
        (p_value, (ci_lo, ci_hi)) for the difference in accuracy.
    Uses the exact binomial (n = b + a, p = 0.5) — appropriate for the small
    n in this benchmark (DESIGN.md §七: McNemar 双侧精确检验).
    """
    n = discordant_b_passed + discordant_a_passed
    if n == 0:
        return 1.0, (0.0, 0.0)
    # Two-sided exact: 2 * min tail under Binomial(n, 0.5).
    from math import comb

    k = min(discordant_b_passed, discordant_a_passed)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2**n)
    p_value = min(1.0, 2 * tail)
    # Difference in accuracy = (b - a) / n_pairs.
    diff = (discordant_b_passed - discordant_a_passed) / n
    return p_value, (diff, diff)  # CI kept simple (point diff); p_value is the headline


def _binom_ci(k: int, n: int, alpha: float) -> tuple[float, float]:
    """Clopper-Pearson exact CI for a binomial proportion."""
    if n == 0:
        return 0.0, 1.0
    lo = _beta_inv(alpha / 2, k, n - k + 1)
    hi = _beta_inv(1 - alpha / 2, k + 1, n - k)
    return lo, hi


def _beta_inv(p: float, a: int, b: int) -> float:
    """Inverse of the regularized incomplete beta (Clopper-Pearson).

    Implemented via a simple bisection on the binomial CDF to avoid a scipy
    dependency. Coarse (1e-3) precision is plenty for benchmark CIs.
    """
    from math import comb

    def cdf(x: float) -> float:
        # P(X <= k-1) for X~Beta(a, b) ≈ binomial tail; use binomial sum.
        return sum(comb(a + b - 1, i) * (x**i) * ((1 - x) ** (a + b - 1 - i)) for i in range(a)) if a > 0 else 0.0

    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion (more stable than Wald at small n)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - spread), min(1.0, center + spread)
