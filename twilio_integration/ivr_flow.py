"""IVR call flow state machine — tracks which step a caller is on.

State is passed via URL query parameters (stateless approach that works
with Twilio's webhook model without requiring server-side session storage).
"""

from enum import Enum


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


# Enrollment prompt phrases spoken before each of the 5 recordings
ENROLLMENT_PROMPTS = {
    "en": [
        "Please say: The sun rises over the mountain every morning.",
        "Please say: My voice is my password and it is unique.",
        "Please say: The market opens early on Wednesday.",
        "Please say: I confirm this request with my own voice.",
        "Please say: Please verify my identity for this service.",
    ],
    "sw": [
        "Tafadhali sema: Jua linachomoza juu ya mlima kila asubuhi.",
        "Tafadhali sema: Sauti yangu ndiyo nenosiri langu.",
        "Tafadhali sema: Soko linafunguliwa mapema siku ya Jumatano.",
        "Tafadhali sema: Ninathibitisha ombi hili kwa sauti yangu.",
        "Tafadhali sema: Tafadhali thibitisha utambulisho wangu.",
    ],
}
