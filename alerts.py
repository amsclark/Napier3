"""Out-of-band alerts for failures nobody is watching the logs for.

Napier runs unattended. When ICOS stops answering or a job crashes, the only
record is a line in `heroku logs`, which is read after someone complains rather
than before. This module pushes the handful of failures worth waking up for to
a notification endpoint, and pushes the all-clear when they end.

Set NAPIER_ALERT_URL to an ntfy topic (or anything that accepts an HTTPS POST
with a text body). Unset, every call here is a no-op, which is what tests and
local development want.

Two rules shape the rest of this module.

Nothing from ICOS goes into an alert. The endpoint is a third-party service and
the payloads are client court records, so alerts carry the shape of a failure
(which subsystem, which exception type, how long) and never its content. No
names, no case numbers, no exception messages, since a parser blowing up on a
case tends to put that case in its message. The full detail stays in the Heroku
log, which is inside the trust boundary.

An alert that repeats is an alert that gets muted. A sustained ICOS outage would
otherwise fire every twenty seconds, so alerts are per episode: one when a
condition starts, an hourly reminder while it continues, one when it clears.
"""

import os
import threading
import time
import urllib.request

ALERT_URL_ENV = 'NAPIER_ALERT_URL'

# ntfy accepts exactly these and silently 400s on anything else, so an invalid
# priority means the alert is never delivered and nothing says so. Validate here
# rather than discover it during an outage.
PRIORITIES = ('min', 'low', 'default', 'high', 'urgent', 'max')

# How long a still-firing condition waits before it reminds anyone.
REMINDER_SECONDS = 60 * 60

_firing = {}
_lock = threading.Lock()


def _post(title, body, priority, tags):
    url = os.environ.get(ALERT_URL_ENV)
    if not url:
        return False
    if priority not in PRIORITIES:
        print("ALERT bad priority %r, sending as default" % (priority,), flush=True)
        priority = 'default'
    request = urllib.request.Request(
        url, data=body.encode('utf-8'), method='POST',
        headers={'Title': title, 'Priority': priority, 'Tags': tags})
    urllib.request.urlopen(request, timeout=10).read()
    return True


def _send(title, body, priority, tags):
    """Deliver one alert. Never raises: alerting must not take the app down."""
    try:
        sent = _post(title, body, priority, tags)
    except Exception as e:
        print("ALERT delivery failed (%s): %s" % (type(e).__name__, title), flush=True)
        return False
    print("ALERT %s %s" % ("sent" if sent else "suppressed (no %s)" % ALERT_URL_ENV,
                           title), flush=True)
    return sent


def raise_alert(event, title, body, priority='high', tags='rotating_light'):
    """Report that `event` is broken.

    Sends on the first call, then at most once an hour while the condition
    lasts, so an outage is one page plus a reminder rather than a stream.
    """
    now = time.time()
    with _lock:
        last = _firing.get(event)
        if last is not None and now - last < REMINDER_SECONDS:
            return False
        _firing[event] = now
    if last is not None:
        title = "%s (still)" % title
    return _send(title, body, priority, tags)


def clear_alert(event, title, body, tags='white_check_mark'):
    """Report that `event` recovered, if it had been reported broken.

    Silent when the event was never firing, so a healthy app is silent.
    """
    with _lock:
        if event not in _firing:
            return False
        del _firing[event]
    return _send(title, body, 'default', tags)


def is_firing(event):
    with _lock:
        return event in _firing


def reset():
    """Forget all firing state. For tests."""
    with _lock:
        _firing.clear()
