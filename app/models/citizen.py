import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String
from sqlalchemy.orm import relationship

from app.db.database import Base


class Citizen(Base):
    __tablename__ = "CITIZEN"

    citizen_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    national_id_number = Column(String, unique=True, nullable=False, index=True)
    preferred_language = Column(String, nullable=False, default="en")
    phone_number = Column(String, nullable=False)
    enrolled_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    voice_templates = relationship("VoiceTemplate", back_populates="citizen")
    auth_events = relationship("AuthEvent", back_populates="citizen")
    consent_tokens = relationship("ConsentToken", back_populates="citizen")
