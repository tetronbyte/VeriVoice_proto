import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.orm import relationship

from app.db.database import Base


class VoiceTemplate(Base):
    __tablename__ = "VOICE_TEMPLATE"

    template_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    citizen_id = Column(String(36), ForeignKey("CITIZEN.citizen_id"), nullable=False)
    he_ciphertext = Column(LargeBinary, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    citizen = relationship("Citizen", back_populates="voice_templates")
