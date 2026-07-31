#import cookielib 
import http.cookiejar
import os
import urllib, urllib.request, urllib.parse

user_agent = u"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

# ICOS sits behind Akamai, which fingerprints clients for bot detection. A bare
# request that sends only a User-Agent gets intermittently served an empty body
# (the "Empty response from ICOS" failures). Sending the full set of headers a
# real Chrome navigation sends makes the request look browser-like and reduces
# that flagging. NOTE: we deliberately do NOT advertise Accept-Encoding here --
# urllib does not auto-decompress, so advertising gzip/br would hand the parser
# compressed bytes. Leaving it off keeps responses as identity/plaintext.
browser_headers = [
    ('User-Agent', user_agent),
    ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'),
    ('Accept-Language', 'en-US,en;q=0.9'),
    ('sec-ch-ua', '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"'),
    ('sec-ch-ua-mobile', '?0'),
    ('sec-ch-ua-platform', '"Windows"'),
    ('Sec-Fetch-Dest', 'document'),
    ('Sec-Fetch-Mode', 'navigate'),
    ('Sec-Fetch-Site', 'same-origin'),
    ('Sec-Fetch-User', '?1'),
    ('Upgrade-Insecure-Requests', '1'),
    ('Referer', 'https://www.iowacourts.state.ia.us/ESAWebApp/TrialCourtStateWide'),
]





class Opener:
    def __init__(self):
        self.cookieJar = http.cookiejar.CookieJar()
        cookie_processor = urllib.request.HTTPCookieProcessor(self.cookieJar)
        self.opener = urllib.request.build_opener(cookie_processor)
        self.opener.addheaders = list(browser_headers)

    def open(self, url, data=None, timeout=None):
        kw = {} if timeout is None else {"timeout": timeout}
        if data is not None:
            data_tuple_list = urllib.parse.parse_qsl(data, keep_blank_values=True)
            opener_data = urllib.parse.urlencode(data_tuple_list).encode('UTF-8')
            return self.opener.open(url, opener_data, **kw)
        return self.opener.open(url, **kw)


'''
import mechanize

class NoHistory(object):
    def add(self, *a, **k): pass
    def clear(self): pass

class Opener:
    def __init__(self):
        self.opener = mechanize.Browser(history=NoHistory())
        self.opener.set_handle_robots(False)

    def set_cookie(self, name, value):
        self.opener.set_cookie(str(name) + '=' + str(value))

    def open(self, *args):
        url = args[0]
        if len(args) == 2:
            data = args[1]
            return self.opener.open(url, data)
        return self.opener.open(url)
'''
