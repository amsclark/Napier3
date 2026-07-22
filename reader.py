import time
import socket
import urllib
import urllib.error

BASE_URL = "https://www.iowacourts.state.ia.us/ESAWebApp/"

# Outcomes of a single ICOS request. The retry engine in icos.py decides what
# to do with each one; a bare body can't tell "slow court site" from "wrong
# password", and those need opposite handling.
OK = "OK"
EMPTY = "EMPTY"
TIMEOUT = "TIMEOUT"
ERROR = "ERROR"


class FetchResult:
    __slots__ = ("outcome", "body", "status", "elapsed", "detail")

    def __init__(self, outcome, body=b"", status="?", elapsed=0.0, detail=""):
        self.outcome = outcome
        self.body = body
        self.status = status
        self.elapsed = elapsed
        self.detail = detail

    @property
    def ok(self):
        return self.outcome == OK


def build_url(path):
    return BASE_URL + path

class Reader:
    def __init__(self, opener):
        self.opener = opener

    def fetch_once(self, url, data=None, timeout=8):
        # One attempt, classified. Retry strategy lives in icos.py so it can
        # apply backoff and progress reporting across a whole job; this only
        # reports what happened. Logs endpoint name and timing only -- never
        # bodies or form data -- so production stays measurable without PII.
        name = url.rsplit("/", 1)[-1].split("?")[0] or "ESAWebApp"
        t0 = time.time()
        try:
            resp = self.opener.open(url, timeout=timeout) if data is None \
                else self.opener.open(url, data, timeout=timeout)
            body = resp.read()
            dt = time.time() - t0
            status = getattr(resp, "status", "?")
            if body and body.strip():
                print(f"ICOS {name} status={status} {len(body)}b {dt:.2f}s OK", flush=True)
                return FetchResult(OK, body, status, dt)
            print(f"ICOS {name} status={status} EMPTY {dt:.2f}s", flush=True)
            return FetchResult(EMPTY, b"", status, dt)
        except (socket.timeout, TimeoutError):
            dt = time.time() - t0
            print(f"ICOS {name} TIMEOUT {dt:.2f}s", flush=True)
            return FetchResult(TIMEOUT, b"", "?", dt)
        except urllib.error.URLError as e:
            dt = time.time() - t0
            reason = getattr(e, "reason", e)
            print(f"ICOS {name} URLERR {reason} {dt:.2f}s", flush=True)
            return FetchResult(ERROR, b"", "?", dt, str(reason))
        except Exception as e:
            dt = time.time() - t0
            print(f"ICOS {name} ERR {type(e).__name__} {dt:.2f}s", flush=True)
            return FetchResult(ERROR, b"", "?", dt, type(e).__name__)

    def fetch(self, url, data=None):
        # ICOS stalls the first request(s) on a cold/idle connection path
        # (observed: ~30s hangs from cold, then instant once warm). Use a short
        # per-attempt timeout so a cold stall fails fast and we retry into the
        # warming path, instead of hanging until Heroku's 30s H12. Log the
        # timing + outcome of every attempt (endpoint name only -- no bodies,
        # so no PII) to measure the real cold/warm pattern in production.
        name = url.rsplit("/", 1)[-1].split("?")[0] or "ESAWebApp"
        attempts = 2
        result = b""
        for attempt in range(attempts):
            t0 = time.time()
            try:
                resp = self.opener.open(url, timeout=8) if data is None \
                    else self.opener.open(url, data, timeout=8)
                result = resp.read()
                dt = time.time() - t0
                status = getattr(resp, "status", "?")
                if result and result.strip():
                    print(f"ICOS {name} try {attempt+1}/{attempts} status={status} {len(result)}b {dt:.2f}s OK", flush=True)
                    return result
                print(f"ICOS {name} try {attempt+1}/{attempts} status={status} EMPTY {dt:.2f}s", flush=True)
            except (socket.timeout, TimeoutError):
                print(f"ICOS {name} try {attempt+1}/{attempts} TIMEOUT {time.time()-t0:.2f}s (cold path?)", flush=True)
                result = b""
            except urllib.error.URLError as e:
                print(f"ICOS {name} try {attempt+1}/{attempts} URLERR {getattr(e, 'reason', e)} {time.time()-t0:.2f}s", flush=True)
                result = b""
            except Exception as e:
                print(f"ICOS {name} try {attempt+1}/{attempts} ERR {type(e).__name__} {time.time()-t0:.2f}s", flush=True)
                result = b""
            time.sleep(0.5)
        return result

    # Request builders: (url, data) pairs, so the retry engine can issue an
    # attempt at a time. The fetch()-based methods below keep working for any
    # caller that just wants a body.
    def init_request(self):
        return build_url("ESALogin.jsp"), None

    def login_request(self, username, password):
        return build_url("EUACustomLoginServlet"), urllib.parse.urlencode([
            ('userid', username),
            ('password', password),
            ('agency', "JUDICIAL"),
            ('jumpto', build_url("TrialCourtStateWide")),
            ('search', "Logon")
        ])

    def logoff_request(self):
        return build_url("EPALogout"), urllib.parse.urlencode([
            ('logoffButton', "Logoff")
        ])

    def search_request(self, firstname, middlename, lastname):
        return build_url("TrialCaseSearchResultServlet"), urllib.parse.urlencode([
            ('searchtype', "N"),
            ('last', lastname),
            ('first', firstname),
            ('middle', middlename),
            ('alast', ""),
            ('afirst', ""),
            ('amiddle', ""),
            ('role', "NTAT"),
            ('and/or', ""),
            ('last', ""),
            ('first', ""),
            ('middle', ""),
            ('alast', ""),
            ('afirst', ""),
            ('amiddle', ""),
            ('role', "NTAT"),
            ('county', "00"),
            ('casetype', "ALL"),
            ('caseid1sel', ""),
            ('caseid3sel', "AM"),
            ('caseid1', ""),
            ('caseid2', ""),
            ('caseid3', ""),
            ('caseid4', ""),
            ('citation_number', ""),
            ('search', "Search")
        ])

    def case_summary_request(self, case_id):
        return build_url("TViewCaseCivil?caseid=") + case_id.replace(" ", "+"), None

    def case_charges_request(self):
        return build_url("TViewCharges"), None

    def case_financials_request(self):
        return build_url("TViewFinancials"), None

    def init(self):
        url = build_url("ESALogin.jsp")
        return self.fetch(url)

    def login(self, username, password):
        url = build_url("EUACustomLoginServlet")
        data = urllib.parse.urlencode([
            ('userid', username),
            ('password', password),
            ('agency', "JUDICIAL"),
            ('jumpto', build_url("TrialCourtStateWide")),
            ('search', "Logon")
        ])
        return self.fetch(url, data)

    def logoff(self):
        url = build_url("EPALogout")
        data = urllib.parse.urlencode([
            ('logoffButton', "Logoff")
        ])
        return self.fetch(url, data)

    def search(self, firstname, middlename, lastname):
        url = build_url("TrialCaseSearchResultServlet")
        data = urllib.parse.urlencode([
            ('searchtype', "N"),
            ('last', lastname),
            ('first', firstname),
            ('middle', middlename),
            ('alast', ""),
            ('afirst', ""),
            ('amiddle', ""),
            ('role', "NTAT"),
            ('and/or', ""),
            ('last', ""),
            ('first', ""),
            ('middle', ""),
            ('alast', ""),
            ('afirst', ""),
            ('amiddle', ""),
            ('role', "NTAT"),
            ('county', "00"),
            ('casetype', "ALL"),
            ('caseid1sel', ""),
            ('caseid3sel', "AM"),
            ('caseid1', ""),
            ('caseid2', ""),
            ('caseid3', ""),
            ('caseid4', ""),
            ('citation_number', ""),
            ('search', "Search")
        ])
        return self.fetch(url, data)
    
    def case_summary(self, case_id):
        url = build_url("TViewCaseCivil?caseid=")
        url += case_id.replace(" ", "+")
        return self.fetch(url)
    
    def case_charges(self):
        url = build_url("TViewCharges")
        return self.fetch(url)
    
    def case_financials(self):
        url = build_url("TViewFinancials")
        return self.fetch(url)
