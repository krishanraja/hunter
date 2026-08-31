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

def db_get(cfg: Config, table: str, params: dict[str, str]) -> list[dict]:
    r = requests.get(f"{cfg.supabase_url}/rest/v1/{table}",
                     headers=_rest_headers(cfg), params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def db_insert(cfg: Config, table: str, rows: list[dict], *,
              on_conflict: str | None = None, ignore_duplicates: bool = False) -> None:
    if not rows:
        return
    headers = dict(_rest_headers(cfg))
    prefer = ["return=minimal"]
    if ignore_duplicates:
        prefer.append("resolution=ignore-duplicates")
    headers["Prefer"] = ",".join(prefer)
    params = {"on_conflict": on_conflict} if on_conflict else {}
    r = requests.post(f"{cfg.supabase_url}/rest/v1/{table}",
                      headers=headers, params=params, json=rows, timeout=60)
    r.raise_for_status()


def db_patch(cfg: Config, table: str, match: dict[str, str], values: dict) -> None:
    params = {k: f"eq.{v}" for k, v in match.items()}
    headers = dict(_rest_headers(cfg))
    headers["Prefer"] = "return=minimal"
    r = requests.patch(f"{cfg.supabase_url}/rest/v1/{table}",
                       headers=headers, params=params, json=values, timeout=60)
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
