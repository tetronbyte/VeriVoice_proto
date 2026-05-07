from sqlalchemy.orm import Session

from app.models.auth_event import AuthEvent
from app.models.citizen import Citizen
from app.models.consent_token import ConsentToken
from app.models.service_form import ServiceForm
from app.models.voice_template import VoiceTemplate


# ── Citizen ──────────────────────────────────────────────────────────────────

def create_citizen(
    db: Session,
    national_id_number: str,
    preferred_language: str = "en",
    phone_number: str = "",
    mosip_individual_id: str | None = None,
    identity_verified: bool = False,
) -> Citizen:
    citizen = Citizen(
        national_id_number=national_id_number,
        preferred_language=preferred_language,
        phone_number=phone_number,
        mosip_individual_id=mosip_individual_id,
        identity_verified=identity_verified,
    )
    db.add(citizen)
    db.commit()
    db.refresh(citizen)
    return citizen


def get_citizen_by_id(db: Session, citizen_id: str) -> Citizen | None:
    return db.query(Citizen).filter(Citizen.citizen_id == citizen_id).first()


def get_citizen_by_national_id(db: Session, national_id_number: str) -> Citizen | None:
    return db.query(Citizen).filter(Citizen.national_id_number == national_id_number).first()


def get_citizen_by_mosip_id(db: Session, mosip_individual_id: str) -> Citizen | None:
    return db.query(Citizen).filter(Citizen.mosip_individual_id == mosip_individual_id).first()


def link_mosip_identity(db: Session, citizen_id: str, mosip_individual_id: str) -> Citizen:
    citizen = get_citizen_by_id(db, citizen_id)
    if citizen is None:
        raise ValueError(f"Citizen {citizen_id} not found")
    citizen.mosip_individual_id = mosip_individual_id
    citizen.identity_verified = True
    db.commit()
    db.refresh(citizen)
    return citizen


# ── VoiceTemplate ────────────────────────────────────────────────────────────

def create_voice_template(
    db: Session,
    citizen_id: str,
    he_ciphertext: bytes,
) -> VoiceTemplate:
    try:
        # Deactivate any existing active templates for this citizen
        db.query(VoiceTemplate).filter(
            VoiceTemplate.citizen_id == citizen_id,
            VoiceTemplate.is_active == True,  # noqa: E712
        ).update({"is_active": False})

        template = VoiceTemplate(
            citizen_id=citizen_id,
            he_ciphertext=he_ciphertext,
            is_active=True,
        )
        db.add(template)
        db.commit()
        db.refresh(template)
        return template
    except Exception:
        db.rollback()
        raise


def get_active_template(db: Session, citizen_id: str) -> VoiceTemplate | None:
    return (
        db.query(VoiceTemplate)
        .filter(
            VoiceTemplate.citizen_id == citizen_id,
            VoiceTemplate.is_active == True,  # noqa: E712
        )
        .first()
    )


# ── AuthEvent ────────────────────────────────────────────────────────────────

def create_auth_event(
    db: Session,
    citizen_id: str,
    voice_match_score: float,
    result: str,
) -> AuthEvent:
    event = AuthEvent(
        citizen_id=citizen_id,
        voice_match_score=voice_match_score,
        result=result,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


# ── ConsentToken ─────────────────────────────────────────────────────────────

def create_consent_token(
    db: Session,
    citizen_id: str,
    ministry_code: str,
    data_scope: str,
    digital_signature: bytes,
) -> ConsentToken:
    token = ConsentToken(
        citizen_id=citizen_id,
        ministry_code=ministry_code,
        data_scope=data_scope,
        digital_signature=digital_signature,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token


def get_consent_token(db: Session, token_id: str) -> ConsentToken | None:
    return db.query(ConsentToken).filter(ConsentToken.token_id == token_id).first()


def revoke_consent_token(db: Session, token_id: str) -> ConsentToken | None:
    token = get_consent_token(db, token_id)
    if token is None:
        return None
    token.is_revoked = True
    db.commit()
    db.refresh(token)
    return token


# ── ServiceForm ──────────────────────────────────────────────────────────────

def create_service_form(
    db: Session,
    citizen_id: str,
    consent_token_id: str,
    ministry_code: str,
    service_code: str,
    answers_json: str,
) -> ServiceForm:
    form = ServiceForm(
        citizen_id=citizen_id,
        consent_token_id=consent_token_id,
        ministry_code=ministry_code,
        service_code=service_code,
        answers_json=answers_json,
    )
    db.add(form)
    db.commit()
    db.refresh(form)
    return form


def get_service_form(db: Session, form_id: str) -> ServiceForm | None:
    return db.query(ServiceForm).filter(ServiceForm.form_id == form_id).first()
