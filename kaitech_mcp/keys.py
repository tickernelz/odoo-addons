import base64
import hashlib
import hmac
import os

PREFIX = 'o19mcp_'
INDEX_LENGTH = 8
SECRET_BYTES = 32


def new_token():
    secret = base64.urlsafe_b64encode(os.urandom(SECRET_BYTES)).rstrip(b'=')
    return PREFIX + secret.decode('ascii')


def digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def index_of(token):
    """Lookup index of a token, or None when it does not look like an MCP key."""
    if not token or not token.startswith(PREFIX):
        return None
    secret = token[len(PREFIX):]
    if len(secret) < INDEX_LENGTH * 2:
        return None
    return secret[:INDEX_LENGTH]


def matches(token, stored_digest):
    if not stored_digest:
        return False
    return hmac.compare_digest(digest(token), stored_digest)
