""" Client for remote debugging Google Chrome.

    > crs = ChromeRemoteShell(host='localhost', port=92222, socket_timeout=3)

    crs.tablist has a list of details on open tabs.

    > crs.connect(tab=index, updateTabs=True)

    will connect crs.soc to the webservice endpoint for tablist[index]'th
    tab.  index is an integer, and updateTabs is True or False. Both tab
    and updateTabs are optional, defaulting to 0 and True respectively.

    At this point crs.soc.send and crs.soc.recv will synchronously write
    commands and read responses.  The api is semi-asynchronous with
    responses for commands, but also spontaeneous events will be
    send by the browser. For this kind of advance usage, select/pol
    on soc is advised.
"""
import json
import urllib.request
import websocket


class ChromeRemoteShell(object):

    def __init__(self, host='localhost', port=9222, socket_timeout=3):
        """ init """
        self.host = host
        self.port = port
        self.socket_timeout = socket_timeout
        self.soc = None
        self.tablist = None
        self.find_tabs()

    def connect(self, tab=None, update_tabs=True):
        """Open a websocket connection to remote browser, determined by
           self.host and self.port.  Each tab has it's own websocket
           endpoint - you specify which with the tab parameter, defaulting
           to 0.  The parameter update_tabs, if True, will force a rescan
           of open tabs before connection. """
        if update_tabs or not self.tablist:
            self.find_tabs()
        numtabs = len(self.tablist)
        if not tab:
            tab = numtabs - 1
        wsurl = self.tablist[tab]['webSocketDebuggerUrl']
        if self.soc and self.soc.connected:
            self.soc.close()
        websocket.setdefaulttimeout(3)
        self.soc = websocket.WebSocket()
        self.soc.settimeout(self.socket_timeout)
        self.soc.connect(wsurl)
        return self.soc

    def close(self):
        """ Close websocket connection to remote browser."""
        if self.soc:
            self.soc.close()
            self.soc = None

    def find_tabs(self):
        """Connect to host:port and request list of tabs
             return list of dicts of data about open tabs."""
        # find websocket endpoint
        f = urllib.request.urlopen('http://{}:{}/json'.format(
                self.host, self.port))
        self.tablist = json.loads(f.read().decode('utf-8'))
        return self.tablist

    def open_url(self, url):
        """Open a URL in the oldest tab."""
        if not self.soc or not self.soc.connected:
            self.connect(tab=0)
        # force the 'oldest' tab to load url
        navcom = json.dumps({"id": 0,
                             "method": "Page.navigate",
                             "params": {"url": url}})
        self.soc.send(navcom)
        return self.soc.recv()
