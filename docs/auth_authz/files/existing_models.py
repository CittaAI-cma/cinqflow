# Point this at whatever already maps your `users` table — delete this file
# and fix the two imports in api/auth.py + api/deps.py once you do.
#
# Minimal shape this system needs from your User model:
#   id: UUID (primary key)
#   email: str (unique)
#   hashed_password: str
#   is_active: bool
#
# Example, if you don't already have one:

import uuid

from sqlalchemy import Boolean, Column, String
from sqlalchemy.dialects.postgresql import UUID

from .session import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
