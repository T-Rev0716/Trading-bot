"""Strip credentials from anything about to be printed, logged or saved as a fixture.

Removes: values under credential-named keys (authorization, cookie, api keys, access /
refresh / id tokens, passwords, secrets, Privy tokens), anything shaped like a JWT, the
value of any environment variable whose name says KEY, SECRET, TOKEN or PASSWORD, and
credential-named query parameters in URLs.

The key "token" is deliberately NOT treated as a credential: FOMO's rows use it for token
metadata (address, symbol), which a fixture must keep.
"""
import os
import re

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key",
    "api-key", "apikey", "api_key", "access_token", "refresh_token", "id_token",
    "bearer", "password", "secret", "client_secret", "privy:token", "privy-token",
    "privy:refresh_token", "session", "sessionid", "jwt",
}
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
URL_PARAM = re.compile(r"([?&](?:api[-_]?key|key|token|access_token|auth)=)[^&#\s]+",
                       re.I)
ENV_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD")


def _env_secrets() -> list[str]:
    vals = [v for k, v in os.environ.items()
            if any(h in k.upper() for h in ENV_HINTS) and v and len(v) >= 8]
    return sorted(set(vals), key=len, reverse=True)


def text(s: str) -> str:
    for v in _env_secrets():
        s = s.replace(v, REDACTED)
    s = JWT.sub(REDACTED, s)
    return URL_PARAM.sub(lambda m: m.group(1) + REDACTED, s)


def data(x):
    """A sanitized deep copy of a JSON-like value."""
    if isinstance(x, dict):
        return {k: (REDACTED if str(k).lower() in SENSITIVE_KEYS else data(v))
                for k, v in x.items()}
    if isinstance(x, list):
        return [data(v) for v in x]
    if isinstance(x, str):
        return text(x)
    return x
