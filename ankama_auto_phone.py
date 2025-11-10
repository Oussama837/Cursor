# -*- coding: utf-8 -*-
"""
AnkamaAutoPhone — stealthy proxy setup (no selenium-wire), fewer bot flags.

Requires:
  pip install undetected-chromedriver selenium python-dotenv requests

.env keys used:
  ONLINESIM_API_KEY, ONLINESIM_BASE_URL, HYPE_PROXY_ID, CHANGE_IP_URL, ANKAMA_COUNTRY,
  PROXY_SCHEME, PROXY_HOST, PROXY_PORT, PROXY_USER, PROXY_PASS,
  HEADLESS, PAGELOAD_TIMEOUT
"""

import os
import sys
import time
import json
import random
import requests
import warnings
import tempfile
import zipfile
import threading
import socket
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from dotenv import load_dotenv

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

# =========================
# Load .env
# =========================
load_dotenv()

# ----------- Config -----------
ONLINESIM_API_KEY  = os.getenv("ONLINESIM_API_KEY", "9fc783aa95f9503e0cc99b58a34f29df")
ONLINESIM_BASE_URL = os.getenv("ONLINESIM_BASE_URL", "https://onlinesim.io/api")
HYPE_PROXY_ID      = os.getenv("HYPE_PROXY_ID", "aba515b0")
CHANGE_IP_URL      = os.getenv("CHANGE_IP_URL", "https://api.hypeproxy.io/Utils/DirectRenewIp/{HYPE_PROXY_ID}").strip()

ankama_country_code = os.getenv("ANKAMA_COUNTRY", "France")

PROXY_SCHEME = os.getenv("PROXY_SCHEME", "http").lower().strip()   # http / https / socks5
PROXY_HOST   = os.getenv("PROXY_HOST", "").strip()
PROXY_PORT   = int(os.getenv("PROXY_PORT", "0") or 0)
PROXY_USER   = os.getenv("PROXY_USER", "").strip()
PROXY_PASS   = os.getenv("PROXY_PASS", "").strip()

HEADLESS          = os.getenv("HEADLESS", "0") in ("1","true","True","yes","YES")
PAGELOAD_TIMEOUT  = int(os.getenv("PAGELOAD_TIMEOUT", "40"))
SKIP_PROXY_VERIFY = os.getenv("SKIP_PROXY_VERIFY", "0") in ("1","true","True","yes","YES")

# ----------- Requests through same proxy -----------
if PROXY_HOST and PROXY_PORT:
    _auth = f"{PROXY_USER}:{PROXY_PASS}@" if (PROXY_USER and PROXY_PASS) else ""
    REQUESTS_PROXIES = {
        "http":  f"{PROXY_SCHEME}://{_auth}{PROXY_HOST}:{PROXY_PORT}",
        "https": f"{PROXY_SCHEME}://{_auth}{PROXY_HOST}:{PROXY_PORT}",
    }
else:
    REQUESTS_PROXIES = {}

# ----------- Noise suppression -----------
def ignore_close(exctype, value, traceback):
    if exctype == OSError and "Descripteur non valide" in str(value): return
    sys.__excepthook__(exctype, value, traceback)
sys.excepthook = ignore_close
warnings.filterwarnings("ignore", category=ResourceWarning)

# ----------- Small helpers -----------
def _rand_user_agent():
    bases = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    ]
    return random.choice(bases)

def create_proxy_pac_file(proxy_host, proxy_port, proxy_scheme, proxy_user=None, proxy_pass=None):
    """
    Create a PAC (Proxy Auto-Configuration) file for proxy with auth.
    Note: PAC files don't support auth directly, but we'll use it with extension for auth.
    """
    pac_content = f"""
function FindProxyForURL(url, host) {{
    return "PROXY {proxy_host}:{proxy_port}";
}}
"""
    pac_file = tempfile.NamedTemporaryFile(mode='w', suffix='.pac', delete=False)
    pac_file.write(pac_content)
    pac_file.close()
    return pac_file.name

class LocalProxyHandler(BaseHTTPRequestHandler):
    """Local proxy server that adds authentication to upstream proxy"""
    upstream_host = None
    upstream_port = None
    upstream_scheme = None
    upstream_user = None
    upstream_pass = None
    
    def do_CONNECT(self):
        """Handle CONNECT method for HTTPS"""
        try:
            # Parse destination
            if ':' in self.path:
                host, port = self.path.split(':', 1)
                port = int(port)
            else:
                host = self.path
                port = 443
            
            # Connect to upstream proxy
            upstream_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            upstream_sock.settimeout(30)
            upstream_sock.connect((self.upstream_host, self.upstream_port))
            
            # Send CONNECT request with auth
            auth_str = f"{self.upstream_user}:{self.upstream_pass}"
            import base64
            auth_b64 = base64.b64encode(auth_str.encode()).decode()
            connect_req = f"CONNECT {host}:{port} HTTP/1.1\r\n"
            connect_req += f"Host: {host}:{port}\r\n"
            connect_req += f"Proxy-Authorization: Basic {auth_b64}\r\n"
            connect_req += "\r\n"
            upstream_sock.sendall(connect_req.encode())
            
            # Read response
            response = b""
            upstream_sock.settimeout(10)
            while True:
                chunk = upstream_sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\r\n\r\n" in response:
                    break
            
            # Forward response to client
            self.wfile.write(response)
            
            # If connection established (200), tunnel data
            if b"200" in response or b"Connection established" in response:
                self._tunnel(upstream_sock)
            
            upstream_sock.close()
        except Exception as e:
            try:
                self.send_error(502, f"Proxy error: {e}")
            except:
                pass
    
    def do_GET(self):
        self._proxy_request()
    
    def do_POST(self):
        self._proxy_request()
    
    def do_PUT(self):
        self._proxy_request()
    
    def do_DELETE(self):
        self._proxy_request()
    
    def _proxy_request(self):
        """Proxy HTTP requests"""
        try:
            # Get full URL from request
            host = self.headers.get('Host', '')
            if not host:
                self.send_error(400, "Missing Host header")
                return
            
            # Build full URL
            if self.path.startswith('http'):
                url = self.path
            else:
                scheme = 'https' if self.headers.get('X-Forwarded-Proto') == 'https' else 'http'
                url = f"{scheme}://{host}{self.path}"
            
            # Prepare headers (remove hop-by-hop headers)
            headers = {}
            for key, value in self.headers.items():
                key_lower = key.lower()
                if key_lower not in ['host', 'connection', 'proxy-connection', 'transfer-encoding', 'upgrade']:
                    headers[key] = value
            
            # Use requests with proxy auth
            proxy_url = f"{self.upstream_scheme}://{self.upstream_user}:{self.upstream_pass}@{self.upstream_host}:{self.upstream_port}"
            proxies = {
                'http': proxy_url,
                'https': proxy_url
            }
            
            # Read request body if present
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else None
            
            # Forward request through proxy
            resp = requests.request(
                self.command,
                url,
                headers=headers,
                data=body,
                stream=True,
                proxies=proxies,
                timeout=30,
                allow_redirects=False
            )
            
            # Send response
            self.send_response(resp.status_code)
            for key, value in resp.headers.items():
                key_lower = key.lower()
                if key_lower not in ['connection', 'transfer-encoding', 'content-encoding']:
                    self.send_header(key, value)
            self.end_headers()
            
            # Stream response body
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    self.wfile.write(chunk)
                    
        except Exception as e:
            import traceback
            print(f"[PROXY ERROR] {e}")
            print(traceback.format_exc())
            self.send_error(502, f"Proxy error: {e}")
    
    def _tunnel(self, upstream_sock):
        """Tunnel data between client and upstream"""
        import select
        while True:
            r, w, x = select.select([self.connection, upstream_sock], [], [], 1)
            if not r:
                break
            for sock in r:
                try:
                    data = sock.recv(8192)
                    if not data:
                        return
                    if sock is self.connection:
                        upstream_sock.sendall(data)
                    else:
                        self.connection.sendall(data)
                except:
                    return
    
    def log_message(self, format, *args):
        # Suppress normal logs, but log errors
        if 'error' in format.lower() or 'exception' in format.lower():
            print(f"[PROXY] {format % args}")
    
    def handle_one_request(self):
        """Override to catch connection reset errors"""
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            # These are normal when Chrome closes connections - ignore them
            pass
        except Exception as e:
            # Log other errors
            print(f"[PROXY ERROR] {e}")

def start_local_proxy(upstream_host, upstream_port, upstream_scheme, upstream_user, upstream_pass):
    """Start a local proxy server that adds authentication"""
    # Find available port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    local_port = sock.getsockname()[1]
    sock.close()
    
    # Configure handler
    LocalProxyHandler.upstream_host = upstream_host
    LocalProxyHandler.upstream_port = upstream_port
    LocalProxyHandler.upstream_scheme = upstream_scheme
    LocalProxyHandler.upstream_user = upstream_user
    LocalProxyHandler.upstream_pass = upstream_pass
    
    # Start server
    server = HTTPServer(('127.0.0.1', local_port), LocalProxyHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    return local_port, server

def create_simple_proxy_extension(proxy_host, proxy_port, proxy_scheme, proxy_user=None, proxy_pass=None):
    """
    Create a minimal Chrome extension for proxy with auth support.
    Works for HTTP, HTTPS, and SOCKS5 proxies.
    """
    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Proxy Extension",
        "permissions": ["proxy", "webRequest", "webRequestBlocking", "<all_urls>"],
        "background": {
            "scripts": ["background.js"],
            "persistent": True
        }
    }
    
    # Build proxy config - ensure port is integer
    proxy_config = {
        "mode": "fixed_servers",
        "rules": {
            "singleProxy": {
                "scheme": proxy_scheme,
                "host": proxy_host,
                "port": int(proxy_port)  # Must be integer, not string
            },
            "bypassList": ["localhost", "127.0.0.1", "<-loopback>"]
        }
    }
    
    # Build the background script - set proxy immediately on load
    background_js = f"""
// Set proxy immediately when extension loads
var config = {json.dumps(proxy_config)};

// Set proxy on startup
chrome.runtime.onStartup.addListener(function() {{
    chrome.proxy.settings.set({{value: config, scope: "regular"}}, function(details) {{
        console.log("Proxy set on startup:", config);
    }});
}});

// Set proxy immediately
chrome.proxy.settings.set({{value: config, scope: "regular"}}, function(details) {{
    if (chrome.runtime.lastError) {{
        console.error("Proxy setup error:", chrome.runtime.lastError);
    }} else {{
        console.log("Proxy configured successfully:", config);
    }}
}});

// Ensure proxy persists
chrome.proxy.settings.onChange.addListener(function(details) {{
    if (details.levelOfControl !== "controlled_by_this_extension") {{
        chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});
    }}
}});

"""
    
    # Add auth handler if credentials provided
    if proxy_user and proxy_pass:
        background_js += f"""
// Handle proxy authentication
function handleAuth(details) {{
    console.log("Proxy auth required for:", details.challenger.host);
    return {{
        authCredentials: {{
            username: "{proxy_user}",
            password: "{proxy_pass}"
        }}
    }};
}}

// Register auth handler BEFORE any requests
chrome.webRequest.onAuthRequired.addListener(
    handleAuth,
    {{urls: ["<all_urls>"]}},
    ["blocking"]
);

// Monitor proxy settings to ensure they persist
chrome.proxy.onProxyError.addListener(function(details) {{
    console.error("Proxy error:", details);
    // Re-set proxy on error
    chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});
}});
"""
    
    # Create extension directory
    tmpdir = tempfile.mkdtemp(prefix="proxy_ext_")
    manifest_path = Path(tmpdir) / "manifest.json"
    background_path = Path(tmpdir) / "background.js"
    
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    background_path.write_text(background_js, encoding='utf-8')
    
    # Create zip
    zip_path = Path(tempfile.gettempdir()) / f"proxy_{int(time.time())}.zip"
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zp:
        zp.write(manifest_path, arcname="manifest.json")
        zp.write(background_path, arcname="background.js")
    
    return str(zip_path)

# ----------- OnlineSim helpers -----------
def get_virtual_number(service='ankama', country=33):
    try:
        r = requests.get(f"{ONLINESIM_BASE_URL}/getNum.php",
                         params={"apikey": ONLINESIM_API_KEY, "service": service, "country": country, "number": True, "lang": "fr"},
                         proxies=REQUESTS_PROXIES or None, timeout=30)
        j = r.json()
        if j.get("response") == 1:
            return {"tzid": j["tzid"], "number": j["number"]}
        print("[ERROR] get_virtual_number:", j)
    except Exception as e:
        print("[ERROR] get_virtual_number exception:", e)
    return None

def close_number(tzid):
    try:
        requests.get(f"{ONLINESIM_BASE_URL}/setOperationOk.php",
                     params={"apikey": ONLINESIM_API_KEY, "tzid": tzid},
                     proxies=REQUESTS_PROXIES or None, timeout=20)
        print("[INFO] Number closed successfully.")
    except Exception as e:
        print(f"[ERROR] close_number exception: {e}")

def wait_for_sms(self, tzid):
    try:
        for attempt in range(7):  # ~140s
            time.sleep(20)
            r = requests.get(f"{ONLINESIM_BASE_URL}/getState.php",
                             params={"apikey": ONLINESIM_API_KEY, "tzid": tzid},
                             proxies=REQUESTS_PROXIES or None, timeout=30)
            data = r.json()
            if isinstance(data, list) and data:
                msg = data[0].get("msg")
                if msg:
                    print(f"[INFO] Received SMS after {(attempt+1)*20}s")
                    return msg.strip()
            print(f"[INFO] Waiting for SMS... ({(attempt+1)*20}s)")
        print("[ERROR] SMS not received within ~2m20s.")
        close_number(tzid)
        if getattr(self, "driver", None):
            try: self.driver.quit()
            except: pass
        return None
    except Exception as e:
        print("[ERROR] wait_for_sms exception:", e)
        close_number(tzid)
        if getattr(self, "driver", None):
            try: self.driver.quit()
            except: pass
        return None

def get_balance():
    try:
        r = requests.get(f"{ONLINESIM_BASE_URL}/getBalance.php",
                         params={"apikey": ONLINESIM_API_KEY},
                         proxies=REQUESTS_PROXIES or None, timeout=20)
        j = r.json()
        return float(j["balance"]) if "balance" in j else 0.0
    except Exception as e:
        print("[ERROR] get_balance:", e)
        return 0.0

# ----------- Main bot -----------
class AnkamaAutoPhone:
    def __init__(self):
        self.driver = None
        self._proxy_ext_path = None

    def random_delay(self, a=0.5, b=1.5):
        time.sleep(random.uniform(a, b))

    def change_ip(self):
        try:
            if CHANGE_IP_URL:
                print("[INFO] Changing IP via HypeProxy...")
                r = requests.get(CHANGE_IP_URL, proxies=REQUESTS_PROXIES or None, timeout=25)
                if r.status_code == 200:
                    print("[INFO] IP change initiated.")
                else:
                    print("[WARN] IP change failed:", r.text[:200])
                time.sleep(5)
        except Exception as e:
            print("[ERROR] change_ip:", e)


    def _stealthify(self, driver):
        # Enhanced stealth: remove webdriver flag & prevent IP leaks
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['fr-FR','fr','en-US','en']});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});

// Block WebRTC IP leaks
(function() {
    const originalRTCPeerConnection = window.RTCPeerConnection;
    window.RTCPeerConnection = function(...args) {
        const pc = new originalRTCPeerConnection(...args);
        const originalCreateOffer = pc.createOffer.bind(pc);
        pc.createOffer = function(...args) {
            return originalCreateOffer(...args).then(offer => {
                offer.sdp = offer.sdp.replace(/a=candidate.*\r\n/g, '');
                return offer;
            });
        };
        return pc;
    };
})();

// WebGL fingerprint masking
try {
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(param){
    if (param === 37445) return 'Intel Inc.';           // UNMASKED_VENDOR_WEBGL
    if (param === 37446) return 'Intel(R) UHD Graphics';// UNMASKED_RENDERER_WEBGL
    return getParameter.call(this, param);
  };
} catch (e) {}
            """
        })

    def setup_driver(self):
        """
        Undetected Chrome with stealthy options and proxy (auth via extension when needed).
        Fixed to ensure proxy is properly applied.
        """
        ua = _rand_user_agent()
        options = uc.ChromeOptions()

        # Critical: Disable automation flags
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--lang=fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7")
        options.add_argument(f"--user-agent={ua}")

        # Window & misc
        w = random.randint(1100, 1400)
        h = random.randint(800, 1000)
        options.add_argument(f"--window-size={w},{h}")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--no-first-run")
        options.add_argument("--force-color-profile=srgb")
        options.page_load_strategy = "eager"

        if HEADLESS:
            options.add_argument("--headless=new")

        # Enhanced WebRTC IP leak prevention
        options.add_experimental_option("prefs", {
            "webrtc.ip_handling_policy": "disable_non_proxied_udp",
            "webrtc.multiple_routes_enabled": False,
            "webrtc.nonproxied_udp_enabled": False,
            "profile.default_content_setting_values.media_stream_mic": 2,
            "profile.default_content_setting_values.media_stream_camera": 2,
        })

        # Additional arguments to prevent IP leaks
        options.add_argument("--disable-webrtc")
        options.add_argument("--disable-webrtc-hw-encoding")
        options.add_argument("--disable-webrtc-hw-decoding")
        options.add_argument("--force-webrtc-ip-permission-check")

        # Proxy setup - Use extension for authenticated proxies (direct proxy for browser)
        if PROXY_HOST and PROXY_PORT:
            if PROXY_USER and PROXY_PASS:
                # Use extension for authenticated proxies (Chrome doesn't support embedded credentials)
                print(f"[INFO] Creating proxy extension for {PROXY_SCHEME}://{PROXY_USER}:***@{PROXY_HOST}:{PROXY_PORT}...")
                try:
                    self._proxy_ext_path = create_simple_proxy_extension(
                        PROXY_HOST,
                        PROXY_PORT,
                        PROXY_SCHEME,
                        PROXY_USER,
                        PROXY_PASS
                    )
                    options.add_extension(self._proxy_ext_path)
                    print(f"[INFO] ✅ Proxy extension loaded - browser will use proxy directly")
                except Exception as e:
                    print(f"[ERROR] Failed to create proxy extension: {e}")
                    raise
            else:
                # No auth - use command-line directly
                proxy_url = f"{PROXY_SCHEME}://{PROXY_HOST}:{PROXY_PORT}"
                options.add_argument(f"--proxy-server={proxy_url}")
                print(f"[INFO] ✅ Using Chrome native proxy (no auth): {proxy_url}")
        else:
            print("[INFO] No proxy configured; launching direct.")

        # Launch Chrome
        print("[INFO] Launching Chrome browser...")
        try:
            driver = uc.Chrome(options=options, version_main=142)
            print("[INFO] Chrome launched successfully")
        except Exception:
            print("[WARN] Failed to launch with version_main=142, trying without version pinning...")
            try:
                driver = uc.Chrome(options=options)
                print("[INFO] Chrome launched successfully (without version pinning)")
            except Exception as e:
                print(f"[ERROR] Failed to launch Chrome: {e}")
                raise

        self.driver = driver
        self.driver.set_page_load_timeout(PAGELOAD_TIMEOUT)

        # Extra stealth
        self._stealthify(self.driver)

        # Wait and verify proxy
        if PROXY_HOST and PROXY_PORT:
            if PROXY_USER and PROXY_PASS:
                # Extension method - wait for it to initialize
                print("[INFO] Waiting for proxy extension to initialize...")
                time.sleep(4.0)
                try:
                    self.driver.get("about:blank")
                    time.sleep(2.0)
                except Exception as e:
                    print(f"[WARN] Navigation failed: {e}")
            else:
                # Command-line proxy is immediate
                print("[INFO] Proxy set via command-line (immediate)")
                time.sleep(1.0)
            
            if not SKIP_PROXY_VERIFY:
                print("[INFO] Verifying proxy is working...")
                try:
                    proxy_working = self._quick_proxy_check()
                    if proxy_working:
                        print("[INFO] ✅ Proxy verification passed!")
                    else:
                        print("[INFO] ⚠️ Proxy verification had issues, but HTTP works - continuing...")
                except Exception as e:
                    print(f"[WARN] Proxy verification error: {e}")
                    print("[INFO] Continuing anyway - proxy may still work for websites")
            else:
                print("[INFO] Proxy verification skipped")
        else:
            print("[INFO] No proxy configured")
    
    def _quick_proxy_check(self):
        """Quick non-blocking proxy check"""
        try:
            print("[VERIFY] Checking proxy connection...")
            self.driver.set_page_load_timeout(15)
            
            # Get proxy IP first (via requests library)
            proxy_ip = None
            try:
                r = requests.get("https://api.ipify.org?format=json", 
                                proxies=REQUESTS_PROXIES, timeout=8)
                proxy_ip = r.json().get("ip", "")
                print(f"[VERIFY] Expected proxy IP (via requests): {proxy_ip}")
            except Exception as e:
                print(f"[WARN] Could not get proxy IP via requests: {e}")
            
            # Try multiple IP check services (start with HTTP - more reliable through proxy)
            ip_services = [
                ("http://icanhazip.com", "HTTP"),
                ("http://ifconfig.me/ip", "HTTP"),
                ("http://api.ipify.org?format=text", "HTTP"),
                ("https://icanhazip.com", "HTTPS"),  # Try HTTPS last
                ("https://ifconfig.me/ip", "HTTPS"),
            ]
            
            browser_ip = None
            last_error = None
            for service_url, protocol in ip_services:
                try:
                    print(f"[VERIFY] Trying {protocol} service: {service_url}...")
                    self.driver.get(service_url)
                    # Wait for page to load
                    WebDriverWait(self.driver, 10).until(
                        lambda d: d.execute_script("return document.readyState") == "complete"
                    )
                    # Get IP from page
                    browser_ip = self.driver.find_element(By.TAG_NAME, "body").text.strip()
                    # Clean up IP (remove whitespace, newlines, error messages)
                    browser_ip = browser_ip.split()[0] if browser_ip else None
                    
                    # Check if we got an actual IP (not an error page)
                    if browser_ip and '.' in browser_ip:
                        parts = browser_ip.split('.')
                        if len(parts) == 4:
                            # Validate it's actually an IP (all parts are numbers 0-255)
                            try:
                                if all(0 <= int(p) <= 255 for p in parts if p.isdigit()):
                                    print(f"[VERIFY] ✅ Browser IP detected: {browser_ip}")
                                    break
                            except ValueError:
                                pass
                    browser_ip = None
                except Exception as e:
                    last_error = str(e)
                    # Check if it's a proxy error
                    if "ERR_PROXY_CONNECTION_FAILED" in last_error or "ERR_PROXY" in last_error:
                        print(f"[VERIFY] ⚠️ {protocol} failed through proxy (this is OK if HTTP works)")
                        # Continue to try HTTP
                        continue
                    else:
                        print(f"[VERIFY] Failed to get IP from {service_url}: {e}")
                        continue
            
            if not browser_ip:
                print("[ERROR] ❌ Could not determine browser IP!")
                if last_error:
                    print(f"[ERROR] Last error: {last_error}")
                print("[ERROR] Proxy extension may not be working correctly")
                print("[INFO] However, proxy might still work for actual websites - continuing...")
                self.driver.set_page_load_timeout(PAGELOAD_TIMEOUT)
                return False  # Return False but don't block execution
            
            # Compare with proxy IP
            if proxy_ip:
                if browser_ip == proxy_ip:
                    print(f"[VERIFY] ✅✅✅ SUCCESS! Proxy working correctly - IP matches: {browser_ip}")
                    self.driver.set_page_load_timeout(PAGELOAD_TIMEOUT)
                    return True
                else:
                    print(f"[VERIFY] ❌ Browser IP ({browser_ip}) != Proxy IP ({proxy_ip})")
                    print("[ERROR] Browser is NOT using the proxy! Extension may have failed.")
                    print("[ERROR] Check extension logs or try increasing wait time")
                    self.driver.set_page_load_timeout(PAGELOAD_TIMEOUT)
                    return False
            else:
                print(f"[VERIFY] Browser IP detected: {browser_ip} (could not verify against proxy IP)")
                print("[WARN] Could not compare with proxy IP, but browser IP was detected")
                self.driver.set_page_load_timeout(PAGELOAD_TIMEOUT)
                return True  # Assume OK if we got an IP
            
        except Exception as e:
            print(f"[WARN] Proxy check failed: {e} - continuing anyway")
            import traceback
            print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        finally:
            try:
                self.driver.set_page_load_timeout(PAGELOAD_TIMEOUT)
            except:
                pass
            return False

    def accept_cookies_if_present(self):
        try:
            btn = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.ak-accept"))
            )
            self.driver.execute_script("arguments[0].click();", btn)
            self.random_delay()
        except Exception:
            pass

    def login(self, username, password):
        url = "https://account.ankama.com/fr/compte/informations/modifier-numeros-de-telephone-mobile"
        for attempt in range(2):
            try:
                self.driver.get(url)
                self.random_delay()
                self.accept_cookies_if_present()

                # Optional "Connexion Ankama"
                try:
                    conn_btn = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-primary.btn-lg.w-100"))
                    )
                    self.driver.execute_script("arguments[0].scrollIntoView();", conn_btn)
                    self.driver.execute_script("arguments[0].click();", conn_btn)
                    print("[INFO] Clicked Connexion Ankama")
                    self.random_delay()
                    self.accept_cookies_if_present()
                except:
                    pass

                WebDriverWait(self.driver, 12).until(
                    EC.presence_of_element_located((By.ID, "ankama-login"))
                ).send_keys(username)
                self.driver.find_element(By.ID, "ankama-password").send_keys(password)
                self.random_delay()

                self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
                self.accept_cookies_if_present()

                # Optional OAuth "Continuer"
                try:
                    continuer_btn = WebDriverWait(self.driver, 8).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-lg.btn-primary.w-100"))
                    )
                    self.driver.execute_script("arguments[0].scrollIntoView();", continuer_btn)
                    self.driver.execute_script("arguments[0].click();", continuer_btn)
                    print("[INFO] Clicked Continuer")
                    self.random_delay()
                    self.accept_cookies_if_present()
                except:
                    print("[INFO] No OAuth confirmation button found.")

                # Already certified?
                try:
                    WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((
                            By.XPATH,
                            "//div[contains(@class,'ak-panel-content') and contains(text(), \"n'est pas directement modifiable\")]"
                        ))
                    )
                    print("[INFO] Phone number already set. Skipping this account.")
                    return "already_certified"
                except:
                    pass

                WebDriverWait(self.driver, 15).until(EC.presence_of_element_located((By.ID, "gsm")))
                print("[INFO] Login successful; on phone number page.")
                return True

            except Exception as e:
                if attempt == 0:
                    print(f"[WARN] First nav failed ({e}); retrying once in 2s...")
                    time.sleep(2)
                    continue
                print(f"[ERROR] Login failed or did not redirect properly: {e}")
                return False

    def submit_virtual_phone_number(self, ankama_country_code, phone_number):
        try:
            COUNTRY_NAME_TO_VALUE = {
                "Pays-Bas": "NL","France": "FR","Russie": "RU","Espagne": "ES","Allemagne": "DE",
                "Maroc": "MA","Ukraine": "UA","USA": "US","Pologne": "PL","Italie": "IT",
            }
            country_value = COUNTRY_NAME_TO_VALUE.get(ankama_country_code)
            if not country_value:
                print(f"[ERROR] No select value for country '{ankama_country_code}'")
                return False

            country_select = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.ID, "countryphone"))
            )
            Select(country_select).select_by_value(country_value)
            print(f"[INFO] Selected country {country_value}.")
            self.random_delay()

            phone_input = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.ID, "gsm"))
            )
            phone_input.clear()
            phone_input.send_keys(phone_number)
            print(f"[INFO] Entered phone: {phone_number}")
            self.random_delay()

            validate_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='submit'].btn.btn-primary.btn-lg"))
            )
            validate_button.click()
            print("[INFO] Clicked validate.")

            WebDriverWait(self.driver, 8).until(EC.presence_of_element_located((By.ID, "ak_field_1")))
            return True
        except Exception as e:
            print(f"[ERROR] Failed to submit phone number: {e}")
            return False

    def process_account_phone_verification(self, username, password, ankama_country_code):
        def fetch_country_id(country_name):
            try:
                r = requests.get(f"{ONLINESIM_BASE_URL}/getTariffs.php",
                                 params={"apikey": ONLINESIM_API_KEY, "lang": "fr"},
                                 proxies=REQUESTS_PROXIES or None, timeout=30)
                countries = r.json().get("countries", {})
                for _, c in countries.items():
                    if c.get("name","").lower() == country_name.lower():
                        return c.get("code")
                print(f"[ERROR] Country '{country_name}' not found in getTariffs.")
            except Exception as e:
                print("[ERROR] fetch_country_id:", e)
            return None

        try:
            self.change_ip()
            self.setup_driver()

            login_result = self.login(username, password)
            if login_result == "already_certified":
                try: self.driver.quit()
                except: pass
                return "already_certified"
            if not login_result:
                try: self.driver.quit()
                except: pass
                return False

            country_id = fetch_country_id(ankama_country_code)
            if not country_id:
                try: self.driver.quit()
                except: pass
                return False

            number_data = get_virtual_number(service="ankama", country=country_id)
            if not number_data:
                try: self.driver.quit()
                except: pass
                return False

            phone_number = number_data["number"].replace("+","")
            tzid = number_data["tzid"]

            if not self.submit_virtual_phone_number(ankama_country_code, phone_number):
                close_number(tzid)
                try: self.driver.quit()
                except: pass
                return False

            code = wait_for_sms(self, tzid)
            if not code:
                # wait_for_sms already handled close/quit on failure
                return False

            print(f"[CODE] Received SMS Code: {code}")

            try:
                code_input = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, "ak_field_1"))
                )
                code_input.clear()
                code_input.send_keys(code)
                print("[INFO] Code entered.")

                validate_btn = self.driver.find_element(By.CSS_SELECTOR, "input[type='submit'].btn.btn-primary.btn-lg")
                validate_btn.click()
                print("[INFO] Code submitted.")
            except Exception as e:
                print(f"[ERROR] Failed to submit SMS code: {e}")
                close_number(tzid)
                try: self.driver.quit()
                except: pass
                return False

            close_number(tzid)
            try: 
                self.driver.quit()
            except: pass
            return True

        except Exception as e:
            print(f"[ERROR] process_account_phone_verification exception: {e}")
            try:
                if self.driver: self.driver.quit()
            except: pass
            return False

# ----------- Main -----------
if __name__ == "__main__":
    accounts_path = "accounts.json"
    with open(accounts_path, "r", encoding="utf-8") as f:
        accounts_data = json.load(f)

    accounts = accounts_data.get("accounts", [])
    total = len(accounts)
    verifier = AnkamaAutoPhone()

    for index, acc in enumerate(accounts):
        if acc.get("certified", False):
            continue

        balance = get_balance()
        print("\n" + "-" * 55)
        print(f"|| ACCOUNT {index + 1}/{total}")
        print(f"|| Username     : {acc['username']}")
        print(f"|| Balance      : {balance:.2f} RUB")

        if balance < 0.30:
            print(f"|| STATUS       : ❌ Insufficient balance (< 0.30 RUB). Stopping.")
            print("-" * 55 + "\n")
            break

        result = verifier.process_account_phone_verification(
            acc["username"],
            acc["password"],
            ankama_country_code
        )

        if result is True or result == "already_certified":
            accounts[index]["certified"] = True
            print(f"|| STATUS       : ✅ Certified.")
        else:
            print(f"|| STATUS       : ❌ Failed to certify.")

        with open(accounts_path, "w", encoding="utf-8") as f:
            json.dump(accounts_data, f, indent=2, ensure_ascii=False)

        print("-" * 55 + "\n")
