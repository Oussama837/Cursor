import os
import sys
import time
import json
import random
import requests
import warnings
import tempfile
import zipfile
import json as _json
from pathlib import Path

from dotenv import load_dotenv

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC


load_dotenv()

ONLINESIM_API_KEY = os.getenv("ONLINESIM_API_KEY", "9fc783aa95f9503e0cc99b58a34f29df")
ONLINESIM_BASE_URL = os.getenv("ONLINESIM_BASE_URL", "https://onlinesim.io/api")
HYPE_PROXY_ID = os.getenv("HYPE_PROXY_ID", "aba515b0")
CHANGE_IP_URL = os.getenv("CHANGE_IP_URL", "https://api.hypeproxy.io/Utils/DirectRenewIp/{HYPE_PROXY_ID}").strip()
if "{HYPE_PROXY_ID}" in CHANGE_IP_URL:
    CHANGE_IP_URL = CHANGE_IP_URL.format(HYPE_PROXY_ID=HYPE_PROXY_ID)

ankama_country_code = os.getenv("ANKAMA_COUNTRY", "France")

PROXY_SCHEME = os.getenv("PROXY_SCHEME", "http").lower().strip()
PROXY_HOST = os.getenv("PROXY_HOST", "").strip()
PROXY_PORT = os.getenv("PROXY_PORT", "").strip()
PROXY_USER = os.getenv("PROXY_USER", "").strip()
PROXY_PASS = os.getenv("PROXY_PASS", "").strip()

if PROXY_PORT.isdigit():
    PROXY_PORT = int(PROXY_PORT)
else:
    PROXY_PORT = 0

HEADLESS = os.getenv("HEADLESS", "0") in ("1", "true", "True", "yes", "YES")
PAGELOAD_TIMEOUT = int(os.getenv("PAGELOAD_TIMEOUT", "40"))

if PROXY_HOST and PROXY_PORT:
    _auth = f"{PROXY_USER}:{PROXY_PASS}@" if (PROXY_USER and PROXY_PASS) else ""
    REQUESTS_PROXIES = {
        "http": f"{PROXY_SCHEME}://{_auth}{PROXY_HOST}:{PROXY_PORT}",
        "https": f"{PROXY_SCHEME}://{_auth}{PROXY_HOST}:{PROXY_PORT}",
    }
else:
    REQUESTS_PROXIES = {}


def ignore_close(exctype, value, traceback):
    if exctype == OSError and "Descripteur non valide" in str(value):
        return
    sys.__excepthook__(exctype, value, traceback)


sys.excepthook = ignore_close
warnings.filterwarnings("ignore", category=ResourceWarning)


def _rand_user_agent():
    bases = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    ]
    return random.choice(bases)


def create_proxy_auth_extension(proxy_host, proxy_port, proxy_scheme, proxy_user, proxy_pass):
    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking",
        ],
        "background": {"scripts": ["background.js"]},
    }
    background_js = f"""
chrome.proxy.settings.clear({{scope: "regular"}}, function(){{}});
var config = {{
  mode: "fixed_servers",
  rules: {{
    singleProxy: {{ scheme: "{proxy_scheme}", host: "{proxy_host}", port: parseInt({proxy_port}) }},
    bypassList: ["localhost"]
  }}
}};
chrome.proxy.settings.set({{value: config, scope: "regular"}}, function(){{}});
function cb(details) {{
  return {{authCredentials: {{username: "{proxy_user}", password: "{proxy_pass}"}}}};
}}
chrome.webRequest.onAuthRequired.addListener(
  cb, {{urls: ["<all_urls>"]}}, ["blocking"]
);
"""
    tmpdir = tempfile.mkdtemp(prefix="proxy_ext_")
    Path(tmpdir, "manifest.json").write_text(_json.dumps(manifest), encoding="utf-8")
    Path(tmpdir, "background.js").write_text(background_js, encoding="utf-8")

    zip_path = Path(tempfile.gettempdir()) / f"proxy_auth_{int(time.time())}.zip"
    with zipfile.ZipFile(zip_path, "w") as zp:
        zp.write(Path(tmpdir) / "manifest.json", arcname="manifest.json")
        zp.write(Path(tmpdir) / "background.js", arcname="background.js")
    return str(zip_path)


def get_virtual_number(service="ankama", country=33):
    try:
        r = requests.get(
            f"{ONLINESIM_BASE_URL}/getNum.php",
            params={"apikey": ONLINESIM_API_KEY, "service": service, "country": country, "number": True, "lang": "fr"},
            proxies=REQUESTS_PROXIES or None,
            timeout=30,
        )
        j = r.json()
        if j.get("response") == 1:
            return {"tzid": j["tzid"], "number": j["number"]}
        print("[ERROR] get_virtual_number:", j)
    except Exception as e:
        print("[ERROR] get_virtual_number exception:", e)
    return None


def close_number(tzid):
    try:
        requests.get(
            f"{ONLINESIM_BASE_URL}/setOperationOk.php",
            params={"apikey": ONLINESIM_API_KEY, "tzid": tzid},
            proxies=REQUESTS_PROXIES or None,
            timeout=20,
        )
        print("[INFO] Number closed successfully.")
    except Exception as e:
        print(f"[ERROR] close_number exception: {e}")


def wait_for_sms(self, tzid):
    try:
        for attempt in range(7):
            time.sleep(20)
            r = requests.get(
                f"{ONLINESIM_BASE_URL}/getState.php",
                params={"apikey": ONLINESIM_API_KEY, "tzid": tzid},
                proxies=REQUESTS_PROXIES or None,
                timeout=30,
            )
            data = r.json()
            if isinstance(data, list) and data:
                msg = data[0].get("msg")
                if msg:
                    print(f"[INFO] Received SMS after {(attempt + 1) * 20}s")
                    return msg.strip()
            print(f"[INFO] Waiting for SMS... ({(attempt + 1) * 20}s)")
        print("[ERROR] SMS not received within ~2m20s.")
        close_number(tzid)
        if getattr(self, "driver", None):
            try:
                self.driver.quit()
            except Exception:
                pass
        return None
    except Exception as e:
        print("[ERROR] wait_for_sms exception:", e)
        close_number(tzid)
        if getattr(self, "driver", None):
            try:
                self.driver.quit()
            except Exception:
                pass
        return None


def get_balance():
    try:
        r = requests.get(
            f"{ONLINESIM_BASE_URL}/getBalance.php",
            params={"apikey": ONLINESIM_API_KEY},
            proxies=REQUESTS_PROXIES or None,
            timeout=20,
        )
        j = r.json()
        return float(j["balance"]) if "balance" in j else 0.0
    except Exception as e:
        print("[ERROR] get_balance:", e)
        return 0.0


class AnkamaAutoPhone:
    def __init__(self):
        self.driver = None
        self._proxy_extension_path = None

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

    def _wait_proxy_ready(self, retries=5, delay=2.0):
        for i in range(retries):
            try:
                self.driver.get("https://api.ipify.org/?format=text")
                WebDriverWait(self.driver, 8).until(lambda d: d.execute_script("return document.readyState") == "complete")
                ip = self.driver.find_element(By.TAG_NAME, "body").text.strip()
                print(f"[VERIFY] Browser sees IP: {ip}")
                return True
            except Exception as e:
                print(f"[WARN] Proxy not ready yet (try {i + 1}/{retries}): {e}")
                time.sleep(delay)
        return False

    def _stealthify(self, driver):
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['fr-FR','fr','en-US','en']});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
try {
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(param){
    if (param === 37445) return 'Intel Inc.';
    if (param === 37446) return 'Intel(R) UHD Graphics';
    return getParameter.call(this, param);
  };
} catch (e) {}
            """,
            },
        )

    def setup_driver(self):
        ua = _rand_user_agent()
        options = uc.ChromeOptions()

        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--lang=fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7")
        options.add_argument(f"--user-agent={ua}")

        w = random.randint(1100, 1400)
        h = random.randint(800, 1000)
        options.add_argument(f"--window-size={w},{h}")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--no-first-run")
        options.add_argument("--force-color-profile=srgb")
        options.page_load_strategy = "eager"

        if HEADLESS:
            options.add_argument("--headless=new")

        options.add_experimental_option(
            "prefs",
            {
                "webrtc.ip_handling_policy": "default_public_interface_only",
                "webrtc.multiple_routes_enabled": False,
                "webrtc.nonproxied_udp_enabled": False,
            },
        )

        proxy_in_use = False
        if PROXY_HOST and PROXY_PORT:
            proxy_address = f"{PROXY_SCHEME}://{PROXY_HOST}:{PROXY_PORT}"
            options.add_argument(f"--proxy-server={proxy_address}")
            options.add_argument("--proxy-bypass-list=<-loopback>")
            proxy_in_use = True
            if PROXY_USER and PROXY_PASS:
                self._proxy_extension_path = create_proxy_auth_extension(
                    PROXY_HOST, PROXY_PORT, PROXY_SCHEME, PROXY_USER, PROXY_PASS
                )
                options.add_extension(self._proxy_extension_path)
                print(f"[INFO] Using proxy via extension: {proxy_address}")
            else:
                print(f"[INFO] Using proxy via --proxy-server: {proxy_address}")
        else:
            print("[INFO] No proxy configured; launching direct.")

        try:
            driver = uc.Chrome(options=options, version_main=142)
        except Exception:
            driver = uc.Chrome(options=options)

        self.driver = driver
        self.driver.set_page_load_timeout(PAGELOAD_TIMEOUT)
        self._stealthify(self.driver)

        time.sleep(1.0)
        if proxy_in_use and not self._wait_proxy_ready():
            print("[ERROR] Proxy not ready (DNS/connection). Retrying once...")
            time.sleep(2)
            if not self._wait_proxy_ready():
                raise RuntimeError("Proxy/DNS never became ready in browser")

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

                try:
                    conn_btn = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-primary.btn-lg.w-100"))
                    )
                    self.driver.execute_script("arguments[0].scrollIntoView();", conn_btn)
                    self.driver.execute_script("arguments[0].click();", conn_btn)
                    print("[INFO] Clicked Connexion Ankama")
                    self.random_delay()
                    self.accept_cookies_if_present()
                except Exception:
                    pass

                WebDriverWait(self.driver, 12).until(EC.presence_of_element_located((By.ID, "ankama-login"))).send_keys(
                    username
                )
                self.driver.find_element(By.ID, "ankama-password").send_keys(password)
                self.random_delay()

                self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
                self.accept_cookies_if_present()

                try:
                    continuer_btn = WebDriverWait(self.driver, 8).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn.btn-lg.btn-primary.w-100"))
                    )
                    self.driver.execute_script("arguments[0].scrollIntoView();", continuer_btn)
                    self.driver.execute_script("arguments[0].click();", continuer_btn)
                    print("[INFO] Clicked Continuer")
                    self.random_delay()
                    self.accept_cookies_if_present()
                except Exception:
                    print("[INFO] No OAuth confirmation button found.")

                try:
                    WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located(
                            (By.XPATH, "//div[contains(@class,'ak-panel-content') and contains(text(), \"n'est pas directement modifiable\")]")
                        )
                    )
                    print("[INFO] Phone number already set. Skipping this account.")
                    return "already_certified"
                except Exception:
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
                "Pays-Bas": "NL",
                "France": "FR",
                "Russie": "RU",
                "Espagne": "ES",
                "Allemagne": "DE",
                "Maroc": "MA",
                "Ukraine": "UA",
                "USA": "US",
                "Pologne": "PL",
                "Italie": "IT",
            }
            country_value = COUNTRY_NAME_TO_VALUE.get(ankama_country_code)
            if not country_value:
                print(f"[ERROR] No select value for country '{ankama_country_code}'")
                return False

            country_select = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.ID, "countryphone")))
            Select(country_select).select_by_value(country_value)
            print(f"[INFO] Selected country {country_value}.")
            self.random_delay()

            phone_input = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.ID, "gsm")))
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
                r = requests.get(
                    f"{ONLINESIM_BASE_URL}/getTariffs.php",
                    params={"apikey": ONLINESIM_API_KEY, "lang": "fr"},
                    proxies=REQUESTS_PROXIES or None,
                    timeout=30,
                )
                countries = r.json().get("countries", {})
                for _, c in countries.items():
                    if c.get("name", "").lower() == country_name.lower():
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
                try:
                    self.driver.quit()
                except Exception:
                    pass
                return "already_certified"
            if not login_result:
                try:
                    self.driver.quit()
                except Exception:
                    pass
                return False

            country_id = fetch_country_id(ankama_country_code)
            if not country_id:
                try:
                    self.driver.quit()
                except Exception:
                    pass
                return False

            number_data = get_virtual_number(service="ankama", country=country_id)
            if not number_data:
                try:
                    self.driver.quit()
                except Exception:
                    pass
                return False

            phone_number = number_data["number"].replace("+", "")
            tzid = number_data["tzid"]

            if not self.submit_virtual_phone_number(ankama_country_code, phone_number):
                close_number(tzid)
                try:
                    self.driver.quit()
                except Exception:
                    pass
                return False

            code = wait_for_sms(self, tzid)
            if not code:
                return False

            print(f"[CODE] Received SMS Code: {code}")

            try:
                code_input = WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.ID, "ak_field_1")))
                code_input.clear()
                code_input.send_keys(code)
                print("[INFO] Code entered.")

                validate_btn = self.driver.find_element(By.CSS_SELECTOR, "input[type='submit'].btn.btn-primary.btn-lg")
                validate_btn.click()
                print("[INFO] Code submitted.")
            except Exception as e:
                print(f"[ERROR] Failed to submit SMS code: {e}")
                close_number(tzid)
                try:
                    self.driver.quit()
                except Exception:
                    pass
                return False

            close_number(tzid)
            try:
                self.driver.quit()
            except Exception:
                pass
            return True

        except Exception as e:
            print(f"[ERROR] process_account_phone_verification exception: {e}")
            try:
                if self.driver:
                    self.driver.quit()
            except Exception:
                pass
            return False


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
            ankama_country_code,
        )

        if result is True or result == "already_certified":
            accounts[index]["certified"] = True
            print(f"|| STATUS       : ✅ Certified.")
        else:
            print(f"|| STATUS       : ❌ Failed to certify.")

        with open(accounts_path, "w", encoding="utf-8") as f:
            json.dump(accounts_data, f, indent=2, ensure_ascii=False)

        print("-" * 55 + "\n")
