from flask import Flask, jsonify, render_template, request, send_file, session, url_for, redirect
import os
import platform
import time

import alerts
import icos_sessions
import jobs
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


@app.route('/')
def index():
    return render_template('start.html')


@app.route('/logout')
def logout():
    icos_sessions.close(session.pop('icos_token', None))
    session.pop('job_ids', None)
    return redirect(url_for('index'))


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

    # A search left open from an earlier run would keep holding the ESA account.
    icos_sessions.close(session.pop('icos_token', None))

    job = jobs.start('search', tasks.search_task, username, password,
                     request.form['firstname'], request.form['middlename'],
                     request.form['lastname'])
    remember_job(job)
    return redirect(url_for('progress', job_id=job.id))


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


@app.route('/results/<job_id>')
def results(job_id):
    job = own_job(job_id)
    if job is None or job.status != jobs.DONE or job.kind != 'search':
        return render_template('start.html', error=RESTARTED_MESSAGE)

    result = job.result
    session['icos_token'] = result['session_token']
    return render_template('search.html', cases=result['cases'], keys=result['keys'],
                           too_many_results=result['too_many_results'],
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

    job = jobs.start('crs', tasks.crs_task, token, keys, search_job.result['cases'],
                     def_name, def_dob, session.get('isLite', False))
    remember_job(job)
    session.pop('icos_token', None)  # the CRS job owns it now and logs it off
    return jsonify({"job_id": job.id, "progress_url": url_for('progress', job_id=job.id)})


@app.route('/done/<job_id>')
def done(job_id):
    job = own_job(job_id)
    if job is None or job.status != jobs.DONE or job.kind != 'crs':
        return render_template('start.html', error=RESTARTED_MESSAGE)

    result = job.result
    return render_template('done.html', job=job.to_dict(),
                           def_name=result['def_name'],
                           is_lite=result['is_lite'],
                           written=result['written_cases'],
                           requested=result['requested_cases'],
                           failed=result['failed_cases'],
                           filename=tasks.download_name(result['def_name'],
                                                        result['is_lite']))


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
