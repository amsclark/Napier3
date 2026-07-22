"""Resilient ICOS client.

Napier's searches fail in ways that need opposite handling, so every attempt is
classified before we decide what to do with it:

  * The court site tarpits the first connections from an idle source IP and
    sometimes returns an empty body mid-session. Those are temporary -- retry
    with backoff and the search eventually goes through.
  * ESA allows one session per account and offers no force-logoff. A concurrent
    login is not a failure to retry hard: the lock clears on its own in about
    15 minutes, so we wait and re-login on a slow cadence.
  * A wrong password will never succeed. Fail immediately.

Timing is injectable so the retry behaviour can be tested without real waits.
"""

import os
import time

from opener import Opener
from reader import Reader

# Backoff between attempts, in seconds; the last value repeats once reached.
BACKOFF = [2, 5, 10, 30, 60, 120, 120, 300]

# ESA's own error page says to wait 15 minutes, so poll a little longer than
# that before giving up, and slowly -- hammering it does not release the lock.
CONCURRENT_INTERVAL = 75

BAD_CREDS_MARKER = "The userID or password could not be validated"
CONCURRENT_MARKER = "Concurrent Login Error"

# ESA does not always put the rejection message on a failed login -- a bad user
# ID has come back as a bare ~700 byte page with no marker at all. Signed in,
# ESA hands back the whole search screen (~28 KB); the concurrent-login error is
# ~3.7 KB. So a small unmarked page means we are not signed in, and treating it
# as success would leave the search retrying against a session that will never
# work.
MIN_SIGNED_IN_BYTES = 8000


def _env_seconds(name, default_minutes):
    try:
        return float(os.environ.get(name, default_minutes)) * 60
    except (TypeError, ValueError):
        return default_minutes * 60


def backoff_for(attempt):
    return BACKOFF[min(attempt, len(BACKOFF) - 1)]


class IcosError(Exception):
    """Base for failures we surface to staff with a plain-language message."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class IcosBadCredentials(IcosError):
    pass


class IcosAccountLocked(IcosError):
    pass


class IcosUnavailable(IcosError):
    pass


class IcosClient:
    def __init__(self, log=None, reader=None, budget_seconds=None,
                 concurrent_budget_seconds=None, sleep=time.sleep,
                 monotonic=time.monotonic):
        self.reader = reader if reader is not None else Reader(Opener())
        self._log = log or (lambda message: None)
        self._sleep = sleep
        self._monotonic = monotonic
        self.budget = budget_seconds if budget_seconds is not None \
            else _env_seconds("RETRY_BUDGET_MIN", 45)
        self.concurrent_budget = concurrent_budget_seconds \
            if concurrent_budget_seconds is not None \
            else _env_seconds("CONCURRENT_WAIT_MIN", 16)
        self.logged_in = False
        self.username = None

    # -- retry core --------------------------------------------------------

    def _attempt_message(self, what, attempt, elapsed):
        # Escalate the wording as a stall drags on, so staff can tell "one slow
        # request" from "the court site is down and we are still working on it".
        if elapsed > 300:
            return ("Iowa Courts appears to be having an outage. Your %s is saved and "
                    "will keep retrying automatically -- you can leave this page open "
                    "or come back later." % what)
        if attempt >= 3:
            return "Iowa Courts is slow, retrying (attempt %d)..." % (attempt + 1)
        return "Iowa Courts did not respond. Retrying..."

    def _retry(self, what, build_request, validate=None):
        """Issue a request until it succeeds or the budget runs out.

        validate() may inspect a successful body and raise, or return False to
        treat the response as a retryable failure.
        """
        started = self._monotonic()
        attempt = 0
        last = "no response"
        while True:
            url, data = build_request()
            result = self.reader.fetch_once(url, data)
            if result.ok:
                if validate is None or validate(result.body):
                    return result.body
                last = "unusable response"
            else:
                last = result.outcome.lower()

            elapsed = self._monotonic() - started
            wait = backoff_for(attempt)
            if elapsed + wait > self.budget:
                raise IcosUnavailable(
                    "Iowa Courts Online did not respond after %d minutes of retrying "
                    "(last result: %s). The court site is likely down. Please try "
                    "again later." % (round(self.budget / 60), last))
            self._log(self._attempt_message(what, attempt, elapsed))
            self._sleep(wait)
            attempt += 1

    # -- operations --------------------------------------------------------

    def login(self, username, password):
        """Log in, waiting out a concurrent-session lock if we hit one.

        The password is used here and never stored on the client, on disk, or
        in a log line.
        """
        self._log("Connecting to Iowa Courts Online...")
        self._retry("search", self.reader.init_request)

        started = self._monotonic()
        waited_for_lock = False
        while True:
            body = self._retry("search",
                               lambda: self.reader.login_request(username, password))
            text = body.decode("utf-8", errors="ignore")

            if BAD_CREDS_MARKER in text:
                raise IcosBadCredentials(
                    "Iowa Courts Online did not accept that user ID or password.")

            if CONCURRENT_MARKER in text:
                # ESA offers no way to clear the other session -- EPALogout only
                # ends our own, which is not the one holding the lock. Waiting is
                # the only thing that works.
                elapsed = self._monotonic() - started
                if elapsed + CONCURRENT_INTERVAL > self.concurrent_budget:
                    raise IcosAccountLocked(
                        "This Iowa Courts account is still logged in from another "
                        "session and Iowa Courts has not released it. Try again in a "
                        "few minutes, or use your own Iowa Courts account so searches "
                        "do not collide.")
                if not waited_for_lock:
                    self._log(
                        "This Iowa Courts account is already logged in somewhere else. "
                        "Waiting for that session to clear -- Iowa Courts releases it "
                        "within about 15 minutes. Nothing is lost; the search will run "
                        "as soon as the account frees up.")
                    waited_for_lock = True
                self._sleep(CONCURRENT_INTERVAL)
                continue

            if len(body) < MIN_SIGNED_IN_BYTES:
                print("ICOS login rejected: %db response, no marker" % len(body),
                      flush=True)
                raise IcosBadCredentials(
                    "Iowa Courts Online did not sign in with that user ID and "
                    "password. Please check them and try again.")

            self.logged_in = True
            self.username = username
            self._log("Signed in to Iowa Courts Online.")
            return

    def search(self, firstname, middlename, lastname):
        self._log("Searching Iowa Courts Online...")
        return self._retry(
            "search",
            lambda: self.reader.search_request(firstname, middlename, lastname))

    def case_bundle(self, case_id):
        """Summary, charges and financials for one case.

        The three pages must be fetched in this order: ICOS keys the charges and
        financials views off the case selected by the summary request.
        """
        summary = self._retry("case", lambda: self.reader.case_summary_request(case_id))
        charges = self._retry("case", self.reader.case_charges_request)
        financials = self._retry("case", self.reader.case_financials_request)
        return summary, charges, financials

    def logoff(self):
        """Release the ESA session.

        Leaving sessions open is what makes shared accounts collide, so this is
        best-effort but never skipped -- and never allowed to raise, since it
        runs in cleanup paths.
        """
        if not self.logged_in:
            return
        try:
            url, data = self.reader.logoff_request()
            self.reader.fetch_once(url, data)
        except Exception as e:
            print("ICOS logoff failed: %s" % type(e).__name__, flush=True)
        finally:
            self.logged_in = False
