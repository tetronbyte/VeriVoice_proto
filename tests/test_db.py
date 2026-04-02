"""Phase 2 validation: full Create-Read cycle for all models + Revoke for ConsentToken."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from sqlalchemy.exc import IntegrityError

from app.db.crud import (
    create_auth_event,
    create_citizen,
    create_consent_token,
    create_voice_template,
    get_active_template,
    get_citizen_by_id,
    get_citizen_by_mosip_id,
    get_citizen_by_national_id,
    get_consent_token,
    link_mosip_identity,
    revoke_consent_token,
)


@pytest.fixture()
def db():
    """In-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# ── Citizen ──────────────────────────────────────────────────────────────────

class TestCitizen:
    def test_create_and_read_by_id(self, db):
        citizen = create_citizen(db, national_id_number="KE-123456", preferred_language="sw", phone_number="+254700000000")
        assert citizen.citizen_id is not None
        assert citizen.enrolled_at is not None

        fetched = get_citizen_by_id(db, citizen.citizen_id)
        assert fetched is not None
        assert fetched.national_id_number == "KE-123456"
        assert fetched.preferred_language == "sw"

    def test_read_by_national_id(self, db):
        create_citizen(db, national_id_number="UG-789012", phone_number="+256700000000")
        fetched = get_citizen_by_national_id(db, "UG-789012")
        assert fetched is not None
        assert fetched.phone_number == "+256700000000"

    def test_duplicate_national_id_raises(self, db):
        create_citizen(db, national_id_number="KE-DUP", phone_number="+254700000001")
        with pytest.raises(Exception):
            create_citizen(db, national_id_number="KE-DUP", phone_number="+254700000002")

    def test_nonexistent_citizen_returns_none(self, db):
        assert get_citizen_by_id(db, "no-such-id") is None
        assert get_citizen_by_national_id(db, "no-such-nid") is None

    def test_new_citizen_identity_not_verified(self, db):
        citizen = create_citizen(db, national_id_number="KE-MOSIP1", phone_number="+254700000050")
        assert citizen.identity_verified is False
        assert citizen.mosip_individual_id is None

    def test_link_mosip_identity(self, db):
        citizen = create_citizen(db, national_id_number="KE-MOSIP2", phone_number="+254700000051")
        assert citizen.identity_verified is False

        linked = link_mosip_identity(db, citizen.citizen_id, "MOSIP-IND-001")
        assert linked.identity_verified is True
        assert linked.mosip_individual_id == "MOSIP-IND-001"

        # Verify lookup by MOSIP ID works
        fetched = get_citizen_by_mosip_id(db, "MOSIP-IND-001")
        assert fetched is not None
        assert fetched.citizen_id == citizen.citizen_id

    def test_duplicate_mosip_id_raises_integrity_error(self, db):
        c1 = create_citizen(db, national_id_number="KE-MOSIP3", phone_number="+254700000052")
        c2 = create_citizen(db, national_id_number="KE-MOSIP4", phone_number="+254700000053")

        link_mosip_identity(db, c1.citizen_id, "MOSIP-IND-DUP")
        with pytest.raises(IntegrityError):
            link_mosip_identity(db, c2.citizen_id, "MOSIP-IND-DUP")

    def test_get_citizen_by_mosip_id_not_found(self, db):
        assert get_citizen_by_mosip_id(db, "MOSIP-NONEXISTENT") is None

    def test_link_mosip_nonexistent_citizen_raises(self, db):
        with pytest.raises(ValueError):
            link_mosip_identity(db, "no-such-citizen", "MOSIP-IND-999")


# ── VoiceTemplate ────────────────────────────────────────────────────────────

class TestVoiceTemplate:
    def test_create_and_read_active(self, db):
        citizen = create_citizen(db, national_id_number="KE-T1", phone_number="+254700000010")
        template = create_voice_template(db, citizen_id=citizen.citizen_id, he_ciphertext=b"fake-ciphertext")
        assert template.template_id is not None
        assert template.is_active is True

        active = get_active_template(db, citizen.citizen_id)
        assert active is not None
        assert active.he_ciphertext == b"fake-ciphertext"

    def test_re_enrollment_deactivates_old(self, db):
        citizen = create_citizen(db, national_id_number="KE-T2", phone_number="+254700000011")
        old = create_voice_template(db, citizen_id=citizen.citizen_id, he_ciphertext=b"old")
        new = create_voice_template(db, citizen_id=citizen.citizen_id, he_ciphertext=b"new")

        active = get_active_template(db, citizen.citizen_id)
        assert active.template_id == new.template_id
        assert active.he_ciphertext == b"new"

        # Refresh old to see the update
        db.refresh(old)
        assert old.is_active is False


# ── AuthEvent ────────────────────────────────────────────────────────────────

class TestAuthEvent:
    def test_create_and_read(self, db):
        citizen = create_citizen(db, national_id_number="KE-A1", phone_number="+254700000020")
        event = create_auth_event(db, citizen_id=citizen.citizen_id, voice_match_score=0.72, result="granted")
        assert event.event_id is not None
        assert event.voice_match_score == 0.72
        assert event.result == "granted"
        assert event.event_timestamp is not None


# ── ConsentToken ─────────────────────────────────────────────────────────────

class TestConsentToken:
    def test_create_read_revoke_cycle(self, db):
        citizen = create_citizen(db, national_id_number="KE-C1", phone_number="+254700000030")

        # Create
        token = create_consent_token(
            db,
            citizen_id=citizen.citizen_id,
            ministry_code="MOH",
            data_scope="health_records",
            digital_signature=b"\x01\x02\x03",
        )
        assert token.token_id is not None
        assert token.is_revoked is False
        assert token.digital_signature == b"\x01\x02\x03"

        # Read
        fetched = get_consent_token(db, token.token_id)
        assert fetched is not None
        assert fetched.ministry_code == "MOH"
        assert fetched.data_scope == "health_records"

        # Revoke
        revoked = revoke_consent_token(db, token.token_id)
        assert revoked is not None
        assert revoked.is_revoked is True

        # Confirm revocation persisted
        fetched_again = get_consent_token(db, token.token_id)
        assert fetched_again.is_revoked is True

    def test_revoke_nonexistent_returns_none(self, db):
        assert revoke_consent_token(db, "no-such-token") is None
