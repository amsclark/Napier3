"""Server-side store for logged-in ICOS sessions.

Staff search, look at the results, then pick who to build a CRS for. That gap
is why shared ESA accounts jam: ICOS allows one session per account, the old
code only logged off when someone actually generated a CRS, and every abandoned
results page left an account locked for about fifteen minutes.

So a session is held here between the search and the CRS build, and a reaper
logs off anything nobody came back to. The browser holds only an opaque token
(in the Flask session cookie); the ESA password is never stored -- the live
cookie jar inside the client is what carries the session.
"""

import threading
import time
import uuid

# Long enough to read a results page and pick defendants, short enough that an
# abandoned search frees the account well inside ESA's own lock window.
IDLE_TIMEOUT = 10 * 60

_sessions = {}
_lock = threading.Lock()


def put(client):
    token = uuid.uuid4().hex
    with _lock:
        _sessions[token] = {"client": client, "last_used": time.time()}
    return token


def get(token):
    if not token:
        return None
    with _lock:
        entry = _sessions.get(token)
        if entry is None:
            return None
        entry["last_used"] = time.time()
        return entry["client"]


def close(token):
    """Log off and forget a session. Safe to call with an unknown token."""
    with _lock:
        entry = _sessions.pop(token, None)
    if entry is not None:
        entry["client"].logoff()


def _reap(now=None):
    now = now if now is not None else time.time()
    with _lock:
        expired = [t for t, e in _sessions.items()
                   if now - e["last_used"] > IDLE_TIMEOUT]
        clients = [_sessions.pop(t)["client"] for t in expired]
    for client in clients:
        print("Logging off an abandoned Iowa Courts session", flush=True)
        client.logoff()
    return len(clients)


def start_reaper(interval=60):
    def loop():
        while True:
            time.sleep(interval)
            try:
                _reap()
            except Exception:
                pass

    threading.Thread(target=loop, name="icos-session-reaper", daemon=True).start()
