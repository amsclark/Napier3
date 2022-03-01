import cookielib
import os
import pickle
import urllib2

user_agent = u"Mozilla/5.0 (Macintosh; U; Intel Mac OS X 10.6; en-US; " + \
    u"rv:1.9.2.11) Gecko/20101012 Firefox/3.6.11"

class Opener:
    def __init__(self):
        self.cookieJar = cookielib.CookieJar()
        cookie_processor = urllib2.HTTPCookieProcessor(self.cookieJar)
        #if os.environ.get('PROXIMO_URL', '') != '':
        #    proxy = urllib2.ProxyHandler({'http': os.environ.get('PROXIMO_URL', '')})
        #    auth = urllib2.HTTPBasicAuthHandler()
        #    self.opener = urllib2.build_opener(cookie_processor, proxy, auth, urllib2.HTTPHandler)
        #else:
        self.opener = urllib2.build_opener(cookie_processor)
        self.opener.addheaders = [('User-Agent', user_agent)]

    def get_cookies(self):
        return pickle.dumps(list(self.cookieJar))
    
    def load_cookies(self, encoded_cookies):
        for cookie in pickle.loads(encoded_cookies):
            self.cookieJar.set_cookie(cookie)

    def open(self, *args):
        url = args[0]
        if len(args) == 2:
            data = args[1]
            return self.opener.open(url, data)
        return self.opener.open(url)

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