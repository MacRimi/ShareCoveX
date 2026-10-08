"""Panel sessions and the limit on failed sign-ins.

The password is checked once, when signing in. After that the browser carries
a random token in a cookie that scripts cannot read, so the password does not
travel with every request and nothing recomputes its hash on each one.
"""
import secrets
import threading
import time

COOKIE = "sharecovex_session"
IDLE = 30 * 60          # a session ends after this long without a request
LIFETIME = 12 * 3600    # and after this long whatever its activity
MAX_SESSIONS = 64

FAILURES = 5            # failed sign-ins from one address...
WINDOW = 5 * 60         # ...within this time...
LOCKOUT = 60            # ...block that address for this long, doubling each
MAX_LOCKOUT = 15 * 60   # time it happens again, up to this
MAX_ADDRESSES = 1024


class SessionStore:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.sessions = {}

    def _expired(self, session, now):
        return now - session["seen"] > IDLE or now - session["created"] > LIFETIME

    def _prune(self, now):
        for token in [token for token, session in self.sessions.items() if self._expired(session, now)]:
            del self.sessions[token]

    def create(self, user):
        now = self.clock()
        with self.lock:
            self._prune(now)
            while len(self.sessions) >= MAX_SESSIONS:
                del self.sessions[min(self.sessions, key=lambda token: self.sessions[token]["seen"])]
            token = secrets.token_urlsafe(32)
            self.sessions[token] = {"user": user, "created": now, "seen": now}
            return token

    def user(self, token):
        """The administrator a token belongs to, or None. Using it keeps it alive."""
        if not isinstance(token, str) or not token:
            return None
        now = self.clock()
        with self.lock:
            session = self.sessions.get(token)
            if session is None:
                return None
            if self._expired(session, now):
                del self.sessions[token]
                return None
            session["seen"] = now
            return session["user"]

    def end(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def end_user(self, user):
        """Sign an administrator out everywhere: its password changed or it was removed."""
        with self.lock:
            for token in [token for token, session in self.sessions.items() if session["user"] == user]:
                del self.sessions[token]


class LoginLimiter:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.addresses = {}

    def retry_after(self, address):
        """Seconds this address still has to wait before another attempt; 0 when it may try."""
        now = self.clock()
        with self.lock:
            record = self.addresses.get(address)
            if not record:
                return 0
            return max(0, int(record["blocked_until"] - now + 0.999))

    def failed(self, address):
        now = self.clock()
        with self.lock:
            if address not in self.addresses and len(self.addresses) >= MAX_ADDRESSES:
                oldest = min(self.addresses, key=lambda item: self.addresses[item]["last"])
                del self.addresses[oldest]
            record = self.addresses.setdefault(address, {"failures": [], "blocked_until": 0.0, "strikes": 0, "last": now})
            record["last"] = now
            record["failures"] = [moment for moment in record["failures"] if now - moment <= WINDOW] + [now]
            if len(record["failures"]) >= FAILURES:
                record["blocked_until"] = now + min(LOCKOUT * 2 ** record["strikes"], MAX_LOCKOUT)
                record["strikes"] += 1
                record["failures"] = []

    def succeeded(self, address):
        with self.lock:
            self.addresses.pop(address, None)
