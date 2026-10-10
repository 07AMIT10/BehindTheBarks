"""The store API (T2.1): plain functions taking a `Session`, no FastAPI imports.

Consuming a pairing code is one atomic `UPDATE ... WHERE used_at IS NULL AND expires_at > now`, so
two racing clients can never both win it. Nothing here logs a plaintext secret.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.store.db import equal_digest, new_id, new_pairing_code, new_token, sha256_hex
from backend.store.models import KIND_STATION, ROLE_OWNER, Household, PairingCode, Station, Viewer

PAIRING_TTL_S = 300.0


def _now() -> float:  # monkeypatched by the tests to expire things deterministically
    return time.time()


# -- households --------------------------------------------------------------------------------
def create_household(session: Session, *, name: str = "", dog_name: str = "Your dog", location: str = "",
                     recovery_key: str | None = None, llm_enabled: bool = False, log_enabled: bool = False,
                     now: float | None = None) -> tuple[Household, str]:
    """The caller shows the returned `recovery_key` once; only its hash is stored."""
    now = now if now is not None else _now()
    key = recovery_key if recovery_key is not None else new_token()
    h = Household(id=new_id(), name=name, dog_name=dog_name, location=location,
                  recovery_hash=sha256_hex(key), llm_enabled=llm_enabled,
                  log_enabled=log_enabled, created_at=now)
    session.add(h)
    return h, key


def get_household(session: Session, household_id: str) -> Household | None:
    return session.get(Household, household_id)


def recover_household(session: Session, recovery_key: str) -> tuple[Viewer, str] | None:
    """Trade a recovery key for a fresh owner session. None (not an error) for an unknown key."""
    for h in session.execute(select(Household)).scalars():
        if equal_digest(h.recovery_hash, sha256_hex(recovery_key)):
            return create_viewer(session, household_id=h.id, role=ROLE_OWNER, label="recovered")
    return None


def delete_household(session: Session, household_id: str) -> bool:
    """Hard delete (the user's data-erasure path): children first, so it works without FK pragmas."""
    h = get_household(session, household_id)
    if h is None:
        return False
    for row in list(h.viewers) + list(h.stations):
        session.delete(row)
    session.query(PairingCode).filter(PairingCode.household_id == household_id).delete()
    session.delete(h)
    session.commit()  # an erase is durable the moment it is requested
    return True


def update_household_profile(session: Session, household_id: str, *, dog_name: str | None = None,
                             location: str | None = None) -> Household | None:
    h = get_household(session, household_id)
    if h is None:
        return None
    if dog_name is not None:
        h.dog_name = dog_name[:64].strip()
    if location is not None:
        h.location = location[:64].strip()
    return h


def update_household_settings(session: Session, household_id: str, *, llm_enabled: bool | None = None,
                              log_enabled: bool | None = None) -> Household | None:
    h = get_household(session, household_id)
    if h is None:
        return None
    if llm_enabled is not None:
        h.llm_enabled = llm_enabled
    if log_enabled is not None:
        h.log_enabled = log_enabled
    return h


# -- viewers -----------------------------------------------------------------------------------
def create_viewer(session: Session, *, household_id: str, role: str, label: str = "",
                  mode_ceiling: str = "full", expires_at: float | None = None,
                  now: float | None = None) -> tuple[Viewer, str]:
    """Returns the row and the plaintext token: the caller shows it once, it is never stored."""
    now = now if now is not None else _now()
    token = new_token()
    v = Viewer(id=new_id(), household_id=household_id, role=role, label=label,
               token_hash=sha256_hex(token), mode_ceiling=mode_ceiling, expires_at=expires_at,
               created_at=now)
    session.add(v)
    return v, token


def viewer_by_token(session: Session, token: str) -> Viewer | None:
    """Hash lookup, then the revoked/expired checks. `token` is never logged."""
    if not token:
        return None
    v = session.execute(select(Viewer).where(Viewer.token_hash == sha256_hex(token))).scalar_one_or_none()
    if v is None or v.revoked_at is not None:
        return None
    if v.expires_at is not None and v.expires_at < _now():
        return None
    return v


def viewers_of_household(session: Session, household_id: str) -> list[Viewer]:
    return list(session.execute(select(Viewer).where(Viewer.household_id == household_id)).scalars())


def revoke_viewer(session: Session, viewer_id: str, now: float | None = None) -> Viewer | None:
    v = session.get(Viewer, viewer_id)
    if v is None:
        return None
    v.revoked_at = now if now is not None else _now()
    return v


def touch_viewer(session: Session, viewer: Viewer, now: float | None = None) -> None:
    viewer.last_seen = now if now is not None else _now()


# -- pairing codes -----------------------------------------------------------------------------
def create_pairing_code(session: Session, *, household_id: str, kind: str, created_by_viewer: str | None = None,
                        mode_ceiling: str | None = None, viewer_label: str | None = None,
                        ttl_s: float = PAIRING_TTL_S, now: float | None = None) -> str:
    """Returns the plaintext code; only its hash is stored."""
    now = now if now is not None else _now()
    code = new_pairing_code()
    session.add(PairingCode(code_hash=sha256_hex(code), household_id=household_id, kind=kind,
                            expires_at=now + ttl_s, created_by_viewer=created_by_viewer,
                            mode_ceiling=mode_ceiling, viewer_label=viewer_label))
    return code


def consume_pairing_code(session: Session, code: str, *, kind: str, now: float | None = None) -> PairingCode | None:
    """Atomic single-use consume: the WHERE clause is the guard, the rowcount is the verdict."""
    now = now if now is not None else _now()
    result = session.execute(
        update(PairingCode)
        .where(PairingCode.code_hash == sha256_hex(code), PairingCode.kind == kind,
               PairingCode.used_at.is_(None), PairingCode.expires_at > now)
        .values(used_at=now)
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    return session.execute(select(PairingCode).where(PairingCode.code_hash == sha256_hex(code))).scalar_one()


# -- stations ----------------------------------------------------------------------------------
def create_station(session: Session, *, household_id: str, name: str, pubkey_spki: bytes,
                   dog_name: str | None = None, now: float | None = None) -> Station:
    now = now if now is not None else _now()
    st = Station(id=new_id(), household_id=household_id, name=name[:64].strip(), dog_name=dog_name,
                 pubkey_spki=pubkey_spki, created_at=now)
    session.add(st)
    return st


def station_by_id(session: Session, station_id: str) -> Station | None:
    return session.get(Station, station_id)


def stations_of_household(session: Session, household_id: str) -> list[Station]:
    return list(session.execute(select(Station).where(Station.household_id == household_id)).scalars())


def revoke_station(session: Session, station_id: str, now: float | None = None) -> Station | None:
    st = station_by_id(session, station_id)
    if st is None:
        return None
    st.revoked_at = now if now is not None else _now()
    return st


def touch_station(session: Session, station: Station, now: float | None = None) -> None:
    station.last_seen = now if now is not None else _now()


def expire_pairing_codes(session: Session, older_than_s: float, now: float | None = None) -> int:
    """T2.10: sweep used/expired codes so the table cannot grow without bound."""
    now = now if now is not None else _now()
    deleted = (session.query(PairingCode)
               .filter((PairingCode.expires_at < now - older_than_s) |
                       ((PairingCode.used_at.is_not(None)) & (PairingCode.used_at < now - older_than_s)))
               .delete(synchronize_session=False))
    return int(deleted or 0)


def any_of(session: Session, model: Any) -> int:
    return int(session.query(model).count())
