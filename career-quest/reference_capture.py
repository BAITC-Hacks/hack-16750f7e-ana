"""Read-only visual inspection of the supplied design site; optional Chromium."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from browser_smoke import CDP


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    reference = root.parent / 'design' / 'career-quest'
    def hashes():
        return {str(p.relative_to(reference)): hashlib.sha256(p.read_bytes()).hexdigest() for p in reference.rglob('*') if p.is_file()}
    before = hashes()
    artifacts = root / 'browser-artifacts'
    artifacts.mkdir(exist_ok=True)
    user_dir = artifacts / ('reference-chromium-' + str(time.time_ns()))
    user_dir.mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'CQ_EMPLOYEE_PASSWORD': 'reference-employee', 'CQ_HR_PASSWORD': 'reference-hr', 'CQ_QUIET': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8'}
    command = f"import sys,runpy; sys.path.insert(0,'.'); sys.argv=['server.py','--port','{port}']; runpy.run_path('server.py',run_name='__main__')"
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    server = subprocess.Popen([sys.executable, '-B', '-c', command], cwd=reference, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    chrome = subprocess.Popen([args.browser, '--headless=new', '--disable-gpu', '--no-first-run', '--remote-debugging-port=0', '--remote-allow-origins=http://localhost', f'--user-data-dir={user_dir}', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    cdp = None
    try:
        for _ in range(150):
            if (user_dir / 'DevToolsActivePort').exists():
                break
            time.sleep(.1)
        debug_port = int((user_dir / 'DevToolsActivePort').read_text().splitlines()[0])
        with urllib.request.urlopen(f'http://127.0.0.1:{debug_port}/json') as response:
            tab = next(item for item in json.load(response) if item['type'] == 'page')
        cdp = CDP(tab['webSocketDebuggerUrl'])
        for width, height in [(1440, 900), (390, 844)]:
            cdp.call('Emulation.setDeviceMetricsOverride', width=width, height=height, deviceScaleFactor=1, mobile=False)
            cdp.call('Network.clearBrowserCookies')
            cdp.call('Page.navigate', url=f'http://127.0.0.1:{port}')
            cdp.until("document.querySelector('#loginForm') !== null")
            def capture(name):
                data = cdp.call('Page.captureScreenshot', format='png')['data']
                (artifacts / f'reference-{width}-{name}.png').write_bytes(base64.b64decode(data))
            capture('landing')
            cdp.js("document.querySelector('#loginForm').scrollIntoView({block:'center',behavior:'instant'})")
            capture('login')
            cdp.js("document.querySelector('#loginUsername').value='employee';document.querySelector('#loginPassword').value='reference-employee';document.querySelector('#loginForm').requestSubmit()")
            cdp.until("document.querySelector('#careerTwin') && !document.querySelector('#careerTwin').hidden")
            cdp.js("scrollTo({top:0,behavior:'instant'})")
            capture('employee')
            cdp.js("document.querySelector('#twinExplainButton')?.click()")
            capture('explanation')
            cdp.js("closeModals()")
            if width < 900:
                cdp.js("document.querySelector('#mobileMenu').click()")
                capture('menu')
            cdp.call('Network.clearBrowserCookies')
            cdp.call('Page.navigate', url=f'http://127.0.0.1:{port}')
            cdp.until("document.querySelector('#loginForm') !== null")
            cdp.js("document.querySelector('#loginUsername').value='hr';document.querySelector('#loginPassword').value='reference-hr';document.querySelector('#loginForm').requestSubmit()")
            cdp.until("!document.querySelector('#appShell').hidden")
            cdp.js("document.querySelector('#hrNav').click()")
            cdp.until("!document.querySelector('#hrView').hidden")
            capture('hr')
            cdp.js("document.querySelector('#securityNav').click()")
            cdp.until("!document.querySelector('#securityView').hidden")
            capture('security')
        assert hashes() == before, 'Reference files changed'
        (artifacts / 'reference-hashes.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
        print('Reference desktop/mobile captured; source hashes unchanged.')
    finally:
        if cdp:
            try:
                cdp.call('Browser.close')
            except Exception:
                pass
            cdp.socket.close()
        if chrome.poll() is None:
            chrome.terminate()
        server.terminate()
        server.wait(timeout=5)


if __name__ == '__main__':
    main()
