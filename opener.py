#import cookielib 
import http.cookiejar
import os
import pickle
import urllib, urllib.request, urllib.parse

http.client.HTTPConnection.debuglevel = 1

user_agent = u"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"





class Opener:
    def __init__(self):
        self.cookieJar = http.cookiejar.CookieJar()
        cookie_processor = urllib.request.HTTPCookieProcessor(self.cookieJar)
        self.opener = urllib.request.build_opener(cookie_processor)
        self.opener.addheaders = [('User-Agent', user_agent)]

    def get_cookies(self):
        return pickle.dumps(list(self.cookieJar))
    
    def load_cookies(self, encoded_cookies):
        for cookie in pickle.loads(encoded_cookies):
            self.cookieJar.set_cookie(cookie)

    def open(self, *args):
        url = args[0]
        if len(args) == 2:
            paramstring = args[1]
            data_tuple_list = urllib.parse.parse_qsl(paramstring,keep_blank_values=True)
            opener_data = urllib.parse.urlencode(data_tuple_list).encode('UTF-8')
            return self.opener.open(url, opener_data)
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
