"""Which shared Iowa Courts accounts Napier is holding right now.

ICOS allows one session per account and offers no force-logoff, so a second
staffer signing in on the same ILA account waits the lock out. Until this they
waited it out blind: the page said an account was signed in somewhere else and
could not say where, so the honest guess was fifteen minutes of nothing.

Most of the time the answer is Napier. Somebody else on the same account
started a run in this process a few minutes ago, and Napier has known which
account that run holds since it signed in. It just never said so.

State is this process's memory, which here is the whole application: the
Procfile pins gunicorn to one worker, and a restart that loses this registry
loses the sessions it describes at the same moment, so an entry cannot outlive
the session behind it.

The account id is staff-facing and the alerting is not. Somebody told their own
sign in collided already typed the user id, so naming it back tells them
nothing they did not bring with them, while alert mail keeps to the account
family the way everything else in Napier does.
"""

import threading
import time
import uuid

_held = {}
_lock = threading.Lock()


def _key(username):
    return (username or "").strip().upper()


def hold(username, now=None):
    """Record that Napier has an ICOS session open on this account.

    Returns a handle to give back to release(). Handles rather than usernames
    because two runs can briefly overlap on one account while the first is
    logging off, and the second must not release the first one's entry.
    """
    handle = uuid.uuid4().hex
    with _lock:
        _held[handle] = {"account": _key(username),
                         "since": time.time() if now is None else now}
    return handle


def release(handle):
    """Forget a session. Safe to call twice, and safe with an unknown handle."""
    if not handle:
        return
    with _lock:
        _held.pop(handle, None)


def holder(username, now=None):
    """The oldest live Napier session on this account, or None.

    The oldest because that is the one actually holding the lock: anything
    newer is either the caller or a session ICOS refused.
    """
    account = _key(username)
    if not account:
        return None
    with _lock:
        entries = [entry for entry in _held.values()
                   if entry["account"] == account]
    if not entries:
        return None
    entry = min(entries, key=lambda e: e["since"])
    now = time.time() if now is None else now
    return {"account": account, "since": entry["since"],
            "seconds": max(0.0, now - entry["since"])}


def describe_wait(seconds):
    """How long the run holding the account has been going, in plain words."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "less than a minute ago"
    if minutes == 1:
        return "a minute ago"
    if minutes < 60:
        return "%d minutes ago" % minutes
    hours = int(minutes // 60)
    return "%d hour%s ago" % (hours, "" if hours == 1 else "s")


def describe(username, now=None):
    """What to tell a staffer whose sign in collided, or None if we cannot say.

    None is the honest answer when Napier is not holding the account: the lock
    belongs to somebody signed in to Iowa Courts outside Napier, or to a run
    lost with a restarted dyno, and neither is something to guess about.
    """
    entry = holder(username, now=now)
    if entry is None:
        return None
    return ("Napier is already signed in to Iowa Courts as %s. That run "
            "started %s and Iowa Courts allows one session per account, so "
            "this search will start as soon as it finishes or somebody stops "
            "it."
            % (entry["account"], describe_wait(entry["seconds"])))


def _reset():
    """For tests. Nothing in the app has any business emptying this."""
    with _lock:
        _held.clear()
