# Deploying to production

Two Heroku apps run this code.

`napier-dev` is staging. It deploys itself from GitHub whenever `main` moves, so
whatever is on `main` is what staff are looking at when they test.

`crs-napier` is production, and it has no such hook. Nothing reaches it without
someone pushing on purpose. That is deliberate: merging a pull request should
never be the thing that changes what Iowa Legal Aid staff are using in the
middle of a working day.

## Before pushing

Staff need to have signed off on staging, and the sign-off needs to be against
the same commit that is about to go out. Check that they match:

```
git ls-remote https://github.com/amsclark/Napier3.git refs/heads/main
heroku releases -a napier-dev -n 1
```

Rotate `SECRET_KEY` on `crs-napier` if it has not been rotated recently. It
signs the session cookie that carries the ICOS session token and gates the CRS
download, so a known key lets someone forge one. Rotating it signs everyone out
and nothing else.

Confirm that alert mail is configured, because a release that cannot tell anyone
it is failing is worse than the one before it:

```
heroku config -a crs-napier | grep -E 'MAILGUN|ALERT'
```

`MAILGUN_DOMAIN`, `MAILGUN_API_KEY` and `ALERT_EMAIL_TO` are the three that are
read. `ALERT_EMAIL` is also set and nothing reads it, so unset it while you are
here rather than leaving a variable that looks load-bearing and is not:

```
heroku config:unset ALERT_EMAIL -a crs-napier
```

Nothing else needs setting. The retry budgets (`RETRY_BUDGET_MIN`,
`CASE_RETRY_BUDGET_MIN`, `CONCURRENT_WAIT_MIN`) all have defaults and are meant
to be absent.

Pick a quiet hour. A deploy restarts the dyno, which ends any search that is
running at that moment.

## Pushing

Production's Heroku remote tracks `master`, not `main`, and it currently holds a
real commit from this repository rather than a synthetic one, so this is an
ordinary fast-forward. It does not need `--force`, and if it ever asks for
`--force` then something is wrong and the answer is to stop, not to force it.

```
git fetch origin
git push https://git.heroku.com/crs-napier.git origin/main:master
```

## Checking it worked

```
heroku releases -a crs-napier -n 2
heroku logs -a crs-napier --tail
```

The boot should log `KEEPALIVE started`. Then sign in through the web interface
and run one search against a name that is known to return cases.

Watch the shutdown of the old dyno in the log while you are there. It should say
`Handling signal: term` and reach `Shutting down: Master` inside a second. If it
instead sits for thirty seconds and ends in `Error R12` and a `SIGKILL`, then
whatever the app was holding was not handed back, and the shared ICOS account
will stay locked for about fifteen minutes.

### Checking that alert mail works

Do not test this with a wrong password. Napier alerts on failures staff cannot
act on, and it decides that by whether the exception carries a message meant for
them (`jobs.py`, in the `except` around the job body). A bad password carries
one, so it is shown on screen and deliberately never emailed. Provoking a login
failure and waiting for mail proves nothing, and the mail that does not arrive
reads exactly like alerting being broken.

Two checks that do work, cheapest first.

The mail path in the code that is now deployed:

```
heroku run --no-tty --exit-code -a crs-napier -- python -c "import alerts; alerts._deliver('Napier cutover check', 'Sent by hand after deploying to production.')"
```

The dyno prints `ALERT sent:` on success and `ALERT delivery failed` otherwise.
`_deliver` swallows the exception either way, so that line is the only tell.

Then one check that goes through the real trigger rather than the mail helper.
Sign in, run a CRS for a name known to return cases, and when the finish page
appears do not download the workbook. Five minutes later a `workbook was built
but never collected` alert should arrive. That is the alert added because a
staffer lost a finished run on a phone and nothing server-side noticed, so it is
the one worth proving end to end.

Run that one once. Each kind of failure is rate limited to one email every ten
minutes across the whole app, so a second attempt inside that window sends
nothing, which looks like the first one having been a fluke.

The Mailgun configuration and the egress path out of `crs-napier` were already
confirmed working on 2026-07-31, by sending a hand-built request from a
production dyno using production's own config and checking the Mailgun events
API for `delivered` rather than `accepted`. What has never run on production is
the code, which is what these two checks are for.

## Going back

Every release is kept, so rolling back is a release number rather than a revert
commit:

```
heroku releases -a crs-napier
heroku rollback vNN -a crs-napier
```

Release v55 is the last one before this work landed. A rollback restarts the
dyno, so the same warning about in-flight searches applies.
