"""Realtime failure alerting by email.

Napier runs unattended. When a search dies after forty-five minutes of retrying,
or a case will not parse, or something throws where nobody expected it, the only
record today is a line in `heroku logs` that gets read after staff complain
rather than before. This module emails the detail needed to diagnose a failure
without shelling into Heroku.

Configured with `MAILGUN_DOMAIN`, `MAILGUN_API_KEY` and `ALERT_EMAIL_TO`, plus
an optional `ALERT_EMAIL_FROM`. Any of them missing and every path here becomes
a logged no-op, which is what local development and the test suite want. The
Mailgun HTTP API is one POST with no SMTP handshake to stall a worker, and it is
reached with `urllib`, so alerting adds no dependency and no Heroku add-on.

Three things shape the rest of this module.

Delivery never blocks the work. Sends run on a short daemon thread with their
own timeout, and a failed send is logged and dropped. A mail outage must not
turn into a failed search.

An alert that repeats is an alert that gets muted. A forty-five minute retry
budget at escalating backoff produces dozens of attempts, and a clinic morning
puts several staff behind the same broken ICOS. So it is one email per job per
failure class, then one digest when the job ends, with a floor of one email per
class per ten minutes across all jobs on top.

Alerts carry case numbers but never people. A case number is court public record
and a parse-failure alert without one is not actionable, so case ids are in.
The defendant's name and date of birth are the privileged part under Article 5
and are never assembled into an alert. Exception messages are dropped for the
same reason: a parser that dies on a case usually quotes that case back. What
survives is the traceback's frames, which are our own source, plus the exception
type.
"""

import base64
import os
import threading
import time
import traceback
import urllib.parse
import urllib.request

# Failure classes. The wording is the email subject line, so it reads as English.
RETRY_EXHAUSTED = 'ICOS retry budget exhausted'
CONCURRENT_EXHAUSTED = 'ESA account stayed locked'
SLOW_RECOVERY = 'ICOS needed repeated retries'
BAD_RESPONSE = 'unusable response from ICOS'
PARSE_FAILURE = 'case could not be read'
CASE_UNAVAILABLE = 'case could not be retrieved from ICOS'
JOB_FAILED = 'job failed'
UNHANDLED = 'unhandled exception'
# Both of these are runs the server thinks went fine. A staffer whose phone drops
# the progress page sees a finished run as a broken one, gives up, and signs in
# to pull the same cases again, and every server-side signal here stays quiet
# because nothing server-side went wrong.
UNCOLLECTED = 'workbook was built but never collected'
CLIENT_LOST = 'progress page lost contact with Napier'
# Napier decides who is a party by listing the roles that are not, so a role
# nobody has seen before is included by default. That default has been wrong
# four times, most recently a nonparty filer, and each time it took someone
# noticing a stranger's convictions in a client's summary. This does not change
# what is included: it says the list has a gap in it while the run is happening.
NOVEL_ROLE = 'unrecognised party role on an ICOS search'

# A run that eventually worked but took this many attempts is the early warning
# that ICOS is degrading, which is worth one email before staff start noticing.
SLOW_RECOVERY_ATTEMPTS = 3

# Floor across all jobs, so a broad ICOS outage during a clinic sends a handful
# of emails rather than one per staffer per attempt.
CLASS_FLOOR_SECONDS = 10 * 60

MAILGUN_ENDPOINT = 'https://api.mailgun.net/v3/%s/messages'

# Both maps below are keyed by something unique per job, and the web error path
# has no end-of-job digest to clean up after itself, so without a cap a dyno
# that stays up for weeks accumulates one entry per request that ever threw.
# Anything evicted is old enough that re-alerting on it would be correct.
MAX_TRACKED_JOBS = 500

_sent = {}             # job_id -> failure classes already emailed for that job
_class_floor = {}      # failure class -> when it last went out, any job
_pending = {}          # job_id -> the events behind the end-of-job digest
_lock = threading.Lock()


def _forget(job_id):
    """Drop a job's bookkeeping. Caller holds the lock."""
    _pending.pop(job_id, None)
    _sent.pop(job_id, None)


# -- what may and may not go in an email ----------------------------------

def username_prefix(username):
    """The account family, never the account.

    Article 1.2 keeps credentials out of anything we transmit, and the useful
    signal is which pool of logins collided, not which login.
    """
    if not username:
        return 'unknown'
    lowered = username.lower()
    if lowered.startswith('drakelegalclinic'):
        return 'drakelegalclinic'
    if lowered.startswith('ila'):
        return 'ILA##'
    return 'other'


def safe_traceback(exc):
    """Frames and exception type, without the exception's own message.

    The frames are Napier's source and carry nothing about a client. The
    message is the risk: a parser failing on a case tends to include the case,
    and sometimes the defendant, in what it raises. Exceptions we authored
    ourselves carry a `.message` written for staff, so that one is safe to keep.
    """
    if exc is None:
        return ''
    frames = ''.join(traceback.format_tb(exc.__traceback__))
    authored = getattr(exc, 'message', None)
    tail = type(exc).__name__
    if authored:
        tail = '%s: %s' % (tail, authored)
    return frames + tail


def _format(job_id, kind, failure, fields, progress):
    lines = ['%s job %s' % (kind, job_id), '']
    ordered = ('classification', 'endpoint', 'attempts', 'elapsed',
               'backoff', 'status', 'response size', 'account', 'case')
    for label in ordered:
        value = fields.get(label)
        if value is not None:
            lines.append('%-14s %s' % (label + ':', value))
    lines.insert(2, '%-14s %s' % ('failure:', failure))

    # A field this function has not been taught about still belongs in the
    # email. Dropping it silently is how a caller ends up sending alerts that
    # are quietly missing the one detail they added them for.
    for label in sorted(fields):
        if label in ordered or label in ('note', 'traceback'):
            continue
        lines.append('%-14s %s' % (label + ':', fields[label]))

    note = fields.get('note')
    if note:
        lines += ['', note]

    tb = fields.get('traceback')
    if tb:
        lines += ['', 'Traceback (exception message withheld under Article 5):',
                  tb]

    if progress:
        lines += ['', 'Last %d progress lines:' % len(progress)]
        lines += ['  ' + line for line in progress]

    return '\n'.join(lines)


# -- delivery -------------------------------------------------------------

def _mailgun(subject, body):
    domain = os.environ.get('MAILGUN_DOMAIN')
    key = os.environ.get('MAILGUN_API_KEY')
    to = os.environ.get('ALERT_EMAIL_TO')
    if not (domain and key and to):
        return False
    sender = os.environ.get('ALERT_EMAIL_FROM') or 'Napier <napier@%s>' % domain
    data = urllib.parse.urlencode({
        'from': sender,
        'to': to,
        'subject': subject,
        'text': body,
    }).encode('utf-8')
    request = urllib.request.Request(MAILGUN_ENDPOINT % domain, data=data)
    request.add_header('Authorization', 'Basic ' + base64.b64encode(
        ('api:%s' % key).encode('utf-8')).decode('ascii'))
    urllib.request.urlopen(request, timeout=10).read()
    return True


def _deliver(subject, body):
    try:
        if _mailgun(subject, body):
            print('ALERT sent: %s' % subject, flush=True)
        else:
            print('ALERT not configured, would have sent: %s' % subject, flush=True)
    except Exception as e:
        # A mail outage must never become a failed search.
        print('ALERT delivery failed (%s): %s' % (type(e).__name__, subject),
              flush=True)


def _send(subject, body):
    """Hand the send to a daemon thread and get out of the way.

    Returned so tests can join it. Nothing in the app waits on it.
    """
    thread = threading.Thread(target=_deliver, args=(subject, body),
                              name='alert', daemon=True)
    thread.start()
    return thread


# -- the API the rest of the app uses --------------------------------------

def record(job_id, kind, failure, progress=None, now=None, **fields):
    """Note a failure, and email about it if this is the first of its kind.

    Every call is remembered for the end-of-job digest whether or not it sends,
    so suppressing an email never loses the event.
    """
    now = time.time() if now is None else now
    with _lock:
        _pending.setdefault(job_id, []).append((failure, dict(fields)))
        # Dicts keep insertion order, so the first key is the oldest job.
        while len(_pending) > MAX_TRACKED_JOBS:
            _forget(next(iter(_pending)))
        if failure in _sent.get(job_id, ()):
            return None
        last = _class_floor.get(failure)
        if last is not None and now - last < CLASS_FLOOR_SECONDS:
            return None
        _sent.setdefault(job_id, set()).add(failure)
        _class_floor[failure] = now
    return _send('Napier: %s' % failure,
                 _format(job_id, kind, failure, fields, progress))


def digest(job_id, kind, progress=None):
    """One closing email with the whole timeline, if anything went wrong.

    The per-failure emails are deliberately terse and rate limited, so this is
    where the full picture of a bad run lands.
    """
    with _lock:
        events = _pending.get(job_id)
        # The job is over either way, so its dedup state goes with it.
        _forget(job_id)
    if not events:
        return None
    lines = ['%s job %s finished with %d problem%s.'
             % (kind, job_id, len(events), '' if len(events) == 1 else 's'), '']
    for index, (failure, fields) in enumerate(events, start=1):
        detail = ', '.join('%s %s' % (k, v) for k, v in sorted(fields.items())
                           if k not in ('traceback', 'note'))
        lines.append('%2d. %s%s' % (index, failure, ' (%s)' % detail if detail else ''))
    if progress:
        lines += ['', 'Last %d progress lines:' % len(progress)]
        lines += ['  ' + line for line in progress]
    return _send('Napier: %s job ended with %d problem%s'
                 % (kind, len(events), '' if len(events) == 1 else 's'),
                 '\n'.join(lines))


def emitter(job):
    """An alert callback bound to one job, for handing to IcosClient."""
    def emit(failure, **fields):
        return record(job.id[:8], job.kind, failure,
                      progress=recent_progress(job), **fields)
    return emit


def recent_progress(job, limit=20):
    """The tail of a job's staff-facing progress log.

    These are messages Napier wrote for a person to read on the progress page.
    They report counts, retry state and case ids, never the search subject.
    """
    try:
        return [entry['message'] for entry in job.progress[-limit:]]
    except Exception:
        return []


def reset():
    """Forget all dedup and digest state. For tests."""
    with _lock:
        _sent.clear()
        _class_floor.clear()
        _pending.clear()
