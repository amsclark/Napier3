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

Useful log filters: `heroku logs -a crs-napier | grep -E 'ICOS|KEEPALIVE|JOB'`.

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
and the financial reconciliation against a fixture built from a real case.

Raw html from ICOS is written to `tmp/` as searches run, which is useful when
working on the parsers.
