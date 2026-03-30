import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import relationship

from app.db.database import Base


class AuthEvent(Base):
    __tablename__ = "AUTH_EVENT"

    event_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    citizen_id = Column(String(36), ForeignKey("CITIZEN.citizen_id"), nullable=False)
    voice_match_score = Column(Float, nullable=False)
    result = Column(String, nullable=False)  # "granted" or "denied"
    event_timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    citizen = relationship("Citizen", back_populates="auth_events")
