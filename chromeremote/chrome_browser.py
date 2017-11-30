"""
Run Chrome with debugging console enabled
"""

import base64
import logging
import os
import subprocess
import signal
import time
import json
import sys
import websocket
from datetime import datetime
from .remote_shell import ChromeRemoteShell
from .chrome_profile import chrome_profile


logger = logging.getLogger(__name__)


def dict_val(key, dictionary):
    if key in dictionary:
        return dictionary[key]
    elif key.lower() in dictionary:
        return dictionary[key.lower()]
    return None


def dict_intval(key, dictionary):
    if dict_val(key, dictionary) is not None:
        return int(dict_val(key, dictionary))
    return None


class Request(object):

    def __init__(self, request_id):
        self.id = request_id
        self.failed = False
        self.status_code = None
        self.url = None
        self.content_length = None
        self.mime_type = None
        self.complete = False
        self.redirected = []


class ChromeBrowser(object):

    profile = chrome_profile()

    def __init__(
            self,
            socket=9111,
            startup_delay=4,
            shutdown_delay=3,
            remote_shell_timeout=3,
            chrome_bin=None,
            headless=True,
            content_dir=False,
            work_dir='/tmp/chromeremote',
            user_agent=False,
            master_timeout=120,
            extreme_debugging=False,
    ):
        self.work_dir = work_dir
        self.chrome_sock = socket
        self.chrome_pid = False
        self.chrome_log_file = os.path.join(self.work_dir, 'chromelog.json')
        self.cookie_log_file = os.path.join(self.work_dir, 'cookies.json')
        self.content_dir = os.path.join(self.work_dir, 'content')
        if content_dir:
            self.content_dir = content_dir
        self.open_requests = []
        self.chrome_log = []
        self.shell = False
        self.startup_delay = startup_delay
        self.shutdown_delay = shutdown_delay
        self.remote_shell_timeout = remote_shell_timeout
        self.chrome_bin = chrome_bin
        self.headless = headless
        self.reqs = []
        self.user_agent = user_agent
        if self.chrome_bin is None:
            logger.error('chrome binary not found')
            sys.exit(1)
        self.master_timeout = master_timeout
        self.stop_loading = False
        self.domstorage_enabled = True
        self.extreme_debugging = extreme_debugging

    def _receive_chrome(self):
        response = json.loads(self.shell.soc.recv())
        # logger.debug('got {}'.format(response))
        self.chrome_log.append(response)
        return response

    def _send_chrome(self, data):
        navcom = json.dumps(data)
        self.shell.soc.send(navcom)
        response = self._receive_chrome()
        return response

    def _start_console(self):
        self.shell = ChromeRemoteShell(
                host='localhost',
                port=self.chrome_sock,
                socket_timeout=self.remote_shell_timeout,
        )
        self.shell.connect()
        logger.debug('Socket timeout: {}s'.format(self.shell.soc.gettimeout()))
        self._send_chrome(
                {"id": 0, "method": "Network.enable"})
        self._send_chrome(
                {"id": 0, "method": "DOMStorage.enable"})
        if self.user_agent:
            self._send_chrome(
                    {
                            "id": 0,
                            "method": "Network.setUserAgentOverride",
                            "params": {
                                    "userAgent": self.user_agent,
                            }
                    }
            )

    def _get_requests(self, redirect_only=False):
        requests = {}
        for entry in self.chrome_log:
            if 'method' not in entry \
                    or not entry['method'].startswith('Network.'):
                continue
            rp = entry['params']
            req_id = rp['requestId']
            if entry['method'] == 'Network.requestWillBeSent':
                if 'redirectResponse' in rp:
                    rdr = rp['redirectResponse']
                    last_req = requests[req_id]
                    last_req.complete = True
                    last_req.status_code = rdr['status']
                    requests[req_id] = Request(req_id)
                    if len(last_req.redirected) > 0:
                        requests[req_id].redirected = last_req.redirected
                        last_req.redirected = []
                    requests[req_id].redirected.append(last_req)
                else:
                    requests[req_id] = Request(req_id)
                requests[req_id].url = rp['request']['url']
            elif entry['method'] == 'Network.requestServedFromCache':
                requests[req_id] = Request(req_id)
            elif entry['method'] == 'Network.responseReceived':
                if req_id not in requests:
                    continue
                rpr = rp['response']
                requests[req_id].mime_type = rpr['mimeType']
                requests[req_id].status_code = rpr['status']
                requests[req_id].content_length = dict_intval(
                        'Content-Length', rpr['headers'])
            elif entry['method'] == 'Network.loadingFinished':
                if req_id not in requests:
                    continue
                requests[req_id].complete = True
            elif entry['method'] == 'Network.loadingFailed':
                requests[req_id].complete = True
                requests[req_id].failed = True
        for req in sorted(requests):
            r = requests[req]
            if r.url is None:
                continue
            if r.url.startswith('data'):
                continue
            if redirect_only:
                if len(r.redirected) > 0:
                    self.reqs.append(r)
            else:
                self.reqs.append(r)

    def _read_data(self, data=False):
        domstorage_activities = 0
        while True:
            if data and 'method' in data:
                if self.extreme_debugging:
                    logger.debug('got data: {}'.format(data))
                if data['method'] == 'Network.requestWillBeSent' \
                        or data['method'] == 'Network.requestServedFromCache':
                    request_id = data['params']['requestId']
                    # logger.debug('open req {}'.format(request_id))
                    if request_id not in self.open_requests:
                        self.open_requests.append(request_id)
                    domstorage_activities = 0
                elif data['method'] == 'Network.loadingFinished':
                    request_id = data['params']['requestId']
                    # logger.debug('finished req {}'.format(request_id))
                    try:
                        self.open_requests.remove(request_id)
                    except ValueError:
                        logger.error(
                                'loadingFinished but request {} not found'
                                ' in open requests'.format(request_id)
                        )
                    domstorage_activities = 0
                elif data['method'] == 'Network.loadingFailed':
                    request_id = data['params']['requestId']
                    logger.debug('loading failed on req {}'.format(request_id))
                    try:
                        self.open_requests.remove(request_id)
                    except ValueError:
                        logger.error(
                                'request {} not found in open requests'.format(
                                        request_id)
                        )
                    domstorage_activities = 0
                elif data['method'].startswith('DOMStorage'):
                    domstorage_activities += 1
                elif data['method'] in (
                        'Network.dataReceived',
                        'Network.responseReceived',
                        'Network.resourceChangedPriority',
                ):
                    domstorage_activities = 0
                else:
                    logger.debug(
                            'unexpected data[\'method\']: {}'.format(
                                    data['method'])
                    )
            if self.domstorage_enabled and domstorage_activities > 20:
                # exit when there is no more network traffic
                logger.debug('looks like a DOMStorage loop. stopping it...')
                resp = self._send_chrome(
                    {"id": 0, "method": "DOMStorage.disable"})
                self.domstorage_enabled = False
                # break
            now = datetime.now()
            runtime = now - self.start_time
            if (not self.stop_loading
                    and runtime.total_seconds() > self.master_timeout - 20):
                logger.error(
                        'timeout of {} seconds reached - stop loading'.format(
                                self.master_timeout - 60)
                )
                resp = self._send_chrome(
                    {"id": 0, "method": "Page.stopLoading"})
                logger.debug('got {}'.format(resp))
                self.stop_loading = True
                break
            self.check_timeout()
            try:
                data = self._receive_chrome()
            except websocket.WebSocketTimeoutException:
                logger.debug('TIMEOUT REACHED')
                break

    def start_chrome(self):
        chrome_dir = os.path.join(self.work_dir, 'chrome_profile')
        if not os.path.exists(chrome_dir):
            os.makedirs(chrome_dir)
        logger.debug('Extract Chrome profile to {}...'.format(chrome_dir))
        p = subprocess.call(
                ['tar', 'xz', '-C', chrome_dir, '-f', chrome_profile()])
        logger.debug('Start Chrome...')
        chrome_args = [
                self.chrome_bin,
                '--remote-debugging-port={}'.format(self.chrome_sock),
                '--no-default-browser-check',
                '--user-data-dir={}/chrometemp'.format(chrome_dir),
                '--disable-translate',
                '--net-log-capture-mode=IncludeCookiesAndCredentials',
                '-homepage',
                'about:blank',
                '--disable-extensions']
        if self.headless:
            chrome_args.append('--headless')
        logger.debug(' '.join(chrome_args))
        p = subprocess.Popen(
                chrome_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
        )
        time.sleep(self.startup_delay)
        self.chrome_pid = p.pid
        logger.debug(
                'Chrome PID: {} listening on port {}'.format(
                        p.pid, self.chrome_sock)
        )
        self.start_time = datetime.now()

    def clean_chrome(self):
        logger.debug('Kill Chrome...')
        os.kill(int(self.chrome_pid), signal.SIGTERM)
        logger.debug('Remove chrome profile...')
        time.sleep(self.shutdown_delay)
        subprocess.call(
                ['rm', '-rf', os.path.join(self.work_dir, 'chrome_profile')])

    def check_timeout(self):
        now = datetime.now()
        runtime = now - self.start_time
        if runtime.total_seconds() > self.master_timeout:
            logger.error('chrome master_timeout reached')
            os.kill(int(self.chrome_pid), signal.SIGTERM)
            time.sleep(self.shutdown_delay)
            subprocess.call(
                ['rm', '-rf', os.path.join(self.work_dir, 'chrome_profile')])

    def load_page(self, url):
        if not self.shell:
            self._start_console()
        data = self._send_chrome(
                {
                        "id": 0,
                        "method": "Page.navigate",
                        "params": {
                                "url": url
                        }
                }
        )
        self._read_data(data)
        loopcount = 0
        while (len(self.open_requests) > 0 and loopcount < 5
                and not self.stop_loading):
            self.check_timeout()
            logger.debug('we have {} open requests: {}'.format(
                    len(self.open_requests), self.open_requests))
            self._read_data()
            loopcount += 1
        if len(self.open_requests) > 0:
            logger.debug('open requests: {}'.format(self.open_requests))
        self._send_chrome(
                {"id": 0, "method": "Page.stopLoading"})
        logger.debug('writing log to {}...'.format(self.chrome_log_file))
        with open(self.chrome_log_file, 'w') as f:
            json.dump(self.chrome_log, f, indent=4)
        resp = self._send_chrome(
                {
                        "id": 0,
                        "method": "Page.captureScreenshot",
                        "params": {
                            "format": "jpeg",
                            "quality": 80
                        }
                }
        )
        while True:
            if 'result' in resp and 'data' in resp['result']:
                scrsht = os.path.join(self.work_dir, 'screenshot.jpg')
                with open(scrsht, 'wb') as f:
                    f.write(base64.b64decode(resp['result']['data']))
                logger.debug('screenshot written to {}'.format(scrsht))
                break
            else:
                logger.debug('got {}'.format(resp))
                resp = self._receive_chrome()
        # self._read_data()

    def get_content(self):
        if not os.path.exists(self.content_dir):
            os.makedirs(self.content_dir)
        cache_index_file = '{}/index.json'.format(self.content_dir)
        cache_index = {}
        req_count = 0
        self._get_requests()
        for req in self.reqs:
            if req.failed:
                continue
            if req.status_code not in (200, 206):
                logger.debug('No Content with Status {}: {} - {}'.format(
                        req.status_code, req.id, req.url))
                continue
            if req.content_length == 0:
                logger.debug(
                        'No Content with Content-Length 0: {} - {}'.format(
                                req.id, req.url)
                )
                continue
            response = ''
            cache_index[req.id] = {
                'url': req.url,
                'type': req.mime_type,
            }
            self.check_timeout()
            response = self._send_chrome({
                    "id": 0,
                    "method": "Network.getResponseBody",
                    "params": {"requestId": req.id}
            })
            while 'result' not in response and 'error' not in response:
                try:
                    response = self._receive_chrome()
                except websocket.WebSocketTimeoutException:
                    logger.debug('TIMEOUT REACHED')
                    break
                self.check_timeout()
            if not response:
                logger.error('TIMEOUT FAIL: {} - {}'.format(req.id, req.url))
                continue
            elif 'result' not in response:
                logger.error('RESPONSE FAIL: {} - {}'.format(req.id, req.url))
                logger.debug('response: {}'.format(response))
                logger.debug('content-length: {}'.format(req.content_length))
                continue
            response['result']['content-type'] = req.mime_type
            cfile = self.content_dir + '/{}'.format(req.id)
            with open(cfile, 'w') as f:
                json.dump(response, f, indent=4)
            req_count += 1
        logger.debug('{} content files saved'.format(req_count))
        with open(cache_index_file, 'w') as f:
            json.dump(cache_index, f, indent=4)
        logger.debug('cache index written.')

    def get_cookies(self):
        self.check_timeout()
        response = self._send_chrome({
            "id": 0,
            "method": "Network.getAllCookies",
        })
        while 'result' not in response:
            try:
                response = self._receive_chrome()
            except websocket.WebSocketTimeoutException:
                logger.debug('TIMEOUT REACHED WAITING FOR COOKIES')
                break
        logger.debug(
                'writing cookie log to {}...'.format(self.cookie_log_file))
        with open(self.cookie_log_file, 'w') as f:
            json.dump(response, f, indent=4)
