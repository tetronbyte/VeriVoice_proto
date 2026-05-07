import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.orm import relationship

from app.config import settings
from app.db.database import Base


class ConsentToken(Base):
    __tablename__ = "CONSENT_TOKEN"

    token_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    citizen_id = Column(String(36), ForeignKey("CITIZEN.citizen_id"), nullable=False)
    ministry_code = Column(String, nullable=False)
    data_scope = Column(String, nullable=False)
    digital_signature = Column(LargeBinary, nullable=False)
    issued_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(
        DateTime,
        nullable=True,
        default=lambda: datetime.now(timezone.utc) + timedelta(hours=settings.CONSENT_TOKEN_TTL_HOURS),
    )
    is_revoked = Column(Boolean, nullable=False, default=False)

    citizen = relationship("Citizen", back_populates="consent_tokens")
