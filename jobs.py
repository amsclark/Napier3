"""In-process background jobs.

Searches now run longer than Heroku's 30 second request limit allows (they
retry through court-side stalls), so the work happens on a background thread
and the browser polls for progress.

State lives in this process's memory: the app runs as a single gunicorn
process with threads, and the hosting arrangement is fixed by contract, so
there is no shared store to put it in. A dyno restart therefore loses running
jobs; callers get a clear "the server restarted, please run your search again"
rather than a silent hang.
"""

import threading
import time
import uuid

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

# How long a finished job (and its progress log) stays readable, and how long a
# job may run before the janitor stops tracking it.
RETENTION_SECONDS = 2 * 60 * 60


class Job:
    def __init__(self, kind):
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.status = QUEUED
        self.progress = []
        self.result = None
        self.error = None
        self.next_url = None
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._lock = threading.Lock()

    def log(self, message):
        with self._lock:
            self.progress.append({"t": time.time(), "message": message})
            self.updated_at = time.time()
        print("JOB %s %s: %s" % (self.kind, self.id[:8], message), flush=True)

    def to_dict(self):
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "progress": [p["message"] for p in self.progress],
                "message": self.progress[-1]["message"] if self.progress else "Starting...",
                "error": self.error,
                "next_url": self.next_url,
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


def start_janitor(interval=600):
    def loop():
        while True:
            time.sleep(interval)
            try:
                _janitor_pass()
            except Exception:
                pass

    threading.Thread(target=loop, name="job-janitor", daemon=True).start()
