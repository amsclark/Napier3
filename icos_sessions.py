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

import atexit
import signal
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


def claim(token):
    """Take a session out of the store for a job that will hold it a while.

    The reaper works off last_used, which a CRS run never refreshes: it fetches
    the client once and then spends minutes talking to ICOS. Left in the store,
    a run longer than IDLE_TIMEOUT has its own live session logged off
    underneath it from the reaper thread, and a staffer hitting logout mid-run
    does the same. A claimed session is nobody else's to close, so the job owns
    it and logs it off itself.
    """
    if not token:
        return None
    with _lock:
        entry = _sessions.pop(token, None)
    return entry["client"] if entry is not None else None


def close(token):
    """Log off and forget a session. Safe to call with an unknown token."""
    with _lock:
        entry = _sessions.pop(token, None)
    if entry is not None:
        entry["client"].logoff()


def close_all():
    """Log off everything on the way down.

    The store is in memory, so a dyno restart forgets it. ICOS does not: it
    goes on holding the session, and the shared account stays locked for about
    fifteen minutes while staff who never deployed anything are told someone
    else is signed in. One session that will not close must not strand the
    others, so each is tried on its own.
    """
    with _lock:
        clients = [entry["client"] for entry in _sessions.values()]
        _sessions.clear()
    if clients:
        # Says so in the dyno log because the alternative is a locked account
        # with nothing anywhere explaining why. The reaper announces itself for
        # the same reason.
        print("Shutting down: logging off %d Iowa Courts session(s)"
              % len(clients), flush=True)
    for client in clients:
        try:
            client.logoff()
        except Exception:
            pass
    return len(clients)


def install_shutdown_hooks():
    """Release held sessions when the platform says the process is going away.

    It has to be SIGTERM rather than atexit. Gunicorn takes its full thirty
    second graceful timeout on the way down and Heroku SIGKILLs at that mark,
    so an atexit handler runs in the same instant the process dies: it gets to
    print and nothing more, and the logoff request never leaves the dyno. That
    is measured, not assumed -- an R12 exit timeout with no EPALogout to show
    for it, and the account still locked afterwards. SIGTERM lands thirty
    seconds earlier, which is the entire budget for doing this properly.

    Gunicorn's own handler is left in place behind ours so its shutdown still
    runs. atexit stays as a backstop for the exits that never see a SIGTERM.
    """
    atexit.register(close_all)
    previous = signal.getsignal(signal.SIGTERM)

    def release_then_continue(signum, frame):
        try:
            close_all()
        finally:
            if callable(previous):
                previous(signum, frame)

    signal.signal(signal.SIGTERM, release_then_continue)


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
