"""Optional real Chromium UI smoke, stdlib only. Uses isolated synthetic data.

python browser_smoke.py --browser /path/to/chrome
Artifacts stay in the ignored browser-artifacts directory. No external AI.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time
import urllib.request
from urllib.parse import urlparse
from unittest.mock import patch

from auth import AuthStore
from engine import CareerEngine
import server


class CDP:
    def __init__(self, url):
        address = urlparse(url)
        self.socket = socket.create_connection((address.hostname, address.port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        self.socket.sendall((f"GET {address.path} HTTP/1.1\r\nHost: {address.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nOrigin: http://localhost\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        response = b""
        while not response.endswith(b"\r\n\r\n"):
            response += self.socket.recv(1)
        if not response.startswith(b"HTTP/1.1 101 "):
            raise RuntimeError("Chromium websocket handshake failed")
        self.sequence = 0

    def read(self, count):
        data = b""
        while len(data) < count:
            chunk = self.socket.recv(count - len(data))
            if not chunk:
                raise RuntimeError("Chromium connection closed")
            data += chunk
        return data

    def call(self, method, **params):
        self.sequence += 1
        data = json.dumps({"id": self.sequence, "method": method, "params": params}).encode()
        mask = os.urandom(4)
        size = len(data)
        header = bytes([0x81, 0x80 | (size if size < 126 else 126 if size < 65536 else 127)])
        if size >= 126:
            header += struct.pack("!H" if size < 65536 else "!Q", size)
        self.socket.sendall(header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(data)))
        while True:
            first, second = self.read(2)
            size = second & 127
            if size == 126:
                size = struct.unpack("!H", self.read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.read(8))[0]
            payload = self.read(size)
            if first & 15 == 8:
                raise RuntimeError("Chromium websocket closed")
            result = json.loads(payload)
            if result.get("id") == self.sequence:
                if "error" in result:
                    raise RuntimeError(str(result["error"]))
                return result.get("result", {})

    def js(self, expression):
        result = self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if result.get("exceptionDetails"):
            raise RuntimeError(str(result["exceptionDetails"]))
        return result["result"].get("value")

    def until(self, expression):
        until = time.monotonic() + 12
        while time.monotonic() < until:
            if self.js(expression):
                return
            time.sleep(.08)
        raise AssertionError(f"UI condition timed out: {expression}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", required=True)
    args = parser.parse_args()
    artifacts = Path(__file__).parent / "browser-artifacts"
    artifacts.mkdir(exist_ok=True)
    user_dir = artifacts / ("chromium-" + str(time.time_ns()))
    user_dir.mkdir()
    server.AUTH = AuthStore([("employee", "browser-test", "employee", "E0028"), ("hr", "browser-test", "hr", None)])
    server.ENGINE = CareerEngine()
    http = server.CareerQuestServer(("127.0.0.1", 0), server.CareerQuestHandler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    chrome = subprocess.Popen([args.browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", "--remote-allow-origins=http://localhost", f"--user-data-dir={user_dir.resolve()}", "about:blank"], stdout=subprocess.DEVNULL, stderr=open(artifacts / 'chrome.log', 'w'), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    browser = None
    report = []
    try:
        port_file = user_dir / "DevToolsActivePort"
        deadline = time.monotonic() + 15
        while not port_file.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        port = int(port_file.read_text().splitlines()[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json") as response:
            tab = next(item for item in json.load(response) if item["type"] == "page")
        browser = CDP(tab["webSocketDebuggerUrl"])
        browser.call("Page.enable")
        browser.call("Page.addScriptToEvaluateOnNewDocument", source="window.uiErrors=[]; addEventListener('error', e=>uiErrors.push(e.message)); addEventListener('unhandledrejection', e=>uiErrors.push(String(e.reason)));")
        for width, height in [(1440, 900), (1366, 768), (390, 844)]:
            server.ENGINE.reset()
            browser.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
            browser.call("Network.clearBrowserCookies")
            browser.call("Page.navigate", url=f"http://127.0.0.1:{http.server_port}")
            browser.until("document.querySelector('#loginButton') && !document.querySelector('#loginView').hidden")

            def capture(name):
                assert browser.js("document.documentElement.scrollWidth <= innerWidth"), "page overflow"
                data = browser.call("Page.captureScreenshot", format="png")["data"]
                (artifacts / f"{width}-{name}.png").write_bytes(base64.b64decode(data))

            def login(role):
                browser.js(f"document.querySelector('#loginUsername').value='{role}'; document.querySelector('#loginPassword').value='browser-test'; document.querySelector('#loginForm').requestSubmit()")

            capture("login")
            login("employee")
            browser.until("document.querySelectorAll('[data-details]').length > 0 && !document.querySelector('#employeeView').hidden")
            browser.until("document.querySelectorAll('[data-plan-event]').length > 0")
            capture("profile")
            browser.js("document.querySelector('[data-details]').focus(); document.querySelector('[data-details]').click()")
            browser.until("document.querySelector('#explanationModal').classList.contains('open')")
            assert browser.js("document.querySelector('#explanationModal').contains(document.activeElement)"), "modal focus"
            capture("explanation")
            browser.js("document.querySelector('#explanationModal .modal-close').focus()")
            browser.call("Input.dispatchKeyEvent", type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9)
            assert browser.js("document.querySelector('#explanationModal').contains(document.activeElement)"), "modal tab trap"
            browser.call("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
            assert browser.js("document.activeElement.matches('[data-details]')"), "focus return"
            before = browser.js("state.profile.readiness")
            browser.js("document.querySelector('[data-simulate]').click()")
            browser.until("document.querySelector('#simulationResult h3') !== null")
            capture("simulation")
            browser.js("document.querySelector('[data-complete]').click()")
            browser.until(f"state.profile.readiness > {before}")
            browser.js("document.querySelector('#logoutButton').click()")
            browser.until("!document.querySelector('#loginView').hidden")
            login("hr")
            browser.until("!document.querySelector('#hrView').hidden")
            browser.js("scrollTo(0,0)")
            capture("hr")
            # UI file selection via the native File object, same preview/commit handlers.
            sample = (Path(__file__).parent / "sample_upload/employees.json").read_text(encoding="utf-8-sig")
            profiles = json.loads(sample)
            profiles[0]["name"] = '<img src=x onerror="window.importExecuted=true">'
            sample = json.dumps(profiles)
            browser.js("document.querySelector('#openUploadButton').click()")
            browser.js(f"selectFiles([new File([{json.dumps(sample)}], 'employees.json', {{type:'application/json'}})])")
            browser.until("!document.querySelector('#uploadButton').disabled")
            capture("import")
            browser.js("document.querySelector('#uploadButton').click()")
            browser.until("document.querySelector('#uploadModal').hidden && !document.querySelector('#hrView').hidden")
            browser.js("state.view='employee'; loadEmployee('JURY01')")
            browser.until("state.profile?.employee.employee_id === 'JURY01'")
            assert browser.js("!window.importExecuted && document.querySelector('#employeeName').textContent.includes('<img')"), "imported HTML must stay text"
            browser.js("document.querySelector('#openUploadButton').click()")
            browser.js("selectFiles([new File(['{broken'], 'employees.json')])")
            browser.until("document.querySelector('#importPreview').textContent.startsWith('Ошибка:')")
            assert browser.js("document.querySelector('#uploadButton').disabled"), "invalid import must be disabled"
            browser.js("closeModals()")
            # Old AI requests must not replace results after a time-budget change.
            browser.js("state.view='employee'; loadEmployee('E0028')")
            browser.until("state.profile && document.querySelectorAll('[data-plan-event]').length > 0")
            browser.js("window.oldApi = api; window.releaseAi = null; api = (path, options) => path === '/api/recommendations/ai' ? new Promise(resolve => window.releaseAi = resolve) : oldApi(path, options); window.oldProfile=state.profile; void refreshAiRecommendations(oldProfile)")
            browser.js("document.querySelector('#plannerHours').value='2'; document.querySelector('#plannerHours').dispatchEvent(new Event('change'))")
            browser.until("state.profile.recommendations.length === 0")
            browser.js("releaseAi({recommendations:[{event_id:'STALE'}], revision:oldProfile.revision, ai_used:true, decision_source:'hybrid_ai'}); api=oldApi")
            browser.until("state.profile.recommendations.length === 0 && !state.profile.ai_used")
            assert browser.js("uiErrors.length === 0"), browser.js("uiErrors")
            report.append({"viewport": f"{width}x{height}", "flow": "PASS", "horizontal_overflow": False, "console_errors": 0})
        (artifacts / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    finally:
        if browser:
            try:
                browser.call("Browser.close")
            except Exception:
                pass
            browser.socket.close()
        chrome.terminate() if chrome.poll() is None else None
        http.shutdown()
        http.server_close()


if __name__ == "__main__":
    with patch.dict(os.environ, {"CQ_ALLOW_EXTERNAL_AI": "0", "CQ_QUIET": "1"}):
        main()
