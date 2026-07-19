import time
import socket
import urllib
import urllib.error

BASE_URL = "https://www.iowacourts.state.ia.us/ESAWebApp/"

def build_url(path):
    return BASE_URL + path

class Reader:
    def __init__(self, opener):
        self.opener = opener

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
