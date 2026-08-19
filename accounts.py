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

Knowing who holds an account is only half of it. ICOS refuses a second
session and offers no queue, so before this module grew a waiting line every
job that collided simply slept and asked again. Whoever happened to ask in the
instant the account came free won it, which is a race and not an order, and a
job could lose that race for its whole retry budget while later arrivals walked
straight past it. Tickets fix the order: a job joins the line before it asks
ESA for anything, and only the job at the head of its account's line is allowed
to try.

The account id is staff-facing and the alerting is not. Somebody told their own
sign in collided already typed the user id, so naming it back tells them
nothing they did not bring with them, while alert mail keeps to the account
family the way everything else in Napier does.
"""

import threading
import time
import uuid

_held = {}

# The waiting line for each account, oldest first. Kept apart from _held
# because a ticket is intent and an entry in _held is possession: a job takes a
# ticket before it asks ESA for anything and gives it back the moment it is
# signed in or has given up, while an entry in _held lasts as long as the
# session behind it does.
_tickets = []

# A ticket this old is ignored and swept. Nothing should ever hold one for
# anywhere near this long -- the concurrent wait gives up after sixteen
# minutes -- so a ticket that survives it belongs to a thread that died between
# take_ticket and its finally. Counting it forever would wedge the line for
# everybody behind it, and a wedged line is worse than the race it replaced.
STALE_TICKET_SECONDS = 30 * 60

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


def take_ticket(username, now=None):
    """Join the waiting line for an account.

    Returns a ticket to give back to drop_ticket(), which the caller must do in
    a finally: a ticket nobody drops is swept eventually, but everybody behind
    it waits out STALE_TICKET_SECONDS first.

    Taken before the first sign in attempt rather than after the first refusal.
    The order the jobs arrived in is the only fair way to decide who gets the
    account next, and by the time ESA has refused them they are all polling and
    that order is gone.
    """
    ticket = uuid.uuid4().hex
    with _lock:
        _tickets.append({"id": ticket, "account": _key(username),
                         "since": time.time() if now is None else now})
    return ticket


def drop_ticket(ticket):
    """Leave the waiting line. Safe to call twice, and with an unknown ticket."""
    if not ticket:
        return
    with _lock:
        for index, entry in enumerate(_tickets):
            if entry["id"] == ticket:
                del _tickets[index]
                return


def ahead_of(username, ticket, now=None, stale_after=None):
    """How many jobs joined this account's line before this one.

    Zero means it is this job's turn. Counted by position in the line rather
    than by timestamp, because several jobs submitted together can share a
    clock reading and ties would leave two of them both believing they were
    first, which is the race this exists to end.

    An unknown ticket answers zero. A caller whose ticket has already been
    dropped or swept has waited long enough, and hanging it on a line it is not
    standing in would be the wedge described above.
    """
    account = _key(username)
    stale_after = STALE_TICKET_SECONDS if stale_after is None else stale_after
    now = time.time() if now is None else now
    with _lock:
        fresh = [entry for entry in _tickets
                 if now - entry["since"] < stale_after]
        if len(fresh) != len(_tickets):
            _tickets[:] = fresh
        position = None
        for index, entry in enumerate(_tickets):
            if entry["id"] == ticket:
                position = index
                break
        if position is None:
            return 0
        return sum(1 for entry in _tickets[:position]
                   if entry["account"] == account)


def _reset():
    """For tests. Nothing in the app has any business emptying this."""
    with _lock:
        _held.clear()
        del _tickets[:]
