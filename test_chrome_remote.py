from chromeremote import ChromeBrowser
import logging

logging.basicConfig(level=logging.DEBUG)

cb = ChromeBrowser(
    chrome_bin='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    socket=9989,
    remote_shell_timeout=2,
)
cb.start_chrome()
cb.load_page('https://github.com/tempelkim/chrome-remote-shell')
cb.get_content()
cb.get_cookies()
cb.clean_chrome()
