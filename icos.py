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
import re
import time

import alerts
from opener import Opener
from reader import Reader

# Attempts before a run that still succeeded is worth an early-warning email.
SLOW_RECOVERY_ATTEMPTS = alerts.SLOW_RECOVERY_ATTEMPTS

# Backoff between attempts, in seconds; the last value repeats once reached.
BACKOFF = [2, 5, 10, 30, 60, 120, 120, 300]

# ESA's own error page says to wait 15 minutes, so poll a little longer than
# that before giving up, and slowly -- hammering it does not release the lock.
CONCURRENT_INTERVAL = 75

BAD_CREDS_MARKER = "The userID or password could not be validated"
CONCURRENT_MARKER = "Concurrent Login Error"

# ESA does not always put the rejection message on a failed login -- a bad user
# ID has come back as a bare ~700 byte page with no marker at all. Signed in,
# ESA hands back the whole search screen (~28 KB). Size is only a backstop for
# unmarked pages: measured against live ESA in July 2026 the concurrent-login
# error is about 685 bytes, the same band as a bad credential, so nothing can be
# told apart by length alone. A small unmarked page means we are not signed in,
# and treating it as success would leave the search retrying against a session
# that will never work.
MIN_SIGNED_IN_BYTES = 8000

# ICOS answers a case request with HTTP 200 and a "Problem Report" page when it
# cannot reach its own data source. The page keeps the heading of whichever case
# was selected last, and lists no charges and no money, so it parses perfectly
# well as a civil case with nothing owed. A criminal case quietly reported that
# way is worse than one that fails outright, so a case page has to prove it is
# the case that was asked for before it is accepted.
PROBLEM_REPORT_MARKER = "There was a communication problem"


def _text(body):
    return body.decode("utf-8", "ignore") if isinstance(body, bytes) else body


def _squashed(text):
    """ICOS pads case numbers with runs of spaces that vary between pages."""
    return "".join(text.split()).upper()


def _is_not_a_problem_report(body):
    return PROBLEM_REPORT_MARKER not in _text(body)


def _page_text(body, username=""):
    """The visible words of a short ESA page, for the log.

    ESA answers some refusals with a page carrying none of the markers below,
    and until now all we recorded was its length, which is not enough to tell a
    wrong user ID from an account that is still signed in somewhere else. The
    user ID is taken back out because it identifies the office, and a failure
    page has no reason to reach a log line naming it.
    """
    text = re.sub(r"<[^>]+>", " ", _text(body))
    text = re.sub(r"\s+", " ", text).strip()
    if username:
        text = text.replace(username, "<user id>")
    return text[:400]


def _is_page_for_case(body, case_id):
    page = _text(body)
    if PROBLEM_REPORT_MARKER in page:
        return False
    return _squashed(case_id) in _squashed(page)


def _env_seconds(name, default_minutes):
    try:
        return float(os.environ.get(name, default_minutes)) * 60
    except (TypeError, ValueError):
        return default_minutes * 60


def backoff_for(attempt):
    return BACKOFF[min(attempt, len(BACKOFF) - 1)]


def _describe(seconds):
    if seconds < 90:
        return "%ds" % round(seconds)
    return "%dm%02ds" % (int(seconds // 60), int(seconds % 60))


def _timeline(waits):
    """The backoff actually used, so an alert shows the shape of the stall."""
    if not waits:
        return "none"
    return "+".join(str(w) for w in waits) + "s"


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
                 monotonic=time.monotonic, alert=None, case_budget_seconds=None):
        self.reader = reader if reader is not None else Reader(Opener())
        self._log = log or (lambda message: None)
        # Alerting is a callback rather than an import so this module stays
        # testable without a mail path, and so alerts describe the
        # classification below rather than a raw exception.
        self._alert = alert or (lambda failure, **fields: None)
        self._sleep = sleep
        self._monotonic = monotonic
        self.budget = budget_seconds if budget_seconds is not None \
            else _env_seconds("RETRY_BUDGET_MIN", 45)
        # A stalled search is the whole job, so it is worth waiting out. A
        # stalled case is one row among twenty with staff watching a progress
        # bar, and the run can finish without it. Waiting the full search
        # budget on one case holds the other nineteen hostage for no gain, so
        # a case gets a much shorter one and is dropped if it does not clear.
        self.case_budget = case_budget_seconds if case_budget_seconds is not None \
            else _env_seconds("CASE_RETRY_BUDGET_MIN", 4)
        self.concurrent_budget = concurrent_budget_seconds \
            if concurrent_budget_seconds is not None \
            else _env_seconds("CONCURRENT_WAIT_MIN", 16)
        self.logged_in = False
        self.username = None

    def set_alert(self, alert):
        """Point alerts at a different job.

        The CRS job inherits the live session the search job created, so
        without this a case failure during CRS is filed under the search that
        produced the results list.
        """
        self._alert = alert or (lambda failure, **fields: None)

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
        budget = self.budget if what == "search" else self.case_budget
        attempt = 0
        last = "no response"
        waits = []
        endpoint = None
        while True:
            url, data = build_request()
            endpoint = url.rsplit("/", 1)[-1].split("?")[0] or "ESAWebApp"
            result = self.reader.fetch_once(url, data)
            if result.ok:
                if validate is None or validate(result.body):
                    if attempt >= SLOW_RECOVERY_ATTEMPTS:
                        # It worked, so staff see nothing. But ICOS needing this
                        # many tries is how a bad afternoon starts, and knowing
                        # before the complaints is the whole point of alerting.
                        self._alert(
                            alerts.SLOW_RECOVERY, endpoint=endpoint,
                            attempts=attempt + 1,
                            elapsed=_describe(self._monotonic() - started),
                            backoff=_timeline(waits), status=result.status,
                            note="The search went through, so nobody was told. "
                                 "ICOS is degrading.")
                    return result.body
                last = "unusable response"
            else:
                last = result.outcome.lower()

            # Only after login: before it, an unusable response is usually a
            # rejected credential working as designed, which is not news.
            if self.logged_in:
                self._alert(alerts.BAD_RESPONSE, endpoint=endpoint,
                            attempts=attempt + 1, status=result.status,
                            **{'response size': '%db' % len(result.body)})

            elapsed = self._monotonic() - started
            wait = backoff_for(attempt)
            if elapsed + wait > budget:
                self._alert(alerts.RETRY_EXHAUSTED, endpoint=endpoint,
                            attempts=attempt + 1, elapsed=_describe(elapsed),
                            backoff=_timeline(waits),
                            note="Last result: %s. Staff were told to try again "
                                 "later." % last)
                if what == "search":
                    raise IcosUnavailable(
                        "Iowa Courts Online did not respond after %d minutes of "
                        "retrying (last result: %s). The court site is likely down. "
                        "Please try again later." % (round(budget / 60), last))
                # The caller drops this one case and carries on, so this text
                # is not staff-facing advice about the whole run.
                raise IcosUnavailable(
                    "Iowa Courts Online did not return this case after %d minutes of "
                    "retrying (last result: %s)." % (round(budget / 60), last))
            self._log(self._attempt_message(what, attempt, elapsed))
            waits.append(wait)
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
        retried_trimmed = False
        while True:
            # Without the validator a problem report lands on the size check
            # below, because it is well under MIN_SIGNED_IN_BYTES, and staff
            # are told their user ID or password is wrong while the court site
            # is down. They then retype credentials that were always correct.
            body = self._retry("search",
                               lambda: self.reader.login_request(username, password),
                               validate=_is_not_a_problem_report)
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
                    self._alert(
                        alerts.CONCURRENT_EXHAUSTED,
                        account=alerts.username_prefix(username),
                        elapsed=_describe(elapsed),
                        note="ESA never released the lock. This is shared "
                             "accounts colliding, not an ICOS fault.")
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
                print("ICOS login page said: %s" % _page_text(body, username),
                      flush=True)

                # Typed on a phone, a password picks up a trailing space from
                # the keyboard or from whatever autofilled it, and ESA answers
                # an unmarked short page that is indistinguishable from a wrong
                # password. Spend one more request before telling someone their
                # working credentials are wrong.
                if password != password.strip() and not retried_trimmed:
                    retried_trimmed = True
                    password = password.strip()
                    print("ICOS login retrying without surrounding whitespace",
                          flush=True)
                    continue

                raise IcosBadCredentials(
                    "Iowa Courts Online turned down the sign in without saying "
                    "why. Check the user ID and password. If they are right, "
                    "this account may still be signed in from an earlier "
                    "search, which Iowa Courts clears on its own within about "
                    "15 minutes.")

            self.logged_in = True
            self.username = username
            self._log("Signed in to Iowa Courts Online.")
            return

    def search(self, firstname, middlename, lastname):
        self._log("Searching Iowa Courts Online...")
        # A problem report here parses as a search that matched nobody, and
        # "this person has no Iowa record" is the answer a CRS exists to give.
        # Getting that wrong is worse than getting a case wrong, so the search
        # has to prove it is a real result page before anyone believes it.
        return self._retry(
            "search",
            lambda: self.reader.search_request(firstname, middlename, lastname),
            validate=_is_not_a_problem_report)

    def case_bundle(self, case_id):
        """Summary, charges and financials for one case.

        The three pages must be fetched in this order: ICOS keys the charges and
        financials views off the case selected by the summary request.
        """
        summary = self._retry(
            "case", lambda: self.reader.case_summary_request(case_id),
            validate=lambda body: _is_page_for_case(body, case_id))
        # These two get the same proof of identity as the summary rather than
        # just the problem report check. When ICOS degrades partway through a
        # case it answers with a stub that carries no problem report wording,
        # wears the heading of some other case, and lists nothing. It looks
        # exactly like a case that genuinely has no charges and no court debt,
        # and on a criminal record those two mean opposite things.
        charges = self._retry(
            "case", self.reader.case_charges_request,
            validate=lambda body: _is_page_for_case(body, case_id))
        financials = self._retry(
            "case", self.reader.case_financials_request,
            validate=lambda body: _is_page_for_case(body, case_id))
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
