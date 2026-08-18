from flask import Flask, jsonify, render_template, request, send_file, session, url_for, redirect
import os
import platform
import time

import alerts
import crs
import icos_sessions
import jobs
import roster
import tasks

app = Flask(__name__)
# A guessable secret key lets anyone forge session cookies (which hold the ICOS
# session token and gate the CRS download). Set SECRET_KEY in the app config;
# the urandom fallback keeps sessions unforgeable but resets them on dyno
# restart.
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(24)

tmp_dir = '/tmp/'
if platform.system() == 'Windows':
    tmp_dir = '.\\tmp\\'

RESTARTED_MESSAGE = ("Napier restarted and this search is no longer available. "
                     "Please run the search again.")

# Spellings of one client's name on the single search form. Each one is a
# search, each search holds the shared Iowa Courts account, and past a handful
# the thing to do is search the surname on its own rather than guess at the
# spellings one at a time.
MAX_SPELLINGS = 6

# --- ICOS connection keepalive -------------------------------------------
# ICOS's edge throttles/tarpits the first connection(s) from an idle source IP
# (this Heroku dyno), so a user's first search after a quiet spell stalls ~30s
# and surfaces as Heroku's "Application error". Keep THIS dyno's path to ICOS
# warm by pinging the login page on a timer from inside the web process, so it
# shares the web dyno's egress IP (an external pinger would warm a different IP
# and do nothing). Single-instance guard so multiple gunicorn workers don't
# each ping.
import threading
import socket as _socket
import urllib.request as _urlreq

KEEPALIVE_URL = "https://www.iowacourts.state.ia.us/ESAWebApp/ESALogin.jsp"
KEEPALIVE_SECS = 20
KEEPALIVE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
KEEPALIVE_BURST = 8
# A ping every 20 seconds logging a line each time is 4,300 lines a day, which
# buries the job and ICOS lines that someone reads a log to find. Every change
# of state is logged; a steady healthy ping only says so this often.
KEEPALIVE_LOG_SECS = 10 * 60
_keepalive_lock = None
_keepalive_state = {"ok": None, "logged_at": 0.0, "cold_since": None}

def _keepalive_ping(timeout=6):
    t0 = time.time()
    try:
        r = _urlreq.urlopen(_urlreq.Request(KEEPALIVE_URL, headers={"User-Agent": KEEPALIVE_UA}), timeout=timeout)
        r.read()
        return time.time() - t0, True
    except Exception:
        return time.time() - t0, False

def _describe_duration(seconds):
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return "%d minute%s" % (minutes, "" if minutes == 1 else "s")
    hours = minutes / 60.0
    return "%.1f hours" % hours

def _keepalive_cycle(state, now=None):
    # ICOS tarpits the first connection(s) from an idle source IP, and a single
    # ping does NOT warm a cold path (it takes several attempts). So ping often,
    # and the moment a ping comes back cold, BURST until it warms again, instead
    # of waiting a full cycle. Keeps the web dyno's path to ICOS continuously
    # warm so real searches don't land on a cold start.
    #
    # One iteration, split out of the loop below so the alerting can be tested
    # without waiting on a timer.
    now = time.time() if now is None else now
    dt, ok = _keepalive_ping()
    if not ok:
        for i in range(KEEPALIVE_BURST):
            dt, ok = _keepalive_ping()
            if ok:
                print("KEEPALIVE re-warmed after %d burst tries (%.2fs)" % (i + 1, dt), flush=True)
                break
        else:
            print("KEEPALIVE still cold after burst", flush=True)

    # Deliberately does not alert. A cold keepalive is the normal state of an
    # idle path to ICOS and it recovers on its own, so paging on it would train
    # everyone to ignore Napier's email. Staff-visible failures are alerted
    # where they happen, in the retry classifier and the job engine.
    if ok:
        if state["cold_since"] is not None:
            print("KEEPALIVE warm again after %s"
                  % _describe_duration(now - state["cold_since"]), flush=True)
            state["cold_since"] = None
        if state["ok"] is not True or now - state["logged_at"] >= KEEPALIVE_LOG_SECS:
            print("KEEPALIVE ok %.2fs" % dt, flush=True)
            state["logged_at"] = now
    elif state["cold_since"] is None:
        state["cold_since"] = now
    state["ok"] = ok
    return state

def _keepalive_loop():
    while True:
        try:
            _keepalive_cycle(_keepalive_state)
        except Exception as e:
            # A keepalive thread that dies is invisible until searches start
            # failing, so swallow and keep pinging.
            print("KEEPALIVE cycle error (%s)" % type(e).__name__, flush=True)
        time.sleep(KEEPALIVE_SECS)

def _start_background_threads():
    global _keepalive_lock
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 47999))  # only one process per dyno wins the bind
    except OSError:
        s.close()
        return
    _keepalive_lock = s  # hold the bind for the life of the dyno
    threading.Thread(target=_keepalive_loop, name="icos-keepalive", daemon=True).start()
    print("KEEPALIVE started (every %ss)" % KEEPALIVE_SECS, flush=True)
    icos_sessions.start_reaper()
    jobs.start_janitor()
    # Heroku deploys and its daily dyno cycle both kill this process. Whatever
    # the store is holding has to be handed back to ICOS on the way out, or the
    # shared account stays locked for staff who did nothing but show up.
    icos_sessions.install_shutdown_hooks()

# Guarded so tests (and anything importing the app for inspection) don't start
# pinging the live court site.
if os.environ.get('NAPIER_DISABLE_BACKGROUND') != '1':
    _start_background_threads()
# -------------------------------------------------------------------------


def own_job(job_id):
    """Fetch a job this browser started.

    Job ids are unguessable, but binding them to the session as well keeps one
    staffer's search off another's screen if an id is ever shared or logged.
    """
    if job_id not in session.get('job_ids', []):
        return None
    return jobs.get(job_id)


def remember_job(job):
    session['job_ids'] = (session.get('job_ids', []) + [job.id])[-20:]


app.jinja_env.globals['max_names'] = roster.MAX_NAMES
app.jinja_env.globals['max_spellings'] = MAX_SPELLINGS


@app.context_processor
def waiting_workbook():
    """The most recent finished workbook this browser has not picked up yet.

    A phone that loses signal for ten seconds ends the run on screen while it
    carries on and finishes on the server. Until this, the file was built, sat
    in the job store for two hours and was thrown away unread, and the only way
    forward was to sign in and pull every case again. So every page that can be
    landed on offers the way back.
    """
    for job_id in reversed(session.get('job_ids', [])):
        job = jobs.get(job_id)
        if (job is not None and job.kind in jobs.BUILDS_A_WORKBOOK
                and job.status == jobs.DONE and not job.collected):
            return {"waiting_job": job}
    return {"waiting_job": None}


@app.route('/')
def index():
    return render_template('start.html')


@app.route('/logout')
def logout():
    icos_sessions.close(session.pop('icos_token', None))
    # Starting over has to stop what is running, or the old run keeps the
    # shared ESA account while the new one queues behind it. This used to drop
    # the browser's claim on the job and leave the work going, which is the
    # worst of both: nobody watching and nobody able to collect the result.
    # The session token is not enough, because a running CRS job has claimed
    # its session out of the store and owns the logoff itself.
    for job_id in session.get('job_ids', []):
        job = jobs.get(job_id)
        if job is not None and job.status in (jobs.QUEUED, jobs.RUNNING):
            job.cancel()
    session.pop('job_ids', None)
    return redirect(url_for('index'))


def spellings_typed(form):
    """Every spelling on the search form, in the order they were typed.

    The form posts one firstname, middlename and lastname per row, so a client
    whose name is on the docket two ways is two rows and getlist reads them the
    same way it reads the one row every other search is. A row with no surname
    is an empty extra row somebody added and did not use, not an error: Iowa
    Courts cannot be searched without one.

    Duplicates are dropped. Typing the same spelling twice would hold the
    shared account for a second search that answers what the first one did.
    """
    firsts = form.getlist('firstname')
    middles = form.getlist('middlename')
    lasts = form.getlist('lastname')
    people, seen = [], set()
    for index in range(max(len(firsts), len(middles), len(lasts), 0)):
        def part(values):
            return values[index].strip() if index < len(values) else ''
        person = {'first': part(firsts), 'middle': part(middles),
                  'last': part(lasts)}
        if not person['last']:
            continue
        key = (person['first'].lower(), person['middle'].lower(),
               person['last'].lower())
        if key in seen:
            continue
        seen.add(key)
        people.append(person)
    return people


@app.route('/search', methods=['POST'])
def search():
    # An ESA user ID is ILA## or drakelegalclinic and never has a space in it,
    # so anything around it came from the keyboard or from autofill rather than
    # from the person. Left alone it passes the check below, reaches ICOS as an
    # unknown user, and comes back looking exactly like a wrong password.
    username = request.form['username'].strip()
    password = request.form['password']
    session['isLite'] = 'isLite' in request.form

    if not username.startswith("ILA") and not username.startswith("drakelegalclinic"):
        return render_template('start.html',
                               error="That user ID is not an Iowa Legal Aid Iowa Courts account.")

    people = spellings_typed(request.form)
    if not people:
        # Only the first row's surname is required in the browser, so an empty
        # one can only arrive here with scripting off or from a second row on
        # its own. Iowa Courts has nothing to match without it.
        return render_template('start.html',
                               error="Enter a last name to search for.")
    if len(people) > MAX_SPELLINGS:
        return render_template(
            'start.html',
            error="That is %d spellings of one name. Napier holds the shared "
                  "Iowa Courts account for the whole run, so it takes %d at a "
                  "time." % (len(people), MAX_SPELLINGS))

    # A search left open from an earlier run would keep holding the ESA account.
    icos_sessions.close(session.pop('icos_token', None))

    job = jobs.start('search', tasks.search_task, username, password, people)
    remember_job(job)
    return redirect(url_for('progress', job_id=job.id))


@app.route('/batch', methods=['POST'])
def batch():
    """A whole clinic list, searched on one sign in."""
    username = request.form['username'].strip()
    password = request.form['password']
    session['isLite'] = 'isLiteBatch' in request.form

    if not username.startswith("ILA") and not username.startswith("drakelegalclinic"):
        return render_template('start.html', open_batch=True,
                               error="That user ID is not an Iowa Legal Aid Iowa Courts account.")

    people, rejected = roster.parse(request.form.get('roster', ''))
    if not people:
        return render_template('start.html', open_batch=True,
                               error="There were no names in that list. One "
                                     "client per line, either \"Last, First\" "
                                     "or \"First Last\".")
    # Searches, not clients. A client with an aka is two searches, and what the
    # limit is protecting is how long the run holds the shared account.
    searches = roster.searches_count(people)
    if searches > roster.MAX_NAMES:
        extra = searches - len(people)
        return render_template(
            'start.html', open_batch=True,
            error="That list is %d searches%s. Napier holds the shared Iowa "
                  "Courts account for the whole run, so it takes %d at a time. "
                  "Split the list."
                  % (searches,
                     " (%d clients and %d alternate spelling%s)"
                     % (len(people), extra, "" if extra == 1 else "s")
                     if extra else "",
                     roster.MAX_NAMES))

    icos_sessions.close(session.pop('icos_token', None))

    # The rejected lines ride on the job, not the session. They can hold part of
    # a client's name and the session cookie is a store on a shared machine.
    job = jobs.start('batch_search', tasks.batch_search_task,
                     username, password, people, rejected)
    remember_job(job)
    return redirect(url_for('progress', job_id=job.id))


@app.route('/roster/<job_id>')
def roster_page(job_id):
    job = own_job(job_id)
    if job is None or job.status != jobs.DONE or job.kind != 'batch_search':
        return render_template('start.html', error=RESTARTED_MESSAGE)

    session['icos_token'] = job.result['session_token']
    return render_template('roster.html', clients=job.result['clients'],
                           rejected=job.result['rejected'],
                           search_job_id=job.id)


@app.route('/batch-crs', methods=['POST'])
def batch_crs():
    data = request.get_json(silent=True) or {}
    search_job = own_job(data.get('search_job_id', ''))
    token = session.get('icos_token')
    if search_job is None or search_job.status != jobs.DONE or not token:
        return jsonify({"error": RESTARTED_MESSAGE}), 410

    clients = search_job.result['clients']
    picks = []
    for chosen in data.get('picks') or []:
        try:
            entry = clients[int(chosen.get('client'))]
        except (TypeError, ValueError, IndexError):
            return jsonify({"error": "That clinic list does not look like the "
                                     "one Napier searched. Please run it "
                                     "again."}), 400
        # Every key has to be one this search actually returned, so the browser
        # cannot name a defendant nobody picked off a page.
        keys = [key for key in (chosen.get('keys') or []) if key in entry['keys']]
        if not keys:
            continue
        # Split by the spelling that found each defendant, and deduplicated by
        # case number across all of them: a client with an aka can have the same
        # case come back under both, and writing it twice doubles what they owe.
        searches = tasks.plan_searches(keys, entry['cases'],
                                       entry.get('people')
                                       or [entry.get('person')],
                                       entry.get('found_by'))
        case_ids = [case_id for group in searches
                    for case_id in group['case_ids']]
        # The defendant key is "YYYY-MM-DD NAME", the way the search grouped it.
        def_dob, _, def_name = keys[0].partition(" ")
        # The search terms come off the search job, never off the browser: the
        # run repeats this search on ICOS, and a name posted here instead would
        # be somebody the staffer never saw on the roster page.
        picks.append({'def_name': def_name, 'def_dob': def_dob,
                      'case_ids': case_ids, 'person': entry.get('person'),
                      'searches': searches})

    if not picks:
        return jsonify({"error": "Pick a match for at least one client."}), 400

    job = jobs.start('batch_crs', tasks.batch_crs_task, token, picks,
                     session.get('isLite', False))
    remember_job(job)
    session.pop('icos_token', None)  # the batch job owns it now and logs it off
    return jsonify({"job_id": job.id,
                    "progress_url": url_for('progress', job_id=job.id)})


def _batch_limits(clients):
    """One list for the whole clinic list, in the order the sheets came back.

    A clinic list is one page for a dozen workbooks, and the same sheet falling
    short on four of them is one thing to fix, not four lines to read.
    """
    lines = []
    for client in clients:
        for line in client.get('limits') or []:
            if line not in lines:
                lines.append(line)
    return lines


def _finish_page(job, error=None):
    """The page a finished workbook run lands on, whichever kind of run it was.

    Split out because the retry it offers has to be able to come back to this
    same page with something to say, and a retry of a clinic list and a retry
    of one client end up in different templates.
    """
    result = job.result
    # Whether there is anything left worth trying, not the payload itself. The
    # payload holds whole parsed cases and a browser has no business with them.
    can_retry = bool(result.get('retry'))
    if job.kind == 'batch_crs':
        return render_template('batch_done.html', job=job.to_dict(),
                               clients=result['clients'],
                               months=crs.RECENT_MONTHS,
                               is_lite=result['is_lite'],
                               built=sum(1 for c in result['clients'] if c['file']),
                               written=result['written_cases'],
                               can_retry=can_retry, error=error,
                               missing=sum(len(c['failed'])
                                           for c in result['clients']),
                               limits=_batch_limits(result['clients']))
    return render_template('done.html', job=job.to_dict(),
                           atp=result.get('atp'),
                           def_name=result['def_name'],
                           is_lite=result['is_lite'],
                           written=result['written_cases'],
                           requested=result['requested_cases'],
                           failed=result['failed_cases'],
                           can_retry=can_retry, error=error,
                           missing=len(result['failed_cases']),
                           limits=result.get('limits') or [],
                           filename=tasks.download_name(result['def_name'],
                                                        result['is_lite']))


@app.route('/batch-done/<job_id>')
def batch_done(job_id):
    job = own_job(job_id)
    if job is None or job.status != jobs.DONE or job.kind != 'batch_crs':
        return render_template('start.html', error=RESTARTED_MESSAGE)

    return _finish_page(job)


@app.route('/retry/<job_id>', methods=['POST'])
def retry(job_id):
    """Another go at only the cases Iowa Courts would not give up.

    A fresh sign in, because the run this is started from logged its ICOS
    session off on the way out and that is the behaviour keeping the shared
    account usable. It is the same sign in staff would do anyway, and it buys
    them not re-pulling the sixty cases that already worked.
    """
    job = own_job(job_id)
    if (job is None or job.status != jobs.DONE
            or job.kind not in jobs.BUILDS_A_WORKBOOK):
        return render_template('start.html', error=RESTARTED_MESSAGE)

    payload = job.result.get('retry')
    if not payload:
        # The run came back complete, or it has no way to put the search back in
        # front of ICOS. Either way there is nothing here to do.
        return _finish_page(job, error="There is nothing on this run left to "
                                       "try again.")

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    if not username.startswith("ILA") and not username.startswith("drakelegalclinic"):
        return _finish_page(job, error="That user ID is not an Iowa Legal Aid "
                                       "Iowa Courts account.")

    icos_sessions.close(session.pop('icos_token', None))
    retry_job = jobs.start(payload['kind'], tasks.retry_task,
                           username, password, payload)
    remember_job(retry_job)
    return redirect(url_for('progress', job_id=retry_job.id))


def _served_file(path):
    """A path is only servable if it is an ordinary file we wrote into tmp_dir.

    Same rule the single download has kept: a tampered session must not be able
    to point this at anything else on the dyno.
    """
    real = os.path.realpath(path)
    if os.path.dirname(real) != os.path.realpath(tmp_dir).rstrip(os.sep):
        return None
    if not (real.endswith('.xlsx') or real.endswith('.zip')):
        return None
    return real


@app.route('/batch/<job_id>/download')
@app.route('/batch/<job_id>/download/<int:index>')
def batch_download(job_id, index=None):
    job = own_job(job_id)
    if job is None or job.status != jobs.DONE or job.kind != 'batch_crs':
        return render_template('start.html', error=RESTARTED_MESSAGE)

    if index is None:
        path, name = job.result['file'], 'Napier_clinic_list.zip'
    else:
        try:
            record = job.result['clients'][index]
        except IndexError:
            return render_template('start.html', error=RESTARTED_MESSAGE)
        if not record['file']:
            return render_template('start.html', error=RESTARTED_MESSAGE)
        path = record['file']
        name = tasks.download_name(record['name'], job.result['is_lite'])

    path = _served_file(path)
    if path is None:
        return "Bad session - invalid file"

    # The zip is the whole errand; one client's workbook is not. Collecting the
    # list is what stops the uncollected alert, so a staffer who grabs one file
    # and closes the tab still gets chased about the other nineteen.
    if index is None:
        job.collected = True
    return send_file(path, as_attachment=True, download_name=name)


@app.route('/progress/<job_id>')
def progress(job_id):
    job = own_job(job_id)
    if job is None:
        return render_template('start.html', error=RESTARTED_MESSAGE)
    return render_template('progress.html', job=job.to_dict())


@app.route('/job/<job_id>')
def job_status(job_id):
    job = own_job(job_id)
    if job is None:
        return jsonify({"status": jobs.FAILED, "done": True,
                        "error": RESTARTED_MESSAGE,
                        "message": RESTARTED_MESSAGE, "progress": []}), 410
    return jsonify(job.to_dict())


@app.route('/job/<job_id>/cancel', methods=['POST'])
def cancel_job(job_id):
    """Stop a run a staffer has given up on.

    POST rather than a link because a link gets followed by things that are not
    the staffer, and the run this ends is holding an ESA account.

    Nothing is killed. The work is asked to stop and stops at its next check,
    which is what lets it log the session off on the way out. That is the part
    that matters: the account Iowa Legal Aid shares is locked for a quarter of
    an hour by a session nobody released.
    """
    job = own_job(job_id)
    if job is None:
        return jsonify({"error": RESTARTED_MESSAGE}), 410
    job.cancel()
    return jsonify({"cancelled": True})


@app.route('/job/<job_id>/lost', methods=['POST'])
def lost_contact(job_id):
    """The progress page reporting that it could not reach us for a while.

    Sent once the browser is talking again, because a page with no connection
    cannot report that it has no connection. Nothing here is a server failure,
    which is the point: a staffer watching a working run appear to die is
    exactly the kind of thing that never reaches a log we read.
    """
    job = own_job(job_id)
    if job is None:
        return ('', 204)
    seconds = (request.get_json(silent=True) or {}).get('seconds')
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 0
    alerts.record(job.id[:8], job.kind, alerts.CLIENT_LOST,
                  progress=alerts.recent_progress(job),
                  **{'out of contact': '%d seconds' % seconds,
                     'job status': job.status,
                     'note': ("The run was not affected. This is the staffer's "
                              "connection to Napier, and it recovered on its "
                              "own. Worth watching only if it keeps happening.")})
    return ('', 204)


@app.route('/results/<job_id>')
def results(job_id):
    job = own_job(job_id)
    if job is None or job.status != jobs.DONE or job.kind != 'search':
        return render_template('start.html', error=RESTARTED_MESSAGE)

    result = job.result
    session['icos_token'] = result['session_token']
    # searches is what each spelling came back with, so a run that searched two
    # can say which one found a defendant and which one Iowa Courts refused.
    # A run from before aliases has none and the page renders as it always did.
    searches = result.get('searches') or []
    return render_template('search.html', cases=result['cases'], keys=result['keys'],
                           too_many_results=result['too_many_results'],
                           searches=searches if len(searches) > 1 else [],
                           found_by=result.get('found_by') or {},
                           search_job_id=job.id)


@app.route('/crs-job', methods=['POST'])
def crs_job():
    data = request.get_json(silent=True) or {}
    search_job = own_job(data.get('search_job_id', ''))
    token = session.get('icos_token')
    if search_job is None or search_job.status != jobs.DONE or not token:
        return jsonify({"error": RESTARTED_MESSAGE}), 410

    keys = data.get('keys') or []
    if not keys:
        return jsonify({"error": "Select a name to build a CRS for."}), 400

    # def_name/def_dob come from the selected defendant key ("YYYY-MM-DD NAME"),
    # so the client cannot name a defendant it did not pick.
    primary = keys[0]
    def_dob, _, def_name = primary.partition(" ")

    # The search terms travel with the run so a short workbook can be finished
    # off later. They come off the search job and never off the browser, the
    # same rule the clinic list keeps.
    job = jobs.start('crs', tasks.crs_task, token, keys, search_job.result['cases'],
                     def_name, def_dob, session.get('isLite', False),
                     search_job.result.get('person'),
                     search_job.result.get('people'),
                     search_job.result.get('found_by'))
    remember_job(job)
    session.pop('icos_token', None)  # the CRS job owns it now and logs it off
    return jsonify({"job_id": job.id, "progress_url": url_for('progress', job_id=job.id)})


@app.route('/done/<job_id>')
def done(job_id):
    job = own_job(job_id)
    if job is None or job.status != jobs.DONE or job.kind != 'crs':
        return render_template('start.html', error=RESTARTED_MESSAGE)

    return _finish_page(job)


@app.route('/job/<job_id>/download')
def download(job_id):
    job = own_job(job_id)
    if job is None or job.status != jobs.DONE or job.kind != 'crs':
        return render_template('start.html', error=RESTARTED_MESSAGE)

    # only serve xlsx files that live directly inside tmp_dir, so a tampered
    # session can't point this at an arbitrary file
    path = os.path.realpath(job.result['file'])
    if os.path.dirname(path) != os.path.realpath(tmp_dir).rstrip(os.sep) \
            or not path.endswith('.xlsx'):
        return "Bad session - invalid file"

    # The run is only over once the file has reached someone. This is what stops
    # the uncollected-workbook alert and takes the offer off the start page.
    job.collected = True
    return send_file(path, as_attachment=True,
                     download_name=tasks.download_name(job.result['def_name'],
                                                       job.result['is_lite']))


@app.errorhandler(Exception)
def unhandled(e):
    """Anything that got past a route without being handled.

    Background jobs report their own failures, so what lands here is a bug in a
    request path, which is the case nobody would otherwise find out about: the
    user sees a 500 and closes the tab. Re-raised afterwards so Flask still
    produces its normal error response and the traceback still reaches the log.
    """
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        # 404s and the like are routing, not breakage.
        return e
    # The request path is included and the query string is not, since a search
    # is submitted as a POST body and a path is only ever a route plus a job id.
    alerts.record(request.path, 'web', alerts.UNHANDLED,
                  **{'traceback': alerts.safe_traceback(e)})
    raise e


@app.template_filter('pluralize')
def pluralize(number, singular = '', plural = 's'):
    if number == 1:
        return singular
    else:
        return plural

if __name__ == "__main__":
	app.run(host="0.0.0.0")
