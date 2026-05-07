"""IVR call flow state machine — tracks which step a caller is on.

State is passed via URL query parameters (stateless approach that works
with Twilio's webhook model without requiring server-side session storage).
"""

import random

from app.services.challenge_service import PHRASE_POOLS


def pick_random_enrollment_phrase(
    language: str = "en",
    exclude: list[str] | None = None,
) -> str:
    """Select a random phrase from the pool for an IVR enrollment recording.

    If `exclude` is provided, phrases already used in this enrollment session
    are skipped so the caller hears 5 unique prompts. If all phrases in the
    pool have been used, falls back to the full pool (should not happen in
    practice — pools are larger than ENROLLMENT_PHRASES).
    """
    pool = PHRASE_POOLS.get(language, PHRASE_POOLS["en"])
    if exclude:
        remaining = [p for p in pool if p not in exclude]
        if remaining:
            return random.choice(remaining)
    return random.choice(pool)
