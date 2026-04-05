import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from app.db.database import Base


class ServiceForm(Base):
    """A single service request captured via the IVR (generic).

    `service_code` identifies which service catalog entry was used
    (e.g. "pension", "mpesa_transfer", "aid_verification").
    `answers_json` is a JSON object mapping each service's field_keys
    to the transcribed answer string.
    """

    __tablename__ = "SERVICE_FORM"

    form_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    citizen_id = Column(String(36), ForeignKey("CITIZEN.citizen_id"), nullable=False)
    consent_token_id = Column(String(36), ForeignKey("CONSENT_TOKEN.token_id"), nullable=False)
    ministry_code = Column(String, nullable=False)
    service_code = Column(String, nullable=False)
    answers_json = Column(Text, nullable=False, default="{}")
    submitted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    citizen = relationship("Citizen")
    consent_token = relationship("ConsentToken")
