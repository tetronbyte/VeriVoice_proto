import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.database import Base


class ServiceForm(Base):
    __tablename__ = "SERVICE_FORM"

    form_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    citizen_id = Column(String(36), ForeignKey("CITIZEN.citizen_id"), nullable=False)
    consent_token_id = Column(String(36), ForeignKey("CONSENT_TOKEN.token_id"), nullable=False)
    ministry_code = Column(String, nullable=False)
    form_type = Column(String, nullable=False)
    full_name = Column(String, nullable=False, default="")
    dependants = Column(Integer, nullable=False, default=0)
    primary_facility = Column(String, nullable=False, default="")
    submitted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    citizen = relationship("Citizen")
    consent_token = relationship("ConsentToken")
