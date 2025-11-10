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
from pathlib import Path

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

        # Proxy setup - ALWAYS use extension for authenticated proxies (Chrome doesn't support auth in --proxy-server)
        if PROXY_HOST and PROXY_PORT:
            proxy_display = f"{PROXY_SCHEME}://{PROXY_USER}:***@{PROXY_HOST}:{PROXY_PORT}" if (PROXY_USER and PROXY_PASS) else f"{PROXY_SCHEME}://{PROXY_HOST}:{PROXY_PORT}"
            
            # Always use extension when auth is required (Chrome's --proxy-server doesn't support embedded credentials properly)
            if PROXY_USER and PROXY_PASS:
                print(f"[INFO] Creating proxy extension for {proxy_display}...")
                try:
                    self._proxy_ext_path = create_simple_proxy_extension(
                        PROXY_HOST, 
                        PROXY_PORT, 
                        PROXY_SCHEME, 
                        PROXY_USER,
                        PROXY_PASS
                    )
                    options.add_extension(self._proxy_ext_path)
                    print(f"[INFO] ✅ Proxy extension loaded (required for authenticated proxies)")
                except Exception as e:
                    print(f"[ERROR] Failed to create proxy extension: {e}")
                    raise
            else:
                # No auth - can use command-line
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
            # If using extension (authenticated proxies), wait for it to initialize
            if PROXY_USER and PROXY_PASS:
                print("[INFO] Waiting for proxy extension to initialize...")
                time.sleep(4.0)  # Longer wait for extension
                try:
                    print("[INFO] Triggering extension by navigating...")
                    self.driver.get("about:blank")
                    time.sleep(2.0)  # Give extension time to set proxy
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
                    if not proxy_working:
                        print("[WARN] ⚠️ Proxy verification failed!")
                        print("[WARN] This might be a false negative - proxy may still work for actual websites")
                        print("[WARN] Continuing execution...")
                except Exception as e:
                    print(f"[WARN] Proxy verification error: {e}")
                    print("[WARN] Continuing anyway - proxy may still work")
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
            
            # Try multiple IP check services (start with simpler ones)
            ip_services = [
                ("https://icanhazip.com", "HTTPS"),
                ("http://icanhazip.com", "HTTP"),
                ("https://ifconfig.me/ip", "HTTPS"),
                ("http://ifconfig.me/ip", "HTTP"),
                ("https://api.ipify.org?format=text", "HTTPS"),
                ("http://api.ipify.org?format=text", "HTTP"),
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
                    if "ERR_NO_SUPPORTED_PROXIES" in last_error or "ERR_PROXY" in last_error:
                        print(f"[ERROR] Proxy error detected: {last_error}")
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
                    return True
                else:
                    print(f"[VERIFY] ❌ Browser IP ({browser_ip}) != Proxy IP ({proxy_ip})")
                    print("[ERROR] Browser is NOT using the proxy! Extension may have failed.")
                    print("[ERROR] Check extension logs or try increasing wait time")
                    return False
            else:
                print(f"[VERIFY] Browser IP detected: {browser_ip} (could not verify against proxy IP)")
                print("[WARN] Could not compare with proxy IP, but browser IP was detected")
                return True
            
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
            try: self.driver.quit()
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
