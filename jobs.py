"""In-process background jobs.

Searches now run longer than Heroku's 30 second request limit allows (they
retry through court-side stalls), so the work happens on a background thread
and the browser polls for progress.

State lives in this process's memory, so the Procfile pins gunicorn to a single
worker with threads. Heroku's Python buildpack otherwise sets WEB_CONCURRENCY=2
and half the progress polls land on a worker that has never heard of the job.
A dyno restart still loses running jobs; callers get a clear "the server
restarted, please run your search again" rather than a silent hang.
"""

import threading
import time
import uuid

import alerts

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

# How long a finished job (and its progress log) stays readable, and how long a
# job may run before the janitor stops tracking it.
RETENTION_SECONDS = 2 * 60 * 60

# A finished workbook nobody has downloaded after this long is a run that
# succeeded here and failed on the staffer's screen. It is the only trace such a
# run leaves, since every other alert in this app fires from something throwing.
UNCOLLECTED_AFTER = 5 * 60

# The job kinds that end with a file somebody has to be handed. These are the
# ones the uncollected alert watches and the ones the start page keeps offering
# until a staffer has actually taken the file.
BUILDS_A_WORKBOOK = ('crs', 'batch_crs')

# How many finished units of work are timed to work out how much longer a run
# has. Kept short so the estimate follows the site Napier is talking to now
# rather than the one it was talking to ten minutes ago.
UNITS_TIMED = 20

# And how many of those it takes before saying anything. Two cases is not a
# rate, and an estimate that lands wrong the first time staff see one teaches
# them to ignore every one after it.
UNITS_BEFORE_GUESSING = 3


class Job:
    def __init__(self, kind):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.status = QUEUED
        self.progress = []
        self.result = None
        self.error = None
        self.next_url = None
        # How far along the job is, when it knows. A CRS job pulls a case at a
        # time and can count them; a search cannot, and leaves these None so
        # the page shows an unmeasured bar instead of inventing a number.
        self.count = None
        self.total = None
        # When each of the last few units of work finished, oldest first. The
        # page turns these into "about two minutes left", which is the question
        # staff are actually asking when they watch a bar crawl: whether to wait
        # or to go and do something else.
        self.marks = []
        self.created_at = time.time()
        self.updated_at = self.created_at
        # Whether the file this job built ever reached the person who asked for
        # it. A run is not finished when the server says so, it is finished when
        # a staffer has the workbook.
        self.collected = False
        self.reported_uncollected = False
        # Cases ICOS would not serve while it was serving others, by case
        # id, with what the finish page should say about each. Kept apart
        # from the failed list because the advice differs: a retry helps a
        # case the site was down for, and does nothing for one of these.
        self.refused = {}
        # Set when a staffer asks the run to stop. The work checks it between
        # cases rather than being killed, so the ICOS session is logged off on
        # the way out and the account is free for the next person.
        self.cancelled = False
        self._lock = threading.Lock()

    def cancel(self):
        """Ask the run to stop at its next check.

        A run that is waiting out a court-side stall can sit on one case for
        four minutes, so there has to be a way to stop it. Without this the
        only button on the page logged the browser out, which left the work
        running, held the shared ESA account, and threw away the browser's
        claim on whatever the run did eventually produce.
        """
        self.cancelled = True

    def log(self, message, count=None, total=None):
        with self._lock:
            now = time.time()
            self.progress.append({"t": now, "message": message})
            if total is not None:
                if count != self.count:
                    self.marks = (self.marks + [now])[-UNITS_TIMED:]
                self.count = count
                self.total = total
            self.updated_at = now
        print("JOB %s %s: %s" % (self.kind, self.id[:8], message), flush=True)

    def _seconds_left(self):
        """How much longer this has, or None when there is nothing honest to say.

        Caller holds the lock.

        The typical case is the middle one rather than the average, because a
        single case that Iowa Courts stalls on for four minutes would otherwise
        rewrite the estimate for the sixty behind it and leave staff reading
        "about 45 minutes left" on a run that has two minutes to go.

        Which leaves the stall itself unaccounted for, and a bar that has not
        moved in three minutes under an estimate that has not moved either is
        the page calling itself a liar. So whatever the case in hand has already
        overrun by is added on. During a stall the estimate climbs, which is
        both true and the thing worth knowing, and it drops back the moment the
        case comes through instead of poisoning the rest of the run.
        """
        if not self.total or self.count is None:
            return None
        remaining = self.total - self.count
        if remaining <= 0:
            return None
        gaps = sorted(b - a for a, b in zip(self.marks, self.marks[1:]))
        if len(gaps) < UNITS_BEFORE_GUESSING:
            return None
        typical = gaps[len(gaps) // 2]
        overrun = max(0.0, (time.time() - self.marks[-1]) - typical)
        return typical * remaining + overrun

    def to_dict(self):
        with self._lock:
            seconds_left = self._seconds_left()
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "progress": [p["message"] for p in self.progress],
                "message": self.progress[-1]["message"] if self.progress else "Starting...",
                "error": self.error,
                "count": self.count,
                "total": self.total,
                "seconds_left": None if seconds_left is None else round(seconds_left),
                "next_url": self.next_url,
                "cancelled": self.cancelled,
                "done": self.status in (DONE, FAILED),
            }


_jobs = {}
_jobs_lock = threading.Lock()


def get(job_id):
    with _jobs_lock:
        return _jobs.get(job_id)


def start(kind, target, *args, **kwargs):
    """Run target(job, *args) on a background thread and return the Job.

    target may return a URL to send the browser to when it finishes.
    """
    job = Job(kind)
    with _jobs_lock:
        _jobs[job.id] = job

    def run():
        job.status = RUNNING
        try:
            next_url = target(job, *args, **kwargs)
            job.next_url = next_url
            job.status = DONE
        except Exception as e:
            # Anything with a .message is a failure we already phrased for
            # staff; anything else is a bug, and staff should not be shown a
            # traceback they cannot act on.
            message = getattr(e, "message", None)
            if message is None:
                print("JOB %s %s crashed: %r" % (kind, job.id[:8], e), flush=True)
                # Staff get an apology they cannot act on, so someone who can
                # act has to hear about it.
                alerts.record(job.id[:8], kind, alerts.JOB_FAILED,
                              progress=alerts.recent_progress(job),
                              **{'traceback': alerts.safe_traceback(e)})
                message = ("Something went wrong inside Napier. Please try again, "
                           "and let Clark Management Consulting know if it keeps "
                           "happening.")
            job.error = message
            job.status = FAILED
            job.log(message)
        finally:
            job.updated_at = time.time()

    threading.Thread(target=run, name="job-%s-%s" % (kind, job.id[:8]),
                     daemon=True).start()
    return job


def _janitor_pass(now=None):
    now = now if now is not None else time.time()
    with _jobs_lock:
        stale = [jid for jid, job in _jobs.items()
                 if now - job.updated_at > RETENTION_SECONDS]
        for jid in stale:
            del _jobs[jid]
    return len(stale)


def _uncollected_pass(now=None):
    """Email about workbooks that were built and then never picked up.

    Everything else in this app alerts on something raising. This one alerts on
    a run where nothing raised: the cases came back, the file got written, and
    the staffer never saw any of it because their end of the conversation
    dropped. Without this the failure is invisible until someone complains.
    """
    now = now if now is not None else time.time()
    with _jobs_lock:
        orphans = [job for job in _jobs.values()
                   if job.kind in BUILDS_A_WORKBOOK and job.status == DONE
                   and not job.collected and not job.reported_uncollected
                   and now - job.updated_at > UNCOLLECTED_AFTER]
        for job in orphans:
            job.reported_uncollected = True

    for job in orphans:
        result = job.result or {}
        alerts.record(job.id[:8], job.kind, alerts.UNCOLLECTED,
                      progress=alerts.recent_progress(job),
                      **{
                          'cases written': result.get('written_cases'),
                          'cases requested': result.get('requested_cases'),
                          'waiting': '%d minutes' % int((now - job.updated_at) / 60),
                          'note': ("The run itself succeeded. Either the staffer "
                                   "closed the page before it finished or their "
                                   "browser lost contact with Napier, so they may "
                                   "be about to pull the same cases a second "
                                   "time. The workbook is still on the server and "
                                   "is offered again on Napier's first page for "
                                   "two hours from the time it was built."),
                      })
    return len(orphans)


def start_janitor(interval=60):
    def loop():
        while True:
            time.sleep(interval)
            for step in (_janitor_pass, _uncollected_pass):
                try:
                    step()
                except Exception:
                    pass

    threading.Thread(target=loop, name="job-janitor", daemon=True).start()
