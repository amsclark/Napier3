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
import urllib.parse

import accounts
import alerts
import evidence
from opener import Opener
from reader import EMPTY, TIMEOUT, Reader

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
#
# The marker itself lives in evidence.py, because recognising this page and
# being allowed to mail a copy of it are the same question, and two copies of
# the string would be two things to keep in step.
PROBLEM_REPORT_MARKER = evidence.PROBLEM_REPORT_MARKER


def _text(body):
    return body.decode("utf-8", "ignore") if isinstance(body, bytes) else body


def _squashed(text):
    """ICOS pads case numbers with runs of spaces that vary between pages."""
    return "".join(text.split()).upper()


# Why a body was refused, in the words the alert carries. A validator answers
# None when the page is good and one of these when it is not.
#
# There used to be no such answer. Every refusal reached the alert under one
# subject line with a size field, and a request that never came back at all
# reached it the same way with "0b", which reads as a page that arrived wrong
# rather than one that never arrived. Five causes, one email, and since
# alerts.record only sends the first of each class per run, whichever happened
# first silenced the rest. On 2026-08-01 that meant one email about a timeout,
# while the digest for the same run listed a 3407 byte reply carrying a 200 that
# had been filed under that same subject line. Which of the five that body was
# cannot be established from anything that was sent, which is the defect itself.
PROBLEM_REPORT_REASON = ("ICOS problem report page, meaning its own data source "
                         "was unreachable")
WRONG_CASE_REASON = "a page for a different case than the one asked for"
# Seen against the live site on 2026-08-01: ICOS answers a case request with a
# 200 and nothing in it when the session has not selected that case through a
# search first. Worth naming, because a session that has lost its place and a
# court site that is down are the same event from the outside, and only one of
# them is Iowa's fault.
NO_BODY_REASON = "HTTP 200 with an empty body"
NO_ANSWER_REASON = "no answer inside the request timeout"


def _refusal_reason(result):
    """Why a request that produced no usable body produced none."""
    if result.outcome == TIMEOUT:
        return NO_ANSWER_REASON
    if result.outcome == EMPTY:
        return NO_BODY_REASON
    return result.detail or "the request failed before any body arrived"


def _problem_report_reason(body):
    if PROBLEM_REPORT_MARKER in _text(body):
        return PROBLEM_REPORT_REASON
    return None


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


def _case_page_reason(body, case_id):
    page = _text(body)
    if PROBLEM_REPORT_MARKER in page:
        return PROBLEM_REPORT_REASON
    if _squashed(case_id) not in _squashed(page):
        return WRONG_CASE_REASON
    return None


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


def _requested_case(url):
    """The case id in a case request, for evidence that names what was asked.

    Read back off the wire rather than plumbed down from the caller, because
    what matters on an outage page is the gap between the case requested and
    the stale case the page came back wearing, and this is the requested one as
    ICOS received it.
    """
    match = re.search(r"[?&]caseid=([^&]*)", url or "", re.I)
    return urllib.parse.unquote_plus(match.group(1)) if match else None


# Said in one place because the run can stop in three, and a staffer who stops
# a run wants to know the shared account is free, not that something failed.
STOPPED_MESSAGE = ("Stopped at your request. Iowa Courts Online has been signed "
                   "out, so the account is free for the next search straight "
                   "away.")


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
    """ICOS would not hand something over inside its retry budget.

    court_site_down says ICOS gave the reason itself rather than leaving it to
    be worked out. A problem report page is the court site reporting that its
    own data source is unreachable, which is a different quality of evidence
    from a request that simply never came back, and the run's outage counter is
    allowed to believe it sooner.
    """

    def __init__(self, message, court_site_down=False):
        super().__init__(message)
        self.court_site_down = court_site_down


class IcosStopped(IcosError):
    """A staffer asked for the run to stop and it did.

    Not a failure of anything, but it travels the same way as one so the run
    unwinds through the same finally that logs the ESA session off.
    """


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
        # Asked between attempts. A stalled case is waited out for four minutes
        # and a stalled search for forty-five, which is far too long to make
        # somebody sit through once they have decided to stop.
        self._should_stop = lambda: False
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
        # Which attempt each request finally landed on, and how many were given
        # up on. Measured against the real site on 2026-08-01, a healthy ICOS
        # answered 314 of 314 requests first time, with the slowest taking
        # 1.31s against an 8 second timeout. So the case budget of four minutes
        # is not there for a slow site, it is there for a sick one, and nobody
        # has ever seen what a sick one does: whether a case that fails six
        # times ever comes back on the seventh, or whether the four minutes are
        # spent proving something that was decided in the first ten seconds.
        # That is 23 minutes of a clinic during an outage, so it is worth
        # knowing rather than guessing. This is the counting.
        self.landed_on = {}
        self.given_up = 0
        # The registry entry for the ESA account this client holds, so the next
        # staffer to collide with it can be told what is holding it.
        self._account_handle = None

    def set_alert(self, alert):
        """Point alerts at a different job.

        The CRS job inherits the live session the search job created, so
        without this a case failure during CRS is filed under the search that
        produced the results list.
        """
        self._alert = alert or (lambda failure, **fields: None)

    def set_log(self, log):
        """Point the progress log at a different job, for the same reason.

        Missing this is worse than missing set_alert, because it is the only
        thing the person waiting can see. A staffer watching a CRS run whose
        first case ICOS would not give up sat on "Pulling case 1 of 67" for
        four minutes with nothing under it: the retry notices that say Iowa
        Courts is being retried were being written into the search job, which
        no page is showing by then. It read as a hang, and it was a wait.
        """
        self._log = log or (lambda message: None)

    def set_stop_check(self, should_stop):
        """Give the retry loop a way to be told to give up waiting."""
        self._should_stop = should_stop or (lambda: False)

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

        validate() may inspect a successful body and raise, or return a short
        reason to treat the response as a retryable failure. It returns None
        when the body is good. The reason is what the alert carries, so it is
        written to be read in an email at four in the afternoon.
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
                reason = validate(result.body) if validate is not None else None
                if reason is None:
                    self.landed_on[attempt + 1] = self.landed_on.get(attempt + 1, 0) + 1
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
                # ICOS answered and the answer was wrong. Kept apart from the
                # case below all the way to the subject line, because the two
                # call for opposite things: this one is the court site's data,
                # that one is the path to it.
                failure = alerts.BAD_RESPONSE
                extra = {'response size': '%db' % len(result.body)}
                last = "unusable response"
            else:
                reason = _refusal_reason(result)
                failure = alerts.NO_ANSWER
                # No body arrived, so its length is not a fact about anything.
                # Reporting it as 0b was the whole reason a timeout and a
                # problem report page read identically in the inbox.
                extra = {}
                last = result.outcome.lower()

            # Only after login: before it, an unusable response is usually a
            # rejected credential working as designed, which is not news.
            if self.logged_in:
                self._alert(failure, endpoint=endpoint, reason=reason,
                            attempts=attempt + 1, status=result.status, **extra)

            # An alert says Napier could not get a case. That is Napier's word
            # for it, and Napier's word is the thing in question when a clinic
            # tells the court it lost a morning. When ICOS has said in its own
            # wording that it cannot reach its own data, keep the page.
            if evidence.is_outage_page(result.body):
                alerts.outage_evidence(
                    result.body, endpoint=endpoint,
                    case_id=_requested_case(url), username=self.username,
                    status=result.status, attempts=attempt + 1)

            elapsed = self._monotonic() - started
            wait = backoff_for(attempt)
            if elapsed + wait > budget:
                self.given_up += 1
                self._alert(alerts.RETRY_EXHAUSTED, endpoint=endpoint,
                            attempts=attempt + 1, elapsed=_describe(elapsed),
                            backoff=_timeline(waits), reason=reason,
                            note="Last result: %s. Staff were told to try again "
                                 "later." % reason)
                # Carried out to the caller because the run's outage counter
                # wants it: six cases that never arrived is a guess about the
                # site, two that spent their whole budget being told ICOS
                # cannot reach its own data is not.
                declared = reason == PROBLEM_REPORT_REASON
                if what == "search":
                    raise IcosUnavailable(
                        "Iowa Courts Online did not respond after %d minutes of "
                        "retrying (last result: %s). The court site is likely down. "
                        "Please try again later." % (round(budget / 60), last),
                        court_site_down=declared)
                # The caller drops this one case and carries on, so this text
                # is not staff-facing advice about the whole run.
                raise IcosUnavailable(
                    "Iowa Courts Online did not return this case after %d minutes of "
                    "retrying (last result: %s)." % (round(budget / 60), last),
                    court_site_down=declared)
            # Checked here rather than only between cases, because the whole
            # reason somebody reaches for stop is that a wait is in progress.
            if self._should_stop():
                raise IcosStopped(STOPPED_MESSAGE)
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

        # Said before ICOS is asked, not after it refuses. If Napier is holding
        # this account itself then the refusal is already certain and the wait
        # is already started, and the sentence is worth more now than it is
        # seventy-five seconds from now.
        held_by_napier = accounts.describe(username)
        if held_by_napier:
            self._log(held_by_napier)

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
                               validate=_problem_report_reason)
            text = body.decode("utf-8", errors="ignore")

            if BAD_CREDS_MARKER in text:
                raise IcosBadCredentials(
                    "Iowa Courts Online did not accept that user ID or password.")

            if CONCURRENT_MARKER in text:
                # ESA offers no way to clear the other session -- EPALogout only
                # ends our own, which is not the one holding the lock. Waiting is
                # the only thing that works.
                elapsed = self._monotonic() - started
                # Asked again rather than reused, because a run can pick this
                # account up or put it down while somebody is waiting on it.
                napier_holds = accounts.describe(username)
                if elapsed + CONCURRENT_INTERVAL > self.concurrent_budget:
                    self._alert(
                        alerts.CONCURRENT_EXHAUSTED,
                        account=alerts.username_prefix(username),
                        elapsed=_describe(elapsed),
                        note="Napier's own run held the account for the whole "
                             "wait. Two staff on one login."
                             if napier_holds else
                             "ESA never released the lock, and Napier was not "
                             "holding the account. Somebody is signed in to "
                             "Iowa Courts outside Napier.")
                    if napier_holds:
                        raise IcosAccountLocked(
                            "This Iowa Courts account has been busy with another "
                            "Napier run for the whole wait. Whoever started it can "
                            "stop it from their own progress page, or you can sign "
                            "in with a different Iowa Courts account.")
                    raise IcosAccountLocked(
                        "This Iowa Courts account is still logged in from another "
                        "session and Iowa Courts has not released it. Try again in a "
                        "few minutes, or use your own Iowa Courts account so searches "
                        "do not collide.")
                if not waited_for_lock:
                    if napier_holds and held_by_napier:
                        # Named a moment ago, before ICOS was even asked. Saying
                        # the same paragraph twice reads as the page repeating
                        # itself rather than as progress.
                        self._log("Waiting for that run to finish. Nothing is lost; "
                                  "this search starts as soon as it does.")
                    elif napier_holds:
                        self._log(napier_holds)
                    else:
                        self._log(
                            "This Iowa Courts account is already logged in somewhere "
                            "else, and not by Napier. Waiting for that session to "
                            "clear -- Iowa Courts releases it within about 15 "
                            "minutes. Nothing is lost; the search will run as soon "
                            "as the account frees up.")
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
            # Registered here rather than at the top of login, because until
            # ESA answers with the search screen Napier is not holding
            # anything and pointing the next staffer at a session that does
            # not exist is worse than telling them nothing.
            self._account_handle = accounts.hold(username)
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
            validate=_problem_report_reason)

    def case_bundle(self, case_id):
        """Summary, charges and financials for one case.

        The three pages must be fetched in this order: ICOS keys the charges and
        financials views off the case selected by the summary request.

        Which also settles whether cases could be pulled concurrently, and they
        cannot. TViewCharges and TViewFinancials take no parameters at all, so
        the only thing saying which case they answer about is server state.
        Checked against the real site on 2026-08-01: selecting case A, then
        case B, then reading charges returns B's charges. Two case pulls
        sharing a session interleave into one wrong case, and a second session
        means a second ESA account, which locks a colleague out. Threads and
        aiohttp change nothing here.
        """
        summary = self._retry(
            "case", lambda: self.reader.case_summary_request(case_id),
            validate=lambda body: _case_page_reason(body, case_id))
        # These two get the same proof of identity as the summary rather than
        # just the problem report check. When ICOS degrades partway through a
        # case it answers with a stub that carries no problem report wording,
        # wears the heading of some other case, and lists nothing. It looks
        # exactly like a case that genuinely has no charges and no court debt,
        # and on a criminal record those two mean opposite things.
        charges = self._retry(
            "case", self.reader.case_charges_request,
            validate=lambda body: _case_page_reason(body, case_id))
        financials = self._retry(
            "case", self.reader.case_financials_request,
            validate=lambda body: _case_page_reason(body, case_id))
        return summary, charges, financials

    def retry_summary(self):
        """How hard this session had to work, or None if it did not have to.

        Silent on a healthy run. "Everything answered first time" printed after
        every run is how a log gets stopped being read, and this line is only
        worth anything if it is unusual enough that somebody looks at it.

        It goes in the progress log rather than only to stdout because the
        progress log is what alert emails carry, so the one run that went badly
        arrives already carrying the shape of how it went badly.
        """
        slow = sorted(n for n in self.landed_on if n > 1)
        if not slow and not self.given_up:
            return None
        # "requests", not a bare number. These count page requests, and a
        # case costs three of them, so the bare version sat under a line about
        # 22 cases that could not be pulled and read as though four of them
        # had worked. None of them had.
        parts = ["%d first time" % self.landed_on.get(1, 0)]
        for n in slow:
            parts.append("%d on try %d" % (self.landed_on[n], n))
        line = ("Of the page requests this run made, Iowa Courts answered "
                + ", ".join(parts))
        if self.given_up:
            line += ", and never answered %d" % self.given_up
        return line + "."

    def logoff(self):
        """Release the ESA session.

        Leaving sessions open is what makes shared accounts collide, so this is
        best-effort but never skipped -- and never allowed to raise, since it
        runs in cleanup paths.
        """
        # Before the early return, because a session that never got logged in
        # is a session that spent its whole life retrying, which is exactly the
        # run worth having the numbers from.
        summary = self.retry_summary()
        if summary:
            self._log(summary)
        if not self.logged_in:
            return
        try:
            url, data = self.reader.logoff_request()
            self.reader.fetch_once(url, data)
        except Exception as e:
            print("ICOS logoff failed: %s" % type(e).__name__, flush=True)
        finally:
            self.logged_in = False
            # Released even when the request above failed. ESA may well still
            # be holding the session, but no Napier run is, and the entry
            # exists to point the next staffer at a run they can go and stop.
            # Pointing them at one that has already finished is worse than
            # saying nothing.
            accounts.release(self._account_handle)
            self._account_handle = None
