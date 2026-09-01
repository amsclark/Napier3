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

One thing here is not an alert. outage_evidence() mails a redacted copy of the
page ICOS serves when it has declared itself down, because everything else in
this module is Napier's word about a bad morning and Napier's word is what a
court would question. What may be kept and what has to come out first is
evidence.py's problem, not this module's.
"""

import base64
import os
import threading
import time
import traceback
import urllib.parse
import urllib.request

import evidence

# Failure classes. The wording is the email subject line, so it reads as English.
RETRY_EXHAUSTED = 'ICOS retry budget exhausted'
CONCURRENT_EXHAUSTED = 'ESA account stayed locked'
SLOW_RECOVERY = 'ICOS needed repeated retries'
BAD_RESPONSE = 'unusable response from ICOS'
# ICOS answering wrongly and ICOS not answering at all were one class, and
# record() emails only the first of each class per run, so whichever came first
# silenced the other. They also want opposite things looked at: a bad body is
# the court site's data, and no body is the path to it.
NO_ANSWER = 'no answer from ICOS'
PARSE_FAILURE = 'case could not be read'
CASE_UNAVAILABLE = 'case could not be retrieved from ICOS'
# Its own class so three refusals of one case read as one thing in the
# digest, and not as the site going down.
CASE_REFUSED = 'ICOS refused one case while the site was up'
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
# The same shape of gap one column over. charge_code_map turns the words Iowa
# Courts uses for an outcome into the code the CRS wants, and a word missing
# from it codes the case OTH, which four analysis sheets read as no conviction.
# Nothing fails and the workbook is delivered, so the only way this has ever
# surfaced is somebody noticing a sheet was wrong about a client. The alert
# carries the ICOS wording and the case number and no defendant.
UNKNOWN_DISPOSITION = 'unrecognised disposition on an ICOS case'
# Its own class, because it is a different fact about a different row and it
# wants a different answer. Above is a word missing from charge_code_map, and
# the fix is to add it. This is the status ICOS prints for a case as a whole on
# a case where no count was adjudicated, and case_level_code refuses to
# translate all but one wording of it on purpose: guessing a conviction code
# off a case status is the error it is avoiding. Column G is left empty, which
# BANKRUPTCY, EXEMPTIONS and SOL read as an open charge.
#
# Sharing the class with the one above cost the distinction twice over. Both
# suppress each other under the per-class floor, so a run carrying one of each
# mailed whichever came first, and the subject line said a word was missing
# from a map when nothing was. Splitting them also stops these drowning the
# other: what CLOSED deserves is an open question with Iowa Legal Aid, so this
# fires on every run of the same client until they answer it.
UNCODED_CASE_STATUS = 'untranslated case status on an ICOS case'
# grid.extend_formula_grid fills the derived sheets down to the case list on
# every build, and grid.shortfalls then measures whether it worked. It always
# has. When it stops -- a CRS 3.6 laid out in a way the extension cannot read,
# most likely -- the workbook is still delivered, still opens, and is short a
# figure on a sheet nobody recounts by hand. The finish page tells the staffer,
# and this tells the person who can fix the template, because the two are not
# the same person and the workbook is going to a clinic either way.
WORKBOOK_SHORT = 'workbook sheets do not reach the last case'

# A run that eventually worked but took this many attempts is the early warning
# that ICOS is degrading, which is worth one email before staff start noticing.
SLOW_RECOVERY_ATTEMPTS = 3

# Floor across all jobs, so a broad ICOS outage during a clinic sends a handful
# of emails rather than one per staffer per attempt.
CLASS_FLOOR_SECONDS = 10 * 60

# Not a failure class. It never goes through record(), is never suppressed by a
# job having already alerted, and never appears in a digest. It is a separate
# piece of mail with a separate subject so it can be filtered, kept, and
# forwarded to Iowa Courts without the diagnostics around it.
OUTAGE_EVIDENCE = 'Iowa Courts served its own outage page'

# Its own floor, counted across every job on the dyno. During the 2026-07-30
# outage ICOS served the same page 45 times in eleven minutes; one copy of a
# byte-identical page proves as much as forty five do.
EVIDENCE_FLOOR_SECONDS = 15 * 60

MAILGUN_ENDPOINT = 'https://api.mailgun.net/v3/%s/messages'

# Both maps below are keyed by something unique per job, and the web error path
# has no end-of-job digest to clean up after itself, so without a cap a dyno
# that stays up for weeks accumulates one entry per request that ever threw.
# Anything evicted is old enough that re-alerting on it would be correct.
MAX_TRACKED_JOBS = 500

_sent = {}             # job_id -> failure classes already emailed for that job
_class_floor = {}      # failure class -> when it last went out, any job
_pending = {}          # job_id -> the events behind the end-of-job digest
_evidence_floor = [None]   # when an outage page was last mailed, any job
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
    # 'reason' leads because it is the sentence that decides whether anyone
    # needs to do anything, and it used to not exist: an ICOS failure arrived
    # as an endpoint, an attempt count and a size, and which of five things had
    # gone wrong had to be worked out from the source.
    ordered = ('reason', 'classification', 'endpoint', 'attempts', 'elapsed',
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

def _form_data(fields, attachment):
    """Encode an attachment the way Mailgun's API wants it.

    Hand rolled because the dyno has no requests library and this is the only
    thing in Napier that needs multipart.
    """
    boundary = 'napier-%s' % os.urandom(16).hex()
    while boundary.encode('ascii') in attachment['content']:
        boundary = 'napier-%s' % os.urandom(16).hex()
    out = []
    for name, value in fields.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"'
                    '\r\n\r\n%s\r\n' % (boundary, name, value)).encode('utf-8'))
    out.append(('--%s\r\nContent-Disposition: form-data; name="attachment"; '
                'filename="%s"\r\nContent-Type: text/html\r\n\r\n'
                % (boundary, attachment['filename'])).encode('utf-8'))
    out.append(attachment['content'])
    out.append(('\r\n--%s--\r\n' % boundary).encode('utf-8'))
    return 'multipart/form-data; boundary=%s' % boundary, b''.join(out)


def _mailgun(subject, body, attachment=None):
    domain = os.environ.get('MAILGUN_DOMAIN')
    key = os.environ.get('MAILGUN_API_KEY')
    to = os.environ.get('ALERT_EMAIL_TO')
    if not (domain and key and to):
        return False
    sender = os.environ.get('ALERT_EMAIL_FROM') or 'Napier <napier@%s>' % domain
    fields = {'from': sender, 'to': to, 'subject': subject, 'text': body}
    if attachment is None:
        content_type = 'application/x-www-form-urlencoded'
        data = urllib.parse.urlencode(fields).encode('utf-8')
    else:
        content_type, data = _form_data(fields, attachment)
    request = urllib.request.Request(MAILGUN_ENDPOINT % domain, data=data)
    request.add_header('Content-Type', content_type)
    request.add_header('Authorization', 'Basic ' + base64.b64encode(
        ('api:%s' % key).encode('utf-8')).decode('ascii'))
    urllib.request.urlopen(request, timeout=10).read()
    return True


def _deliver(subject, body, attachment=None):
    try:
        if _mailgun(subject, body, attachment):
            print('ALERT sent: %s' % subject, flush=True)
        else:
            print('ALERT not configured, would have sent: %s' % subject, flush=True)
    except Exception as e:
        # A mail outage must never become a failed search.
        print('ALERT delivery failed (%s): %s' % (type(e).__name__, subject),
              flush=True)


def _send(subject, body, attachment=None):
    """Hand the send to a daemon thread and get out of the way.

    Returned so tests can join it. Nothing in the app waits on it.
    """
    thread = threading.Thread(target=_deliver, args=(subject, body, attachment),
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


def outage_evidence(body, endpoint=None, case_id=None, username=None,
                    status=None, attempts=None, now=None):
    """Mail a redacted copy of a page on which ICOS declared itself down.

    Every other alert here is Napier's account of what happened, which is
    exactly what is in dispute when a clinic tells Iowa Courts it lost a
    morning. This one is Iowa's account, in Iowa's wording, and it is the
    difference between a complaint and a report.

    Sends at most once every 15 minutes across the whole dyno, and only for a
    page evidence.package() will vouch for. Returns the send thread, or None if
    nothing was sent.
    """
    now = time.time() if now is None else now
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))
    document = evidence.package(body, case_id=case_id, stamp=stamp)
    if document is None:
        return None
    with _lock:
        last = _evidence_floor[0]
        if last is not None and now - last < EVIDENCE_FLOOR_SECONDS:
            return None
        _evidence_floor[0] = now

    lines = [
        'Iowa Courts answered a request with its own problem report page:',
        'its web application could not reach the data behind it. The page is',
        'attached as it was served, less the redactions noted below.',
        '',
    ]
    for label, value in (
            ('when (UTC)', time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now))),
            ('when (local)', time.strftime('%Y-%m-%d %H:%M:%S %Z',
                                           time.localtime(now))),
            ('ICOS endpoint', endpoint),
            ('case requested', case_id),
            ('HTTP status', status),
            ('page size', '%db' % document['original size']),
            ('attempts so far', attempts),
            ('ESA account', username_prefix(username) if username else None),
            ('sha256 of the page as served', document['fingerprint'])):
        if value is not None:
            lines.append('%s: %s' % (label, value))
    lines += [
        '',
        'Note the case number printed on the attached page. ICOS keeps serving',
        'the heading of whichever case the session selected last, so on a',
        'problem report it is usually not the case that was requested, and the',
        'disposition table under it is empty. That is why this page is a',
        'hazard rather than an inconvenience: it parses as a real case with no',
        'charges and nothing owed.',
        '',
        'Three things are withheld from the attachment and nothing else is.',
        'The case caption, because on a clinic run it names the client and is',
        'privileged. Any date, for the same reason. The signed-in account,',
        'which ICOS stamps into the corner of every page it serves. None of',
        'them bear on whether ICOS was up. The sha256 above is of the page as',
        'ICOS served it, before any of that was taken out.',
    ]
    return _send('Napier: %s' % OUTAGE_EVIDENCE, '\n'.join(lines), document)


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
        _evidence_floor[0] = None
