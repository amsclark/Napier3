import time
import urllib

BASE_URL = "https://www.iowacourts.state.ia.us/ESAWebApp/"

def build_url(path):
    return BASE_URL + path

class Reader:
    def __init__(self, opener):
        self.opener = opener

    def fetch(self, url, data=None):
        # ICOS sometimes answers a valid request with HTTP 200 and an empty
        # body; retry once before giving up so the user doesn't see a bogus
        # "no results" page.
        for attempt in range(2):
            if data is None:
                result = self.opener.open(url).read()
            else:
                result = self.opener.open(url, data).read()
            if result and result.strip():
                return result
            print("Empty response from", url, "(attempt", attempt + 1, "of 2)")
            time.sleep(1)
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
            ('role', "NOT ATTORNEY"),
            ('and/or', ""),
            ('last', ""),
            ('first', ""),
            ('middle', ""),
            ('alast', ""),
            ('afirst', ""),
            ('amiddle', ""),
            ('role', "NOT ATTORNEY"),
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
