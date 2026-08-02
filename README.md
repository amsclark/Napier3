# Iowa Court Scraper

This is a tool that collects court case data from Iowa's state court case information system (ICOS) and enters it into a spreadsheet created by Iowa Legal Aid.

## Created by Alex Kornya and contractors

## Stack

The server is a [Flask app](https://flask.palletsprojects.com/) written in Python 3.13, served by gunicorn with threads (`--workers 1 --threads 12`, because the job store and the ICOS session store live in process memory).

The front end is html, css and vanilla javascript, all of it inline in the
templates. The earlier version pulled its stylesheet from a Bootstrap CDN and
jQuery from another host. Both are gone: the CSS is in the page and the scripts
are plain fetch, which is all they were ever doing through jQuery.

## Flow

Searches run as background jobs, because ICOS regularly stalls and a search that
waits it out takes longer than a web request is allowed to last. Closing the
browser does not stop a job, and the job outlives the page that started it.

### One client

* The user enters a name and clicks search
* The server starts a background search job and immediately shows a progress page
* The job logs in to ICOS, runs the search, and retries through court-side stalls, reporting what it is doing as it goes
* The progress page polls the job and sends the user to the results list when it finishes: unique names / dates of birth and the number of cases for each
* The user selects a group of cases and clicks Create CRS
* A second background job collects each case's summary, charges and financials from ICOS, builds the spreadsheet, and shows a finish page
* The finish page names the file, says how many cases went in, lists any Iowa Courts would not give up, offers a retry for those, and keeps working for two hours so a lost download does not cost another ICOS session
* The ICOS session is logged off when the CRS finishes, when the user logs out, or by a reaper if the results page is abandoned for ten minutes

### A whole clinic list

A clinic morning is twenty clients, and twenty sign-ins on a shared `ILA##`
account is twenty chances to collide with a colleague. So a list of up to 40
names, one per line as `Last, First` or `First Last`, runs on one sign-in.

Napier searches each name, shows a roster page where staff confirm which person
each name matched, then builds every workbook and offers them as a zip and
individually. One client's bad luck does not end the list: a name ICOS will not
answer, a client whose cases all refuse, or a workbook that fails to build
leaves that row with an explanation and the rest of the list carries on.

### When Iowa Courts will not answer

Napier separates a bad case from a bad site. One case that will not come off
ICOS costs one row, and the run carries on. Six cases refused in a row, counted
across the whole run rather than per client, means the site is down, and there
is no point discovering that twenty times over at four minutes a go, so the run
stops and builds from what it has. Names are counted separately at three,
because an unanswered name costs the 45 minute search budget rather than the
four minute case budget.

Six and three are both guesses about what the silence means. Sometimes ICOS says
it in words instead: a request that spent its whole budget being handed the court
site's own problem report page, which is ICOS reporting that its own data source
is unreachable, is counted a second time on its own and stops the run at two.
Sealed cases and stalls cannot produce that page, so the reason six exists is
untouched. Two rather than one, because a court site can serve one of these and
come back. The progress log says which of the two stopped the run, since one of
them means the account and the machine in front of the staffer are fine.

Anything left missing is offered back as a retry, on the run's own finish page,
for the two hours the job is kept. A retry signs in again, puts the same search
back in front of ICOS, asks only for the cases that are still missing, and
rebuilds each workbook from everything that has ever come back for that client
rather than producing a supplement to the first file.

## What the workbook carries

Beyond the CRS template's own sheets, Napier writes three things of its own.

**ACTION LIST** is the sheet that opens. It carries the clinic date, the number
of cases read, the total owed, the payment record, whether anything on the
record can hold up a vehicle registration, and what is not in the file. That
last line matters because the file outlives every page that would otherwise say
so: a workbook gets emailed to the attorney taking the case and opened weeks
later by someone who never watched it run, and one that is quietly two cases
short is worse than one that says it is two cases short. Below that is a ranked
list of the arguments Napier can spot on the record, each with the authority it
rests on and the facts it was read from.

**PAYMENTS** is every payment ICOS itemized across every case, with the date,
amount, receipt and how it was paid.

**Ability to pay** is two figures shown on the finish page rather than written
into the file: the court debt balance and what has been going toward it each
month, averaged over the last 12 months of what ICOS recorded. They are the two
inputs the calculator at abilitytopay.org asks for that a client sitting in a
clinic cannot answer from memory. They are read from the same numbers the
workbook was built from, so the screen cannot disagree with the file. A client
ICOS itemized no payments for is reported as having no itemization rather than
as having paid zero, because a hearing where someone is recorded as paying
nothing on a debt they have been paying goes the wrong way.

## Why the retries and the logoff matter

Two different failures were making a majority of searches fail in mid-2026:

* ICOS holds the first connections from an idle source IP for ~30 seconds, which surfaced as Heroku's "Application error". A keepalive thread keeps this dyno's path to ICOS warm, and every request retries with backoff.
* ESA allows one session per account and offers no way to force another session off. The app used to leave sessions open, so shared `ILA##` accounts collided and ICOS returned empty pages. Sessions are now always released.

`icos.py` classifies each attempt, because these need opposite handling: a stall
should be retried, a concurrent login should be waited out on a slow cadence,
and a wrong password should fail at once.

A run that had to work for its answers says so in its own progress log, in the
form "Iowa Courts answered 61 first time, 6 on try 2, 1 on try 4." That line is
the difference between a slow morning and a degrading court site, and it costs
nothing to carry.

### Cases cannot be pulled in parallel

`TViewCharges` and `TViewFinancials` take no parameters at all. The case they
answer about is server-side state, set by the last `TViewCaseCivil?caseid=`
request. Checked against the live site on 2026-08-01: select case A, select case
B, read charges, and the charges are B's. Two case pulls sharing a session
interleave into one wrong case, and a second session means a second ESA account,
which locks out a colleague. Threads and aiohttp change nothing here.

It does not matter much. Measured against the real site over 209 requests, the
median page came back in 0.18s and a whole case in 0.81s, so 68 cases were 53
seconds of actual work. What makes a run long is waiting out a stalled ICOS,
and doing that on more connections at once would make it worse.

### Money figures come from the ICOS summary

The itemization at the bottom of an ICOS financial page lists original
assessments only; payments and the third-party (Linebarger) collection fee that
ICOS excludes from the balance appear only in the summary at the top. The
summary is therefore what the CRS reports. If the categories still do not
reconcile with the ICOS total, the row is flagged and column U (the ICOS total)
is the figure to trust.

## What may leave the building

The client's name and date of birth are privileged and never leave the machine
that ran the search. Case numbers are court public record and travel freely,
which is what makes an alert about a case worth reading.

That rule decides a lot of the design. Alerts carry case numbers and never
people. Exception messages are dropped, because a parser that dies on a case
tends to quote that case back; what survives is the traceback's own frames plus
the exception type. The ESA user ID is reduced to its `ILA##` family before it
reaches an alert, and the password never appears anywhere. The ability-to-pay
figures stay on the screen of whoever ran the search and are kept out of the
progress log, because the log is what alert mail carries out. Nothing about a
client is written to browser storage, since the machine is shared. Each of these
guards has a test that fails when the guard is removed.

The one thing Napier ever sends out whole is the page ICOS serves when it has
declared itself down, and it goes redacted. See "Proving an outage to the
court" under Alerts for what comes out of it first and why the rest may stay.

## Production

The application runs on [Heroku](https://www.heroku.com/). `crs-napier` is production; `napier-dev` is staging and auto-deploys from `main`.

Config vars:

* `SECRET_KEY` (required) - signs session cookies
* `RETRY_BUDGET_MIN` (default 45) - how long a job keeps retrying a stalled ICOS search
* `CASE_RETRY_BUDGET_MIN` (default 4) - the same for one case, kept short because a run has many cases and only one search
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

Jobs are kept for two hours after they finish, which is what makes a finish page
survive a closed laptop. ICOS sessions held between a search and a CRS are reaped
after ten minutes idle.

Useful log filters: `heroku logs -a crs-napier | grep -E 'ICOS|KEEPALIVE|JOB|ALERT'`.

### Alerts

Napier runs unattended. When a search dies after forty-five minutes of retrying,
or a case will not parse, the only record used to be a line in `heroku logs`
that got read after staff complained rather than before. Napier now emails the
detail needed to diagnose a failure without shelling into Heroku.

What sends mail: ICOS exhausting the retry budget, an ESA account still locked
when the wait gives up, a run that succeeded but needed three or more attempts,
an unusable response once signed in, ICOS not answering at all, a case that
would not parse, a case ICOS would not retrieve, a job crashing, an unhandled
exception in a request, a party role or a disposition ICOS used that Napier
does not recognise, a workbook built and never collected, and a progress page
that lost contact with the server.

An unusable response and no answer at all are separate subject lines, and each
carries the reason as its first line. They used to be one class with a size
field on it, so a timeout, an empty body, a transport error, an ICOS problem
report page and a page for the wrong case all arrived worded identically, and
since it is one email per class per job, whichever happened first silenced the
rest. On 2026-08-01 that meant one email saying "unusable response, 0b", which
is what a timeout looks like, while the digest for the same run listed a 3407
byte reply carrying a 200 under that same subject line. Which of the five that
body was is no longer recoverable, because the one email that would have said so
had already been spent on something else. A court site that is down and a
session that has lost its place look the same from outside, and only one of them
is Iowa's fault.

The last two are the ones nothing else would catch, because the server thinks
those runs went fine. A staffer whose phone drops the progress page sees a
finished run as a broken one and pulls the same cases again, and every
server-side signal stays quiet because nothing server-side went wrong.

A bad password does not send mail, because it is always a typo. Nor does a cold
keepalive, which is the normal state of an idle path to ICOS and recovers on its
own; paging on it would train everyone to ignore Napier's mail.

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

The keepalive used to log every ping, which was 4,300 lines a day and buried
everything worth reading. It now logs state changes and a heartbeat every ten
minutes.

### Proving an outage to the court

Every alert above is Napier's account of a bad morning, and Napier's account is
the thing a court would question. So when ICOS answers with the page that says
in its own wording that it cannot reach its own data source, Napier keeps that
page and emails a copy, at most once every fifteen minutes across the whole
dyno. The mail carries the time in UTC and local, the endpoint, the case that
was requested, the HTTP status, the attempt count, and a sha256 of the page as
ICOS served it. That is a report Iowa Courts can act on rather than a
complaint.

Only that one page type is ever attached. Napier can recognise it, so it knows
what is on it and can take out what has to come out. A real case page names a
defendant and a search results page lists everyone who matched with their dates
of birth, and neither is a shape worth guessing at, so neither is ever sent.

Three things are withheld from the copy and nothing else is: the case caption,
which on a clinic run is the client's own case, since ICOS keeps serving the
heading of whichever case the session selected last; any date; and the account
ICOS stamps into the corner of every page it serves. None of them bear on
whether ICOS was up, so nothing evidential is lost. The sha256 is of the page
before any of that came out, so the redacted copy can still be tied to the
original if it ever has to be.

The scrubbing is checked rather than trusted. Napier reads its own output back
with a second set of patterns written from the other direction, and if anything
it was supposed to remove is still there, no mail goes out at all. An email
that never arrives costs a follow-up. One that arrives carrying a client's name
cannot be recalled from a court's inbox.

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
the clinic list and the retry path, the financial reconciliation against a
fixture built from a real case, the action list against the CRS template's own
formulas, and alerting against a real local HTTP server, so an alert is proven
to leave the process rather than proven to have been asked to.

Fixtures use the synthetic case number `00000 FECR000000` and no real person,
because this repository is public.

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
