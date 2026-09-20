"""Runtime configuration. Secrets live in Supabase system_config, never in this repo.

Bootstrap environment, the only env vars hunter reads:
  SUPABASE_URL                the OS project URL
  SUPABASE_SERVICE_ROLE_KEY   service role key for PostgREST
  HUNTER_LIVE=1               optional, enables live tests only

Everything else is a row in public.system_config (text values; JSON payloads
are JSON text parsed with json.loads, never a ::jsonb cast).

Two Google credential planes, never mixed:
  Krish's OAuth (refresh token)  -> Docs and Drive (the masters are owner-only)
  Service account mm-os-gsc      -> Sheets (writer on the workbook)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

# Verified constants, cross-checked against canon 9.9 at every run start
# (canon.load_canon hard-fails on mismatch; hunter never picks a winner).
CV_MASTER_ID = "1ALITQk-d0Mms3InJpk6FMsebxaLnf__QPmUyPAa_NEE"
LETTER_MASTER_ID = "1OD6FIxud8AOicqvC74Yoi7nbDI-aGAl6SCI0OIswyQ4"
WORKBOOK_ID = "1AQ8OyprIyJmJ9K7ezjIxkW0uzjGT0TqzRjKtG-NXNOk"
PIPELINE_SHEET_ID = 708873267
# Krish's ruling 2026-09-02: decided rows leave Pipeline for the existing
# (empty) Applied tab, so Pipeline holds only what still needs a decision.
ARCHIVE_TAB = "Applied"
ARCHIVE_SHEET_ID = 1707095501
CV_FOLDER_ID = "1IMMUrAV7wCb-a_eei6c_fmX2BCDgYA8X"
LETTER_FOLDER_ID = "1YotvbjjE8amkVnrLSAjSASd5-U28D1lJ"

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    supabase_url: str
    supabase_key: str
    raw: dict[str, str] = field(repr=False)

    def require(self, key: str) -> str:
        val = self.raw.get(key, "")
        if not val:
            raise ConfigError(
                f"system_config key {key!r} is missing or empty; "
                f"insert it before running (see the bootstrap step in the plan)"
            )
        return val

    def require_json(self, key: str) -> Any:
        try:
            return json.loads(self.require(key))
        except json.JSONDecodeError as e:
            raise ConfigError(f"system_config key {key!r} is not valid JSON: {e}") from e

    def optional(self, key: str, default: str = "") -> str:
        return self.raw.get(key) or default


def _rest_headers(cfg: Config) -> dict[str, str]:
    return {
        "apikey": cfg.supabase_key,
        "Authorization": "Bearer " + cfg.supabase_key,
        "Content-Type": "application/json",
    }


def load() -> Config:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        raise ConfigError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the environment; "
            "they are the only two bootstrap values hunter reads from env"
        )
    r = requests.get(
        f"{url}/rest/v1/system_config",
        headers={"apikey": key, "Authorization": "Bearer " + key},
        params={"select": "key,value"},
        timeout=30,
    )
    r.raise_for_status()
    raw = {row["key"]: (row["value"] or "") for row in r.json()}
    return Config(supabase_url=url, supabase_key=key, raw=raw)


# ---------- PostgREST helpers, used repo-wide ----------

# PostgREST caps a response at its server-side max-rows (1000 on Supabase),
# whatever `limit` asks for. Every read here passed limit=5000 and quietly got
# 1000, so reconcile's id guard saw 1000 of 1888 job_ids and re-inserted rows
# it already had, seen_identity_keys deduped against a partial view, and the
# learning loop read a truncated history. Nothing errored; the data was just
# incomplete. Page until the server stops giving.
PAGE = 1000

# For a read whose correctness depends on seeing every row: identity matching,
# dedupe, the id guard before an insert. A numeric cap on one of those is a
# silent wrong answer the day the table outgrows it, and hunter_seen_roles
# outgrew 5000 in September while every one of those reads asked for 5000.
# Write ALL_ROWS at the call site rather than a number, so the intent is
# visible and a guard test can check for it.
ALL_ROWS = "1000000"


def _paging_order(params: dict[str, str]) -> str | None:
    """A stable sort for a paged read, when the caller did not give one.

    Offset paging over an unordered query is not a read of the table, it is a
    read of whatever order the planner felt like. Postgres is free to return
    rows differently on every request, so page 2 can repeat a row from page 1
    and omit another entirely, and nothing errors. hunter_seen_roles passed
    5000 rows in September and the symptom was exactly that: a sheet row
    paired with its database row on one run and looked unmatched on the next,
    because the row had simply not been in that read.

    The first selected column is used because it is guaranteed to exist in the
    projection, and the reads that page are keyed reads where it is the
    identifier. A caller wanting a different order passes one.
    """
    select = (params.get("select") or "").strip()
    if not select or select == "*" or "(" in select:
        return "id.asc"
    first = select.split(",")[0].strip()
    return f"{first}.asc" if first else "id.asc"


def db_get(cfg: Config, table: str, params: dict[str, str]) -> list[dict]:
    want = int(params.get("limit") or 0) or None
    out: list[dict] = []
    offset = 0
    seen_ordered = False
    while True:
        page_size = PAGE if want is None else min(PAGE, want - len(out))
        if page_size <= 0:
            break
        headers = dict(_rest_headers(cfg))
        headers["Range-Unit"] = "items"
        headers["Range"] = f"{offset}-{offset + page_size - 1}"
        query = {k: v for k, v in params.items() if k != "limit"}
        # Only once a second page is needed, so a single-page read keeps
        # whatever order the caller was relying on.
        if offset and not params.get("order") and not seen_ordered:
            order = _paging_order(params)
            if order:
                query["order"] = order
                seen_ordered = True
                # The first page was unordered, so it is not the first page of
                # this ordering. Read the whole thing again, in order.
                out, offset = [], 0
                headers["Range"] = f"0-{page_size - 1}"
        elif seen_ordered:
            query["order"] = _paging_order(params)
        r = requests.get(f"{cfg.supabase_url}/rest/v1/{table}",
                         headers=headers, params=query, timeout=60)
        r.raise_for_status()
        page = r.json()
        out.extend(page)
        if len(page) < page_size:
            break
        offset += len(page)
    return out


# How many rows go in one POST. A single request carrying every row of a
# sourcing run answered 500 on 2026-09-20: 265 a16z boards had been swept
# instead of 8, and every row now carries the posting text it was scored
# from, so one insert was tens of megabytes of JSON. Thirty minutes of
# sweeping was thrown away at the last step because the write was one
# indivisible thing. Batching also means partial progress survives.
INSERT_BATCH = 300


def db_insert(cfg: Config, table: str, rows: list[dict], *,
              on_conflict: str | None = None, ignore_duplicates: bool = False,
              merge: bool = False) -> None:
    if not rows:
        return
    # PostgREST bulk inserts demand identical keys on every object (PGRST102).
    # Fill gaps with explicit NULLs; keys absent from the whole batch stay
    # absent, so column defaults still apply.
    all_keys = sorted({k for row in rows for k in row})
    rows = [{k: row.get(k) for k in all_keys} for row in rows]
    if on_conflict:
        # Postgres refuses an upsert whose batch names the same conflict key
        # twice: "ON CONFLICT DO UPDATE command cannot affect row a second
        # time". The a16z portfolio sweep hit it on every run, because the
        # board lists a handful of companies under one slug, and the whole
        # leg was being skipped with the error printed as a summary line
        # nobody read. The last one wins, which matches what a second upsert of the
        # same key would have done anyway.
        cols = [c.strip() for c in on_conflict.split(",") if c.strip()]
        if cols:
            deduped: dict[tuple, dict] = {}
            for row in rows:
                deduped[tuple(row.get(c) for c in cols)] = row
            if len(deduped) != len(rows):
                rows = list(deduped.values())
    headers = dict(_rest_headers(cfg))
    prefer = ["return=minimal"]
    if merge:
        prefer.append("resolution=merge-duplicates")
    elif ignore_duplicates:
        prefer.append("resolution=ignore-duplicates")
    headers["Prefer"] = ",".join(prefer)
    params = {"on_conflict": on_conflict} if on_conflict else {}
    url = f"{cfg.supabase_url}/rest/v1/{table}"
    for start in range(0, len(rows), INSERT_BATCH):
        chunk = rows[start:start + INSERT_BATCH]
        r = requests.post(url, headers=headers, params=params, json=chunk,
                          timeout=120)
        if r.status_code >= 400:
            # raise_for_status alone says "500 Server Error" and nothing about
            # which write, how big it was, or what the server objected to.
            raise requests.HTTPError(
                f"{r.status_code} writing rows {start} to {start + len(chunk)} "
                f"of {len(rows)} into {table} "
                f"({len(chunk)} row(s), {len(str(chunk)) // 1024}KB): "
                f"{r.text[:300]}", response=r)


def db_patch(cfg: Config, table: str, match: dict[str, str], values: dict) -> None:
    params = {k: f"eq.{v}" for k, v in match.items()}
    headers = dict(_rest_headers(cfg))
    headers["Prefer"] = "return=minimal"
    r = requests.patch(f"{cfg.supabase_url}/rest/v1/{table}",
                       headers=headers, params=params, json=values, timeout=60)
    r.raise_for_status()


def db_delete(cfg: Config, table: str, params: dict[str, str]) -> None:
    """PostgREST delete by filter. params are raw filters (eq., in.(...))
    and must be non-empty: an unfiltered delete is never what anyone meant."""
    if not params:
        raise ValueError("refusing to delete without a filter")
    headers = dict(_rest_headers(cfg))
    headers["Prefer"] = "return=minimal"
    r = requests.delete(f"{cfg.supabase_url}/rest/v1/{table}",
                        headers=headers, params=params, timeout=60)
    r.raise_for_status()


# ---------- Google credential planes ----------

class GoogleOAuth:
    """Krish's OAuth plane: Docs and Drive. Access tokens minted from the
    refresh token in system_config, cached in process, refreshed early."""

    def __init__(self, cfg: Config):
        self._client_id = cfg.require("hunter_google_oauth_client_id")
        self._client_secret = cfg.require("hunter_google_oauth_client_secret")
        self._refresh_token = cfg.require("hunter_google_oauth_refresh_token")
        self._token: str | None = None
        self._expires_at = 0.0

    def access_token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token
        r = requests.post(OAUTH_TOKEN_URL, data={
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }, timeout=30)
        if r.status_code != 200:
            err = r.json().get("error", "unknown") if r.headers.get(
                "content-type", "").startswith("application/json") else r.text[:100]
            raise ConfigError(
                f"Google OAuth refresh failed ({r.status_code}, {err}); "
                f"if the token was revoked, paste a new refresh token into "
                f"system_config key hunter_google_oauth_refresh_token"
            )
        j = r.json()
        self._token = j["access_token"]
        self._expires_at = time.time() + float(j.get("expires_in", 3600))
        return self._token


class GoogleServiceAccount:
    """Service account plane: Sheets only."""

    def __init__(self, cfg: Config):
        self._info = cfg.require_json("hunter_google_service_account_json")
        self._creds = None

    def access_token(self) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        if self._creds is None:
            self._creds = service_account.Credentials.from_service_account_info(
                self._info, scopes=[SHEETS_SCOPE])
        if not self._creds.valid:
            self._creds.refresh(Request())
        return self._creds.token
