# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import os

PREFIX = 'o10mcp_'
INDEX_LENGTH = 8
SECRET_BYTES = 32


def new_token():
    secret = base64.urlsafe_b64encode(os.urandom(SECRET_BYTES)).rstrip(b'=')
    return PREFIX + secret.decode('ascii')


def digest(token):
    if not isinstance(token, bytes):
        token = token.encode('utf-8')
    return hashlib.sha256(token).hexdigest()


def index_of(token):
    """Lookup index of a token, or None when it does not look like an MCP key."""
    if not token or not token.startswith(PREFIX):
        return None
    secret = token[len(PREFIX):]
    if len(secret) < INDEX_LENGTH * 2:
        return None
    return secret[:INDEX_LENGTH]


def _compare(left, right):
    if len(left) != len(right):
        return False
    outcome = 0
    for a, b in zip(bytearray(left), bytearray(right)):
        outcome |= a ^ b
    return outcome == 0


same_digest = getattr(hmac, 'compare_digest', None) or _compare


def matches(token, stored_digest):
    if not stored_digest:
        return False
    return same_digest(digest(token).encode('ascii'), stored_digest.encode('ascii'))
