import urllib

BASE_URL = "https://www.iowacourts.state.ia.us/ESAWebApp/"

def build_url(path):
    return BASE_URL + path

class Reader:
    def __init__(self, opener):
        self.opener = opener

    def init(self):
        url = build_url("ESALogin.jsp")
        return self.opener.open(url).read()

    def login(self, username, password):
        url = build_url("EUACustomLoginServlet")
        data = urllib.parse.urlencode([
            ('userid', username),
            ('password', password),
            ('agency', "JUDICIAL"),
            ('jumpto', build_url("TrialCourtStateWide")),
            ('search', "Logon")
        ])
        return self.opener.open(url, data).read()

    def logoff(self):
        url = build_url("EPALogout")
        data = urllib.urlencode([
            ('logoffButton', "Logoff")
        ])
        return self.opener.open(url, data).read()

    def search(self, firstname, middlename, lastname):
        url = build_url("TrialCaseSearchResultServlet")
        data = urllib.urlencode([
            ('searchtype', "N"),
            ('last', lastname),
            ('first', firstname),
            ('middle', middlename),
            ('alast', ""),
            ('afirst', ""),
            ('amiddle', ""),
            ('role', "ALL"),
            ('and/or', ""),
            ('last', ""),
            ('first', ""),
            ('middle', ""),
            ('alast', ""),
            ('afirst', ""),
            ('amiddle', ""),
            ('role', "ALL"),
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
        return self.opener.open(url, data).read()
    
    def case_summary(self, case_id):
        url = build_url("TViewCaseCivil?caseid=")
        url += case_id.replace(" ", "+")
        return self.opener.open(url).read()
    
    def case_charges(self):
        url = build_url("TViewCharges")
        return self.opener.open(url).read()
    
    def case_financials(self):
        url = build_url("TViewFinancials")
        return self.opener.open(url).read()
