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

Alert mail has never been exercised on production, because the release before
this one had no alerting in it. It is worth deliberately provoking one failure
after the deploy, by signing in with a wrong password, and confirming the mail
arrives.

## Going back

Every release is kept, so rolling back is a release number rather than a revert
commit:

```
heroku releases -a crs-napier
heroku rollback vNN -a crs-napier
```

Release v55 is the last one before this work landed. A rollback restarts the
dyno, so the same warning about in-flight searches applies.
