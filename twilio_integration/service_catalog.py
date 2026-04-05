"""Catalog of IVR services: 5 government/financial use-cases for VeriVoice.

Each service has:
  - menu_key:          single DTMF digit for selection in the service menu
  - label:             spoken name of the service (EN + SW) — used in menu/readback
  - ministry_code:     short code stored in SERVICE_FORM (provenance audit)
  - field_keys:        3 semantic keys for the 3 answers (used in readback template)
  - questions:         3 prompts per language
  - readback_template: formatted summary built from the 3 answers (EN + SW)

The IVR asks exactly 3 questions per service. The readback_template uses
str.format(**answers) where answers is a dict keyed by field_keys.
"""

SERVICES: dict[str, dict] = {
    # 1. Inua Jamii Pension Withdrawal ──────────────────────────────────────
    "pension": {
        "menu_key": "1",
        "label": {
            "en": "Inua Jamii Pension Withdrawal",
            "sw": "Malipo ya Pensheni ya Inua Jamii",
        },
        "ministry_code": "MLSP",  # Ministry of Labour & Social Protection
        "field_keys": ["payment_count", "withdrawal_type", "delivery_method"],
        "questions": {
            "en": [
                "How many times have you received Inua Jamii payments before? Please say the number, or say first time.",
                "Would you like to withdraw the full amount or a partial amount? Please say: full, or partial.",
                "How would you like to receive your funds? Please say: M-Pesa wallet, or cash at agent.",
            ],
            "sw": [
                "Umepokea malipo ya Inua Jamii mara ngapi hapo awali? Tafadhali sema nambari, au sema mara ya kwanza.",
                "Ungependa kutoa kiasi chote au kiasi cha sehemu? Tafadhali sema: chote, au sehemu.",
                "Ungependa kupokeaje pesa zako? Tafadhali sema: mkoba wa M-Pesa, au pesa taslimu kwa wakala.",
            ],
        },
        "readback_template": {
            "en": (
                "You have requested a {withdrawal_type} Inua Jamii withdrawal, "
                "with {payment_count} prior payments, delivered via {delivery_method}. "
                "Is this correct? Say yes or no."
            ),
            "sw": (
                "Umeomba kutoa pensheni ya Inua Jamii ya {withdrawal_type}, "
                "na malipo ya awali {payment_count}, kupitia {delivery_method}. "
                "Je, hii ni sahihi? Sema Ndiyo au Hapana."
            ),
        },
    },

    # 2. M-Pesa Fund Transfer ────────────────────────────────────────────────
    "mpesa_transfer": {
        "menu_key": "2",
        "label": {
            "en": "M-Pesa Fund Transfer",
            "sw": "Utumaji wa Pesa wa M-Pesa",
        },
        "ministry_code": "SCL",  # Safaricom (telecom operator)
        "field_keys": ["recipient_number", "amount", "reason"],
        "questions": {
            "en": [
                "Please say the M-Pesa phone number you are sending to, one digit at a time.",
                "How much would you like to transfer? Please say the amount.",
                "Please say the reason for the money transfer.",
            ],
            "sw": [
                "Tafadhali sema nambari ya simu ya M-Pesa unayotuma, nambari moja kwa wakati.",
                "Ungependa kutuma kiasi gani? Tafadhali sema kiasi.",
                "Tafadhali sema sababu ya kutuma pesa.",
            ],
        },
        "readback_template": {
            "en": (
                "You are sending {amount} to {recipient_number}, "
                "for {reason}. Is this correct? Say yes or no."
            ),
            "sw": (
                "Unatuma {amount} kwa {recipient_number}, "
                "kwa sababu ya {reason}. Je, hii ni sahihi? Sema Ndiyo au Hapana."
            ),
        },
    },

    # 3. Aid Verification (Proof of Life) ────────────────────────────────────
    "aid_verification": {
        "menu_key": "3",
        "label": {
            "en": "Aid Verification",
            "sw": "Uthibitisho wa Msaada",
        },
        "ministry_code": "UNHCR",
        "field_keys": ["household_size", "all_present", "delivery_method"],
        "questions": {
            "en": [
                "How many people are currently in your household? Please say the number.",
                "Are all members of your household present at this location? Please say: yes, or no.",
                "Would you like your aid sent to M-Pesa or collected at a distribution point? Please say: M-Pesa, or distribution point.",
            ],
            "sw": [
                "Kuna watu wangapi kwenye kaya yako kwa sasa? Tafadhali sema nambari.",
                "Je, wanakaya wote wapo mahali hapa kwa sasa? Tafadhali sema: ndiyo, au hapana.",
                "Ungependa msaada wako utumwe kwa M-Pesa au uchukuliwe kituoni? Tafadhali sema: M-Pesa, au kituo cha mgao.",
            ],
        },
        "readback_template": {
            "en": (
                "Household of {household_size} members, all present {all_present}, "
                "aid to {delivery_method}. Is this correct? Say yes or no."
            ),
            "sw": (
                "Kaya ya watu {household_size}, wote wapo {all_present}, "
                "msaada kwenda {delivery_method}. Je, hii ni sahihi? Sema Ndiyo au Hapana."
            ),
        },
    },

    # 4. SIM Swap Protection ─────────────────────────────────────────────────
    "sim_swap": {
        "menu_key": "4",
        "label": {
            "en": "SIM Swap Protection",
            "sw": "Ulinzi wa Kubadilisha SIM",
        },
        "ministry_code": "TELCO",
        "field_keys": ["action", "phone_number", "confirmation"],
        "questions": {
            "en": [
                "What would you like to do? Please say: lock account, unlock account, or reset PIN.",
                "Please say the phone number for this request, one digit at a time.",
                "Please say confirm to proceed, or cancel to stop.",
            ],
            "sw": [
                "Ungependa kufanya nini? Tafadhali sema: funga akaunti, fungua akaunti, au weka upya PIN.",
                "Tafadhali sema nambari ya simu kwa ombi hili, nambari moja kwa wakati.",
                "Tafadhali sema thibitisha kuendelea, au ghairi kusimamisha.",
            ],
        },
        "readback_template": {
            "en": (
                "You are requesting to {action} for number {phone_number}, "
                "with status {confirmation}. Is this correct? Say yes or no."
            ),
            "sw": (
                "Unaomba ku-{action} kwa nambari {phone_number}, "
                "na hali {confirmation}. Je, hii ni sahihi? Sema Ndiyo au Hapana."
            ),
        },
    },

    # 5. Telemedicine Check-In ───────────────────────────────────────────────
    "telemedicine": {
        "menu_key": "5",
        "label": {
            "en": "Telemedicine Check-In",
            "sw": "Huduma ya Afya kwa Simu",
        },
        "ministry_code": "MOH",
        "field_keys": ["visit_type", "callback_day", "time_of_day"],
        "questions": {
            "en": [
                "What type of visit do you need? Please say: prescription refill, consultation, or emergency.",
                "Would you like a callback today or tomorrow? Please say: today, or tomorrow.",
                "What time do you prefer? Please say: morning, afternoon, or evening.",
            ],
            "sw": [
                "Unahitaji aina gani ya ziara? Tafadhali sema: kuongeza dawa, ushauri, au dharura.",
                "Ungependa simu ya kurudi leo au kesho? Tafadhali sema: leo, au kesho.",
                "Muda gani unapendelea? Tafadhali sema: asubuhi, mchana, au jioni.",
            ],
        },
        "readback_template": {
            "en": (
                "You have requested a {visit_type} callback {callback_day} in the {time_of_day}. "
                "Is this correct? Say yes or no."
            ),
            "sw": (
                "Umeomba simu ya {visit_type} {callback_day} wakati wa {time_of_day}. "
                "Je, hii ni sahihi? Sema Ndiyo au Hapana."
            ),
        },
    },
}


def get_service(service_code: str) -> dict | None:
    """Return the service dict or None if the code is unknown."""
    return SERVICES.get(service_code)


def service_code_by_menu_key(menu_key: str) -> str | None:
    """Look up a service_code from the DTMF menu digit the caller pressed."""
    for code, svc in SERVICES.items():
        if svc["menu_key"] == menu_key:
            return code
    return None


def build_readback(service_code: str, lang: str, answers: dict[str, str]) -> str:
    """Render the service's readback template with the captured answers."""
    svc = SERVICES[service_code]
    template = svc["readback_template"].get(lang, svc["readback_template"]["en"])
    # Fill any missing keys with "(unclear)" so str.format doesn't raise.
    filled = {k: (answers.get(k) or "(unclear)") for k in svc["field_keys"]}
    return template.format(**filled)


def menu_prompt(lang: str) -> str:
    """Build the service-selection menu prompt (numbered list of services)."""
    if lang == "sw":
        intro = "Tafadhali chagua huduma. "
        lines = [
            f"Bonyeza {svc['menu_key']} kwa {svc['label']['sw']}."
            for svc in SERVICES.values()
        ]
    else:
        intro = "Please select a service. "
        lines = [
            f"Press {svc['menu_key']} for {svc['label']['en']}."
            for svc in SERVICES.values()
        ]
    return intro + " ".join(lines)
