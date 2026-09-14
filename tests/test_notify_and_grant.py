"""Offline tests for the only outbound module and for the consent re-grant.

Two things here are load bearing rather than decorative. The recipient lock is
the mechanism behind notify.py's promise that it can only reach Krish. And the
grant's scope check is what stops a consent that adds gmail.send but silently
drops spreadsheets, which would break the sheet writer with no visible error
until the next scheduled run.
"""
from __future__ import annotations

import base64

import pytest

from hunter import notify, oauth_grant
from hunter.notify import NotifyError


class FakeCfg:
    raw: dict = {}

    def require(self, key):
        return {"hunter_google_oauth_client_id": "cid",
                "hunter_google_oauth_client_secret": "secret",
                "hunter_google_oauth_refresh_token": "refresh"}[key]


# ---------------- the recipient lock ----------------

@pytest.mark.parametrize("addr", ["hello@krishraja.com", "krishanraja@gmail.com",
                                  "Hello@KrishRaja.com  "])
def test_krishs_own_addresses_are_accepted(addr):
    raw = notify.build_message(addr, "s", "<p>hi</p>")
    assert base64.urlsafe_b64decode(raw + "==")


@pytest.mark.parametrize("addr", [
    "recruiter@openai.com",
    "jobs@cloudflare.com",
    "hello@krishraja.com.evil.test",
    "",
])
def test_every_other_address_is_refused(addr):
    with pytest.raises(NotifyError, match="can only reach Krish"):
        notify.build_message(addr, "s", "<p>hi</p>")


def test_send_email_refuses_a_foreign_recipient_before_any_request(monkeypatch):
    """The check must happen before a token is minted or a socket is opened."""
    def explode(*a, **k):
        raise AssertionError("send_email reached the network for a bad address")

    monkeypatch.setattr(notify.requests, "post", explode)
    monkeypatch.setattr(notify, "GoogleOAuth", explode)
    with pytest.raises(NotifyError, match="can only reach Krish"):
        notify.send_email(FakeCfg(), "s", "<p>x</p>", to="hr@example.com")


# ---------------- message encoding ----------------

def test_the_message_is_base64url_not_standard_base64():
    """The Gmail API rejects + and / in raw, so the encoding is not a detail."""
    raw = notify.build_message("hello@krishraja.com", "Subject",
                               "<p>" + "ÿ" * 200 + "</p>")
    assert "+" not in raw and "/" not in raw
    decoded = base64.urlsafe_b64decode(raw + "===").decode("utf-8", "replace")
    assert "Subject" in decoded


def test_an_em_dash_never_leaves_the_building():
    # Escaped on purpose: a literal one here trips the repo guard.
    em = "\u2014"
    with pytest.raises(NotifyError, match="em dash"):
        notify.build_message("hello@krishraja.com", f"a {em} b", "<p>x</p>")
    with pytest.raises(NotifyError, match="em dash"):
        notify.build_message("hello@krishraja.com", "s", f"<p>a {em} b</p>")


def test_a_plain_text_alternative_is_always_present():
    raw = notify.build_message("hello@krishraja.com", "s",
                              "<p>Apply to <b>Harvey</b></p>")
    body = base64.urlsafe_b64decode(raw + "===").decode()
    assert "text/plain" in body and "text/html" in body


# ---------------- graceful degradation before the grant ----------------

def test_a_missing_scope_falls_back_to_stdout_instead_of_failing(monkeypatch, capsys):
    class R:
        status_code = 403
        text = ('{"error":{"details":[{"reason":'
                '"ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}')

    monkeypatch.setattr(notify, "GoogleOAuth",
                        lambda cfg: type("T", (), {"access_token": lambda s: "t"})())
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: R())
    out = notify.send_email(FakeCfg(), "Approve: Harvey", "<p>body</p>")
    assert out["sent"] is False and "not granted" in out["reason"]
    printed = capsys.readouterr().out
    assert "hunter.oauth_grant" in printed, "the fallback must say how to fix it"
    assert "body" in printed, "and must still show the content"


def test_a_real_failure_still_raises(monkeypatch):
    class R:
        status_code = 500
        text = "upstream exploded"

    monkeypatch.setattr(notify, "GoogleOAuth",
                        lambda cfg: type("T", (), {"access_token": lambda s: "t"})())
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: R())
    with pytest.raises(NotifyError, match="gmail send failed"):
        notify.send_email(FakeCfg(), "s", "<p>x</p>")


# ---------------- the grant's scope safety net ----------------

def test_all_four_scopes_present_is_the_only_passing_case():
    assert oauth_grant.missing_scopes(" ".join(oauth_grant.REQUIRED_SCOPES)) == []


def test_a_grant_missing_gmail_send_is_caught():
    granted = f"{oauth_grant.DOCS} {oauth_grant.DRIVE} {oauth_grant.SHEETS}"
    assert oauth_grant.missing_scopes(granted) == [oauth_grant.GMAIL_SEND]


def test_a_grant_that_silently_drops_spreadsheets_is_caught():
    """The expensive failure: gmail.send arrives, the sheet writer dies quietly."""
    granted = f"{oauth_grant.DOCS} {oauth_grant.DRIVE} {oauth_grant.GMAIL_SEND}"
    assert oauth_grant.missing_scopes(granted) == [oauth_grant.SHEETS]


def test_scope_order_and_extra_scopes_do_not_matter():
    granted = ("https://www.googleapis.com/auth/userinfo.email "
               + " ".join(reversed(oauth_grant.REQUIRED_SCOPES)))
    assert oauth_grant.missing_scopes(granted) == []


def test_an_empty_scope_string_reports_everything_missing():
    assert oauth_grant.missing_scopes("") == list(oauth_grant.REQUIRED_SCOPES)


def test_the_auth_url_asks_for_offline_consent_and_all_four_scopes():
    """Without access_type=offline and prompt=consent Google issues no refresh
    token at all, which is the most common way this flow silently fails."""
    import urllib.parse
    url = oauth_grant.auth_url("cid", "http://localhost:9/", "st")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    assert set(q["scope"][0].split()) == set(oauth_grant.REQUIRED_SCOPES)
    assert q["state"] == ["st"]
    assert q["redirect_uri"] == ["http://localhost:9/"]


def test_exchange_without_a_refresh_token_explains_the_cause(monkeypatch):
    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "a"}  # no refresh_token

    monkeypatch.setattr(oauth_grant.requests, "post", lambda *a, **k: R())
    with pytest.raises(oauth_grant.GrantError, match="prompt=consent"):
        oauth_grant.exchange("cid", "secret", "code", "http://localhost:9/")


def test_verify_refuses_to_write_when_a_scope_is_missing(monkeypatch):
    """verify() raising is what keeps the working token in place."""
    class Post:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "a"}

    class Get:
        status_code = 200

        @staticmethod
        def json():
            return {"scope": f"{oauth_grant.DOCS} {oauth_grant.DRIVE} "
                             f"{oauth_grant.GMAIL_SEND}"}

    monkeypatch.setattr(oauth_grant.requests, "post", lambda *a, **k: Post())
    monkeypatch.setattr(oauth_grant.requests, "get", lambda *a, **k: Get())
    with pytest.raises(oauth_grant.GrantError, match="spreadsheets"):
        oauth_grant.verify(FakeCfg(), "newtoken")
