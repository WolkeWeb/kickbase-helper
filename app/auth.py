"""Single-user login: password hash from `.env`, signed session cookie, CSRF token."""

import logging
import secrets
import time
from collections import deque
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Request

logger = logging.getLogger(__name__)

SESSION_AUTH_KEY = "auth"
SESSION_CSRF_KEY = "csrf"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHashError) as exc:
        # Settings validation rejects malformed hashes at startup, so this should not happen.
        logger.error("APP_PASSWORD_HASH unbrauchbar (%s: %s)", type(exc).__name__, exc)
        return False


class LoginRequired(Exception):
    """Raised by `require_login`; turned into a redirect to /login by an exception handler."""

    def __init__(self, next_path: str) -> None:
        self.next_path = next_path


def is_logged_in(request: Request) -> bool:
    return request.session.get(SESSION_AUTH_KEY) is True


def require_login(request: Request) -> None:
    if not is_logged_in(request):
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query
        raise LoginRequired(next_path=target)


def log_in(request: Request) -> None:
    request.session.clear()
    request.session[SESSION_AUTH_KEY] = True
    request.session[SESSION_CSRF_KEY] = secrets.token_urlsafe(32)


def log_out(request: Request) -> None:
    request.session.clear()


def csrf_token(request: Request) -> str:
    token = request.session.get(SESSION_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF_KEY] = token
    return token


def csrf_valid(request: Request, submitted: str | None) -> bool:
    expected = request.session.get(SESSION_CSRF_KEY)
    if not expected or not submitted:
        return False
    # Compare bytes: compare_digest raises TypeError for non-ASCII str input.
    return secrets.compare_digest(expected.encode(), submitted.encode())


class CsrfFailed(Exception):
    """Raised by `require_csrf_header`; answered with 403."""


def require_csrf_header(request: Request) -> None:
    """For HTMX POSTs: the token travels in the X-CSRF-Token header (see layout.html)."""
    if not csrf_valid(request, request.headers.get("X-CSRF-Token")):
        raise CsrfFailed


def safe_next_path(candidate: str | None) -> str:
    """Only allow local absolute paths as redirect target (no open redirects)."""
    if not candidate:
        return "/"
    try:
        parts = urlsplit(candidate)
    except ValueError:  # e.g. "//[" is not a parseable URL
        return "/"
    if (
        parts.scheme
        or parts.netloc
        or not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
    ):
        return "/"
    return candidate


class LoginThrottle:
    """Blocks further attempts after too many failures within a sliding window.

    In-memory and per process; intended for the single-user, single-app-instance setup.
    """

    def __init__(self, max_failures: int = 5, window_seconds: float = 300.0) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._failures and now - self._failures[0] > self.window_seconds:
            self._failures.popleft()

    def blocked(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        self._prune(now)
        return len(self._failures) >= self.max_failures

    def failure_count(self, now: float | None = None) -> int:
        self._prune(time.monotonic() if now is None else now)
        return len(self._failures)

    def record_failure(self, now: float | None = None) -> None:
        self._failures.append(time.monotonic() if now is None else now)

    def reset(self) -> None:
        self._failures.clear()
