"""Household, viewer, station and pairing-code tables (T2.1).

Timestamps are float epoch seconds (portable between sqlite and Postgres, and easy to compare).
All ids are UUID4 hex strings. Soft deletes (`revoked_at`, `deleted_at`) keep the audit trail; a
hard delete is `repo.delete_household`.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.store.db import Base, new_id

ROLE_OWNER = "owner"
ROLE_VIEWER = "viewer"
KIND_STATION = "station"
KIND_VIEWER = "viewer"


class Household(Base):
    __tablename__ = "households"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), default="")
    dog_name: Mapped[str] = mapped_column(String(64), default="Your dog")
    location: Mapped[str] = mapped_column(String(64), default="")
    recovery_hash: Mapped[str] = mapped_column(String(64))
    llm_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # off for tester households (T2.9)
    log_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float)
    deleted_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    viewers: Mapped[list["Viewer"]] = relationship(back_populates="household", cascade="all, delete-orphan")
    stations: Mapped[list["Station"]] = relationship(back_populates="household", cascade="all, delete-orphan")


class Viewer(Base):
    __tablename__ = "viewers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    household_id: Mapped[str] = mapped_column(String(32), ForeignKey("households.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(64), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mode_ceiling: Mapped[str] = mapped_column(String(16), default="full")  # enforced in Phase 3
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    last_seen: Mapped[float | None] = mapped_column(Float, nullable=True)

    household: Mapped[Household] = relationship(back_populates="viewers")


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    household_id: Mapped[str] = mapped_column(String(32), ForeignKey("households.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    dog_name: Mapped[str | None] = mapped_column(String(64), nullable=True)  # a label for this camera
    pubkey_spki: Mapped[bytes] = mapped_column(LargeBinary)  # ECDSA P-256 public key, DER SPKI
    created_at: Mapped[float] = mapped_column(Float)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_seen: Mapped[float | None] = mapped_column(Float, nullable=True)

    household: Mapped[Household] = relationship(back_populates="stations")


class PairingCode(Base):
    __tablename__ = "pairing_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(32), ForeignKey("households.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    used_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by_viewer: Mapped[str | None] = mapped_column(String(32), ForeignKey("viewers.id"), nullable=True)
    mode_ceiling: Mapped[str | None] = mapped_column(String(16), nullable=True)
    viewer_label: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppSetting(Base):
    """Operator settings that must survive a switch between hosts (e.g. the admin bandwidth overrides).

    One row per setting. `value` is JSON text, so the schema does not change when a setting's shape does.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[float] = mapped_column(Float)
