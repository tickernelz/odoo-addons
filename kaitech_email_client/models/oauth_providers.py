# -*- coding: utf-8 -*-
"""OAuth2 endpoints for Gmail / Google Workspace and Microsoft 365.

Both providers expose plain IMAP and SMTP with the XOAUTH2 SASL mechanism, so
the same sync engine serves every account type; only authentication differs.
"""
import base64
import json
import time
from urllib.parse import urlencode

import requests

from .compat import get_param

P = "kaitech_email_client."

PROVIDERS = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scope": "openid email https://mail.google.com/",
        "extra": {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
        "imap": ("imap.gmail.com", 993, "ssl"),
        "smtp": ("smtp.gmail.com", 587, "starttls"),
    },
    "microsoft": {
        "auth_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "scope": "openid email offline_access "
                 "https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send",
        "extra": {"prompt": "select_account"},
        "imap": ("outlook.office365.com", 993, "ssl"),
        "smtp": ("smtp.office365.com", 587, "starttls"),
    },
}


class OAuthError(Exception):
    pass


def _app(env, provider):
    client_id = get_param(env, P + provider + "_client_id")
    secret = get_param(env, P + provider + "_client_secret")
    if not client_id or not secret:
        raise OAuthError("missing_app")
    tenant = (get_param(env, P + "microsoft_tenant") or "common") if provider == "microsoft" else ""
    return client_id, secret, tenant


def redirect_uri(env):
    return env["ir.config_parameter"].sudo().get_base_url().rstrip("/") + "/umc/oauth/callback"


def authorization_url(env, provider, state, login_hint=None):
    conf = PROVIDERS[provider]
    client_id, _secret, tenant = _app(env, provider)
    params = dict(conf["extra"], client_id=client_id, response_type="code", scope=conf["scope"],
                  redirect_uri=redirect_uri(env), state=state)
    if login_hint:
        params["login_hint"] = login_hint
    return conf["auth_url"].format(tenant=tenant) + "?" + urlencode(params)


def _token_request(env, provider, data):
    conf = PROVIDERS[provider]
    client_id, secret, tenant = _app(env, provider)
    data = dict(data, client_id=client_id, client_secret=secret)
    if provider == "microsoft":
        data["scope"] = conf["scope"]
    resp = requests.post(conf["token_url"].format(tenant=tenant), data=data, timeout=20)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200 or "access_token" not in body:
        raise OAuthError(body.get("error_description") or body.get("error") or "HTTP %s" % resp.status_code)
    body["expires_at"] = int(time.time()) + int(body.get("expires_in") or 3600) - 60
    return body


def exchange_code(env, provider, code):
    return _token_request(env, provider, {"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": redirect_uri(env)})


def refresh(env, provider, refresh_token):
    return _token_request(env, provider, {"grant_type": "refresh_token", "refresh_token": refresh_token})


def email_from_id_token(id_token):
    """Address of the signed-in account. The id_token comes straight from the provider's
    token endpoint over TLS, so its claims are read without re-validating the signature."""
    try:
        part = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return ""
    return (claims.get("email") or claims.get("preferred_username") or "").strip().lower()


def xoauth2(email, access_token):
    return "user=%s\x01auth=Bearer %s\x01\x01" % (email, access_token)
