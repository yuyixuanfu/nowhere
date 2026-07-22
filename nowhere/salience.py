"""Salience ranking — pick the top-3 things the body should report.

Score = 0.5*delta + 0.3*novelty + 0.2*(1-body_distance).

Only the top 3 survive; the rest stay silent in the data attachment.
"""

from __future__ import annotations

import random


def rank(
    candidates: list[dict],
    rng: random.Random,
    recent_kinds: set[str] | None = None,
) -> list[dict]:
    """Rank candidates by salience and return the top 3.

    Parameters
    ----------
    candidates : list[dict]
        Each dict must have keys: kind, delta, novelty, body_distance, payload.
    rng : random.Random
        Seeded RNG for tie-breaking (reproducible).
    recent_kinds : set[str] | None
        Kinds that appeared in the previous salience result.  Novelty for
        these is multiplied by 0.1 to prevent the same kind winning every
        time when all deltas are zero.

    Returns
    -------
    list[dict]
        Top-3 candidates sorted by score descending.  Ties broken by rng.
    """
    if not candidates:
        return []

    if recent_kinds is None:
        recent_kinds = set()

    scored = []
    for c in candidates:
        novelty = c["novelty"]
        if c["kind"] in recent_kinds:
            novelty *= 0.1
        score = (
            0.5 * c["delta"]
            + 0.3 * novelty
            + 0.2 * (1.0 - c["body_distance"])
        )
        scored.append((score, rng.random(), c))

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [t[2] for t in scored[:3]]
