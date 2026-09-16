"""
Database models and session handling for the License Server.

SQLAlchemy 2.x style models. Works with PostgreSQL (production) and SQLite
(local development) through the same code path.
"""

import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _engine():
    url = settings.DATABASE_URL
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args, future=True)


engine = _engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    license_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    tier: Mapped[str] = mapped_column(String(32), default="Pro")
    max_users: Mapped[int] = mapped_column(Integer, default=50)
    max_activations: Mapped[int] = mapped_column(Integer, default=1)
    features_json: Mapped[str] = mapped_column(Text, default="{}")  # JSON blob
    customer: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    activations: Mapped[list["Activation"]] = relationship(
        back_populates="license", cascade="all, delete-orphan"
    )


class Activation(Base):
    __tablename__ = "activations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    license_id: Mapped[int] = mapped_column(ForeignKey("licenses.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    hostname: Mapped[str] = mapped_column(String(255), default="")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    license: Mapped[License] = relationship(back_populates="activations")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    action: Mapped[str] = mapped_column(String(64))
    license_key: Mapped[str] = mapped_column(String(128), default="", index=True)
    fingerprint: Mapped[str] = mapped_column(String(128), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
