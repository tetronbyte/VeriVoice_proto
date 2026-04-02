"""IVR call flow state machine — tracks which step a caller is on.

State is passed via URL query parameters (stateless approach that works
with Twilio's webhook model without requiring server-side session storage).
"""

import random
from enum import Enum

from app.services.challenge_service import PHRASE_POOLS


class IVRState(str, Enum):
    """Possible states during a VeriVoice IVR call."""
    WELCOME = "welcome"
    LANGUAGE_SELECTED = "language_selected"
    ENROLL_PROMPT = "enroll_prompt"
    ENROLL_RECORDING = "enroll_recording"     # recording_index tracks 1-5
    ENROLL_COMPLETE = "enroll_complete"
    AUTH_CHALLENGE = "auth_challenge"
    AUTH_RECORDING = "auth_recording"
    AUTH_RESULT = "auth_result"
    CONSENT_PROMPT = "consent_prompt"
    CONSENT_RECORDING = "consent_recording"
    CONSENT_RESULT = "consent_result"
    SERVICE_QUESTION = "service_question"
    SERVICE_RECORDING = "service_recording"
    SERVICE_COMPLETE = "service_complete"


def pick_random_enrollment_phrase(language: str = "en") -> str:
    """Select a random phrase from the pool for an IVR enrollment recording.

    Each call picks independently so that the 5 enrollment prompts are
    randomly varied (duplicates are possible but acceptable — what matters
    is voice biometric diversity, not phrase uniqueness).
    """
    pool = PHRASE_POOLS.get(language, PHRASE_POOLS["en"])
    return random.choice(pool)
