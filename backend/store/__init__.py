"""Household storage (T2.1): models, engine helpers, and the hashing rules for secrets.

Importing this package registers the models on `db.Base`, so `db.init_db(engine)` creates their
tables. The default store is SQLite (`sqlite:///out/btb.db`, override with `BTB_DATABASE_URL`); every
secret (viewer token, pairing code, recovery key) is stored as a SHA-256 hash and compared with
`hmac.compare_digest`, so a leaked database file does not hand out sessions.
"""

from backend.store import models  # noqa: F401 - registers the tables on Base
from backend.store.db import Base, make_engine, init_db, normalize_db_url  # noqa: F401 - the public engine helpers
from backend.store.storage import SnapshotStorage, EphemeralStorage, R2SnapshotStorage, build_storage  # noqa: F401
