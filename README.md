# Iowa Court Scraper

This is a tool that collects court case data from Iowa's state court case information system (ICOS) and enters it into a spreadsheet created by Iowa Legal Aid.

## Created by Alex Kornya and contractors

## Stack

The server is a [Flask app](https://flask.palletsprojects.com/) written in Python 3, served by gunicorn with threads. The front end is html and javascript using jQuery and Bootstrap.

## Flow

Searches run as background jobs, because ICOS regularly stalls and a search that
waits it out takes longer than a web request is allowed to last.

* The user enters a name and clicks search
* The server starts a background search job and immediately shows a progress page
* The job logs in to ICOS, runs the search, and retries through court-side stalls, reporting what it is doing as it goes
* The progress page polls the job and sends the user to the results list when it finishes: unique names / dates of birth and the number of cases for each
* The user selects a group of cases and clicks Create CRS
* A second background job collects each case's summary, charges and financials from ICOS, builds the spreadsheet, and offers it for download. Closing the browser does not stop it.
* The ICOS session is logged off when the CRS finishes, when the user logs out, or by a reaper if the results page is abandoned

### Why the retries and the logoff matter

Two different failures were making a majority of searches fail in mid-2026:

* ICOS holds the first connections from an idle source IP for ~30 seconds, which surfaced as Heroku's "Application error". A keepalive thread keeps this dyno's path to ICOS warm, and every request retries with backoff.
* ESA allows one session per account and offers no way to force another session off. The app used to leave sessions open, so shared `ILA##` accounts collided and ICOS returned empty pages. Sessions are now always released.

`icos.py` classifies each attempt, because these need opposite handling: a stall
should be retried, a concurrent login should be waited out on a slow cadence,
and a wrong password should fail at once.

### Money figures come from the ICOS summary

The itemization at the bottom of an ICOS financial page lists original
assessments only; payments and the third-party (Linebarger) collection fee that
ICOS excludes from the balance appear only in the summary at the top. The
summary is therefore what the CRS reports. If the categories still do not
reconcile with the ICOS total, the row is flagged and column U (the ICOS total)
is the figure to trust.

## Production

The application runs on [Heroku](https://www.heroku.com/). `crs-napier` is production; `napier-dev` is staging and auto-deploys from `main`.

Config vars:

* `SECRET_KEY` (required) - signs session cookies
* `RETRY_BUDGET_MIN` (default 45) - how long a job keeps retrying a stalled ICOS
* `CONCURRENT_WAIT_MIN` (default 16) - how long to wait out a locked ESA account
* `NAPIER_DISABLE_BACKGROUND` - set to `1` to skip the keepalive and reapers (tests)
* `NAPIER_DUMP_HTML` - set to `1` to write scraped ICOS pages to `tmp/`. Off by
  default, and it should stay off in production: those pages are the unredacted
  court record for everyone a search matched
* `MAILGUN_DOMAIN` - the Mailgun sending domain, for failure alerts
* `MAILGUN_API_KEY` - the Mailgun private API key
* `ALERT_EMAIL_TO` - where failure alerts go; leaving any of these three unset
  turns alerting into a logged no-op
* `ALERT_EMAIL_FROM` (optional) - defaults to `Napier <napier@MAILGUN_DOMAIN>`

Useful log filters: `heroku logs -a crs-napier | grep -E 'ICOS|KEEPALIVE|JOB|ALERT'`.

### Alerts

Napier runs unattended. When a search dies after forty-five minutes of retrying,
or a case will not parse, the only record used to be a line in `heroku logs`
that got read after staff complained rather than before. Napier now emails the
detail needed to diagnose a failure without shelling into Heroku.

Seven things send mail: ICOS exhausting the retry budget, an ESA account still
locked when the wait gives up, a run that succeeded but needed three or more
attempts, an unusable response once signed in, a case that would not parse, a
job crashing, and an unhandled exception in a request. A bad password does not,
because it is always a typo. Nor does a cold keepalive, which is the normal
state of an idle path to ICOS and recovers on its own; paging on it would train
everyone to ignore Napier's mail.

Sending is one POST to the Mailgun HTTP API over `urllib`, on a daemon thread
with its own timeout. No SMTP handshake to stall a worker, no dependency, no
Heroku add-on. A failed send is logged and dropped, because a mail outage must
not become a failed search.

An alert that repeats is an alert that gets muted. A forty-five minute retry
budget at escalating backoff is dozens of attempts, and a clinic morning puts
several staff behind the same broken ICOS. So it is one email per job per
failure class, with a floor of one per class per ten minutes across all jobs on
top, and one digest when the job ends carrying every event including the ones
that were suppressed.

Alerts carry case numbers but never people. A case number is court public record
and a parse-failure alert without one gives nobody anything to look at. The
defendant's name and date of birth are the privileged part and are never
assembled into an alert. Exception messages are dropped for the same reason: a
parser that dies on a case tends to quote that case back. What survives is the
traceback's frames, which are our own source, plus the exception type. Both
guards have tests that fail when the guard is removed.

The keepalive used to log every ping, which was 4,300 lines a day and buried
everything worth reading. It now logs state changes and a heartbeat every ten
minutes.

## Development

You'll need git, an editor, and Python 3.13.

    python3 -m venv .venv
    .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python app.py

Open http://127.0.0.1:5000/ and use the website to create a spreadsheet.

### Tests

    NAPIER_DISABLE_BACKGROUND=1 .venv/bin/python -m pytest tests/ -q

`tests/` covers the retry engine (with virtual time, so no real waiting), the
job engine and session reaper, the staff-facing flow against a fake court site,
the financial reconciliation against a fixture built from a real case, and
alerting against a real local HTTP server, so an alert is proven to leave the
process rather than proven to have been asked to.

Set `NAPIER_DUMP_HTML=1` to have raw ICOS html written to `tmp/` as searches
run, which is useful when working on the parsers. It is off by default. A
search results page lists every person whose name matched, with their dates of
birth, so a production dyno that wrote one held privileged client data on local
disk for as long as it stayed up, for nobody's benefit.

### A name search is not a person search

One real search during development returned 120 rows covering nine different
people who shared a surname. `parse_search` is what separates them, and the
results page is where staff pick the one they meant. `tests/fixtures/
search_results_sample.html` is that page, scrubbed, cut to one row per shape
that changes the parser's mind.
