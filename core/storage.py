"""Where each client's work is saved, so nothing is lost when the browser
tab closes or the app restarts.

Two stores with the same methods:
  PostgresStore  the real one (a private Supabase/Postgres database, set by
                 DATABASE_URL in Streamlit secrets)
  MemoryStore    used when no database is set up (and by the tests); work
                 lives only as long as the browser session, and the app says so

What is saved per client is only the source of truth (see core/workpaper.py):
name, tax year, thresholds, the AI's documents with any hand corrections,
the log of hand corrections, and the client answers. Everything else is
rebuilt by rules on every page load.

Files waiting to be read are saved too (their bytes, and the AI's answer for
every part already read), so reading can stop and pick up where it left off.
A file's bytes are deleted as soon as it has been read successfully.
"""
import copy
import json
import math
import re
import threading
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

SCHEMA = "schedule_c"
CLIENT_FIELDS = ("client_name", "tax_year", "de_minimis_threshold", "materiality_threshold",
                 "files", "manual_edits", "answered_questions", "duplicates_flagged")


class StorageError(Exception):
    """Same shape as AIExtractionError: a code, a plain message, and a fix."""

    def __init__(self, code: str, message: str, fix: str, detail: str = ""):
        super().__init__(message)
        self.code, self.message, self.fix, self.detail = code, message, fix, detail

    def __str__(self) -> str:
        return f"[{self.code}] {self.message} What to do: {self.fix}"


def to_json_safe(value: Any) -> Any:
    """Plain JSON types only. Numbers from pandas/numpy become Python
    numbers, dates become "YYYY-MM-DD" text, and NaN/infinity become empty
    (Postgres refuses NaN inside JSON)."""
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        value = float(value)
    if hasattr(value, "item") and not isinstance(value, (int, float)):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, int):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        if value != value:  # pandas NA / NaT
            return None
    except (TypeError, ValueError):
        return None
    return str(value)


def client_record(state: Dict[str, Any]) -> Dict[str, Any]:
    """Only the fields that are saved; serialised and read back so what the
    app keeps in memory is exactly what the database would give back."""
    record = {field: state.get(field) for field in CLIENT_FIELDS}
    return json.loads(json.dumps(to_json_safe(record), allow_nan=False))


def new_client_state(client_name: str = "", tax_year: int = 2025) -> Dict[str, Any]:
    return {"client_name": client_name, "tax_year": tax_year, "de_minimis_threshold": 2500.0,
            "materiality_threshold": 0.0, "files": {}, "manual_edits": [], "answered_questions": [],
            "duplicates_flagged": []}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    """Keeps everything in this Python process only."""

    is_saved = False

    def __init__(self):
        self._lock = threading.Lock()
        self._clients: Dict[str, Dict[str, Any]] = {}
        self._uploads: Dict[tuple, Dict[str, Any]] = {}
        self._parts: Dict[tuple, Dict[int, Any]] = {}

    def list_clients(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [{"client_id": cid, "client_name": c["state"]["client_name"],
                     "tax_year": c["state"]["tax_year"], "updated_at": c["updated_at"]}
                    for cid, c in self._clients.items()]
        return sorted(rows, key=lambda r: r["updated_at"], reverse=True)

    def create_client(self, client_name: str, tax_year: int) -> str:
        client_id = _new_id()
        self.save_client(client_id, new_client_state(client_name, tax_year))
        return client_id

    def load_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            found = self._clients.get(client_id)
            return copy.deepcopy(found["state"]) if found else None

    def save_client(self, client_id: str, state: Dict[str, Any]) -> None:
        record = client_record(state)
        with self._lock:
            self._clients[client_id] = {"state": record, "updated_at": _now()}

    def delete_client(self, client_id: str) -> None:
        with self._lock:
            self._clients.pop(client_id, None)
            for key in [k for k in self._uploads if k[0] == client_id]:
                self._uploads.pop(key, None)
                self._parts.pop(key, None)

    def add_upload(self, client_id: str, filename: str, raw_bytes: bytes, part_count: int) -> None:
        with self._lock:
            self._uploads[(client_id, filename)] = {"raw_bytes": bytes(raw_bytes), "size": len(raw_bytes),
                                                    "failed": False, "part_count": part_count,
                                                    "added_at": _now()}

    def uploads(self, client_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [{"filename": f, "size": u["size"], "failed": u["failed"], "part_count": u["part_count"],
                     "added_at": u["added_at"]}
                    for (cid, f), u in self._uploads.items() if cid == client_id]
        return sorted(rows, key=lambda r: (r["added_at"], r["filename"]))

    def upload_bytes(self, client_id: str, filename: str) -> Optional[bytes]:
        with self._lock:
            found = self._uploads.get((client_id, filename))
            return found["raw_bytes"] if found else None

    def set_upload_failed(self, client_id: str, filename: str, failed: bool) -> None:
        with self._lock:
            if (client_id, filename) in self._uploads:
                self._uploads[(client_id, filename)]["failed"] = failed

    def delete_upload(self, client_id: str, filename: str) -> None:
        with self._lock:
            self._uploads.pop((client_id, filename), None)
            self._parts.pop((client_id, filename), None)

    def save_part_result(self, client_id: str, filename: str, part_index: int, result: Dict[str, Any]) -> None:
        safe = json.loads(json.dumps(to_json_safe(result), allow_nan=False))
        with self._lock:
            if (client_id, filename) not in self._uploads:
                return  # the file was removed while this part was being read
            self._parts.setdefault((client_id, filename), {})[part_index] = safe

    def done_parts(self, client_id: str) -> Dict[str, Set[int]]:
        with self._lock:
            return {f: set(parts) for (cid, f), parts in self._parts.items() if cid == client_id}

    def part_results(self, client_id: str, filename: str) -> Dict[int, Dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._parts.get((client_id, filename), {}))

    def delete_part_result(self, client_id: str, filename: str, part_index: int) -> None:
        with self._lock:
            self._parts.get((client_id, filename), {}).pop(part_index, None)


_CREATE_TABLES = """
create schema if not exists {schema};
create table if not exists {schema}.clients (
    client_id   text primary key,
    client_name text not null default '',
    tax_year    integer,
    state       jsonb not null,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create table if not exists {schema}.pending_files (
    client_id   text not null references {schema}.clients(client_id) on delete cascade,
    filename    text not null,
    raw_bytes   bytea not null,
    size        integer not null,
    part_count  integer not null,
    failed      boolean not null default false,
    added_at    timestamptz not null default now(),
    primary key (client_id, filename)
);
create table if not exists {schema}.part_results (
    client_id   text not null,
    filename    text not null,
    part_index  integer not null,
    result      jsonb not null,
    saved_at    timestamptz not null default now(),
    primary key (client_id, filename, part_index),
    foreign key (client_id, filename) references {schema}.pending_files(client_id, filename) on delete cascade
);
alter table {schema}.clients enable row level security;
alter table {schema}.pending_files enable row level security;
alter table {schema}.part_results enable row level security;
"""


def _connection_error(exc: Exception, secrets: List[str]) -> StorageError:
    detail = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, "<hidden>")
    return StorageError(
        "DATABASE_UNREACHABLE", "The app can't reach the database where work is saved.",
        "Check that DATABASE_URL in the app's secrets is the Supabase 'Session pooler' connection "
        "string with the right password, and that the Supabase project isn't paused.", detail[:500])


class PostgresStore:
    """A private Postgres database (Supabase). Tables live in their own
    schema, which Supabase does not expose through its public web API, and
    have row level security on with no policies, so only this app's own
    database login can read them."""

    is_saved = True

    def __init__(self, url: str, schema: str = SCHEMA):
        from urllib.parse import urlsplit
        from psycopg_pool import ConnectionPool

        try:
            password = urlsplit(url).password or ""
        except ValueError:
            password = ""
        self._secrets = [url, password]
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
            raise ValueError("schema must be a plain lowercase name")
        self._schema = schema
        try:
            self._pool = ConnectionPool(
                url, min_size=1, max_size=6, open=True, timeout=30,
                kwargs={"autocommit": True, "prepare_threshold": None, "connect_timeout": 15},
                check=ConnectionPool.check_connection)
            with self._pool.connection() as conn:
                conn.execute(_CREATE_TABLES.format(schema=self._schema))
        except Exception as exc:
            raise _connection_error(exc, self._secrets) from None

    def _run(self, sql: str, params: tuple = (), fetch: str = ""):
        try:
            with self._pool.connection() as conn:
                cursor = conn.execute(sql, params)
                if fetch == "one":
                    return cursor.fetchone()
                if fetch == "all":
                    return cursor.fetchall()
                return None
        except Exception as exc:
            raise _connection_error(exc, self._secrets) from None

    def list_clients(self) -> List[Dict[str, Any]]:
        rows = self._run(f"select client_id, client_name, tax_year, updated_at from {self._schema}.clients "
                         "order by updated_at desc", fetch="all")
        return [{"client_id": r[0], "client_name": r[1], "tax_year": r[2], "updated_at": r[3]} for r in rows]

    def create_client(self, client_name: str, tax_year: int) -> str:
        client_id = _new_id()
        self.save_client(client_id, new_client_state(client_name, tax_year))
        return client_id

    def load_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        row = self._run(f"select state from {self._schema}.clients where client_id = %s", (client_id,), fetch="one")
        return row[0] if row else None

    def save_client(self, client_id: str, state: Dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        record = client_record(state)
        self._run(
            f"insert into {self._schema}.clients (client_id, client_name, tax_year, state) values (%s, %s, %s, %s) "
            "on conflict (client_id) do update set client_name = excluded.client_name, "
            "tax_year = excluded.tax_year, state = excluded.state, updated_at = now()",
            (client_id, record["client_name"] or "", record["tax_year"], Jsonb(record)))

    def delete_client(self, client_id: str) -> None:
        self._run(f"delete from {self._schema}.clients where client_id = %s", (client_id,))

    def add_upload(self, client_id: str, filename: str, raw_bytes: bytes, part_count: int) -> None:
        self._run(
            f"insert into {self._schema}.pending_files (client_id, filename, raw_bytes, size, part_count) "
            "values (%s, %s, %s, %s, %s) on conflict (client_id, filename) do update set "
            "raw_bytes = excluded.raw_bytes, size = excluded.size, part_count = excluded.part_count, "
            "failed = false",
            (client_id, filename, bytes(raw_bytes), len(raw_bytes), part_count))

    def uploads(self, client_id: str) -> List[Dict[str, Any]]:
        rows = self._run(f"select filename, size, failed, part_count, added_at from {self._schema}.pending_files "
                         "where client_id = %s order by added_at, filename", (client_id,), fetch="all")
        return [{"filename": r[0], "size": r[1], "failed": r[2], "part_count": r[3], "added_at": r[4]}
                for r in rows]

    def upload_bytes(self, client_id: str, filename: str) -> Optional[bytes]:
        row = self._run(f"select raw_bytes from {self._schema}.pending_files where client_id = %s and filename = %s",
                        (client_id, filename), fetch="one")
        return bytes(row[0]) if row else None

    def set_upload_failed(self, client_id: str, filename: str, failed: bool) -> None:
        self._run(f"update {self._schema}.pending_files set failed = %s where client_id = %s and filename = %s",
                  (failed, client_id, filename))

    def delete_upload(self, client_id: str, filename: str) -> None:
        self._run(f"delete from {self._schema}.pending_files where client_id = %s and filename = %s",
                  (client_id, filename))

    def save_part_result(self, client_id: str, filename: str, part_index: int, result: Dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        safe = json.loads(json.dumps(to_json_safe(result), allow_nan=False))
        # Inserted only while the file is still waiting; a part that finishes
        # after its file was removed is simply dropped.
        self._run(
            f"insert into {self._schema}.part_results (client_id, filename, part_index, result) "
            f"select %s, %s, %s, %s where exists (select 1 from {self._schema}.pending_files "
            "where client_id = %s and filename = %s) "
            "on conflict (client_id, filename, part_index) do update set result = excluded.result, saved_at = now()",
            (client_id, filename, part_index, Jsonb(safe), client_id, filename))

    def done_parts(self, client_id: str) -> Dict[str, Set[int]]:
        rows = self._run(f"select filename, part_index from {self._schema}.part_results where client_id = %s",
                         (client_id,), fetch="all")
        found: Dict[str, Set[int]] = {}
        for filename, index in rows:
            found.setdefault(filename, set()).add(index)
        return found

    def part_results(self, client_id: str, filename: str) -> Dict[int, Dict[str, Any]]:
        rows = self._run(f"select part_index, result from {self._schema}.part_results "
                         "where client_id = %s and filename = %s", (client_id, filename), fetch="all")
        return {r[0]: r[1] for r in rows}

    def delete_part_result(self, client_id: str, filename: str, part_index: int) -> None:
        self._run(f"delete from {self._schema}.part_results where client_id = %s and filename = %s and part_index = %s",
                  (client_id, filename, part_index))
