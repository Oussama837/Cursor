import json
import logging
import os
import time
from typing import Optional

import requests
from seleniumwire import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None


ITERATIONS = 5
IP_CHECK_URL = "https://api.ipify.org?format=json"
IP_BROWSER_URL = "https://www.whatismyip.com"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def bool_from_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip() not in {"0", "false", "False", ""}


def get_env_config() -> dict:
    """Collect runtime settings from environment variables."""
    config = {
        "onlinesim_api_key": os.getenv("ONLINESIM_API_KEY"),
        "onlinesim_base_url": os.getenv("ONLINESIM_BASE_URL"),
        "hype_proxy_id": os.getenv("HYPE_PROXY_ID"),
        "change_ip_url": os.getenv("CHANGE_IP_URL"),
        "ankama_country": os.getenv("ANKAMA_COUNTRY"),
        "proxy_scheme": os.getenv("PROXY_SCHEME", "http"),
        "proxy_host": os.getenv("PROXY_HOST"),
        "proxy_port": os.getenv("PROXY_PORT"),
        "proxy_user": os.getenv("PROXY_USER"),
        "proxy_pass": os.getenv("PROXY_PASS"),
        "headless": bool_from_env("HEADLESS", False),
        "pageload_timeout": int(os.getenv("PAGELOAD_TIMEOUT", "40")),
    }

    missing = [k for k, v in config.items() if v in (None, "")]
    if missing:
        logging.warning("Missing environment variables: %s", ", ".join(missing))

    return config


def build_proxy_url(config: dict) -> str:
    """Return a proxy URL with credentials."""
    auth = ""
    if config.get("proxy_user") and config.get("proxy_pass"):
        auth = f"{config['proxy_user']}:{config['proxy_pass']}@"

    return f"{config['proxy_scheme']}://{auth}{config['proxy_host']}:{config['proxy_port']}"


def renew_hypeproxy_ip(change_ip_url: Optional[str]) -> None:
    """Trigger a HypeProxy IP renewal if the URL is provided."""
    if not change_ip_url:
        logging.info("No CHANGE_IP_URL provided; skipping IP renewal.")
        return

    logging.info("Requesting IP renewal via HypeProxy...")
    try:
        response = requests.get(change_ip_url, timeout=30)
        response.raise_for_status()
        logging.info("HypeProxy response: %s", response.text)
    except requests.RequestException as exc:
        logging.warning("Failed to renew IP: %s", exc)


def fetch_ip_with_requests(proxy_url: str, timeout: int = 30) -> str:
    """Fetch the outbound IP using the proxy via requests."""
    logging.info("Verifying proxy via requests...")
    proxies = {
        "http": proxy_url,
        "https": proxy_url,
    }
    response = requests.get(IP_CHECK_URL, proxies=proxies, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    ip_address = data.get("ip")
    if not ip_address:
        raise RuntimeError("Response did not contain an IP address.")
    logging.info("Proxy reports IP %s via requests.", ip_address)
    return ip_address


def create_driver(proxy_url: str, headless: bool, pageload_timeout: int):
    """Instantiate an undetected Chrome driver using Selenium Wire for proxy auth."""
    options = uc.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    seleniumwire_options = {
        "proxy": {
            "http": proxy_url,
            "https": proxy_url,
            "no_proxy": "localhost,127.0.0.1",
        }
    }

    driver = uc.Chrome(options=options, seleniumwire_options=seleniumwire_options)
    driver.set_page_load_timeout(pageload_timeout)
    return driver


def fetch_ip_with_selenium(driver) -> str:
    """Navigate to an IP-checking endpoint and parse the IP address."""
    driver.get(IP_CHECK_URL)

    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    body_text = driver.find_element(By.TAG_NAME, "body").text
    ip_address = json.loads(body_text).get("ip")

    if not ip_address:
        raise RuntimeError("Could not extract IP address from browser.")

    logging.info("Proxy reports IP %s via Selenium.", ip_address)
    return ip_address


def browse_ip_page(driver, url: str = IP_BROWSER_URL) -> None:
    """Open an additional IP-checking website to visually confirm the proxy."""
    logging.info("Opening browser IP verification page: %s", url)
    driver.get(url)
    time.sleep(5)
    logging.info("Page title: %s", driver.title)


def verify_proxy_iteration(iteration: int, proxy_url: str, config: dict) -> None:
    logging.info("=== Iteration %d/%d ===", iteration, ITERATIONS)
    renew_hypeproxy_ip(config.get("change_ip_url"))
    time.sleep(5)

    expected_ip = fetch_ip_with_requests(proxy_url)
    driver = None
    try:
        driver = create_driver(
            proxy_url,
            headless=config.get("headless", False),
            pageload_timeout=config.get("pageload_timeout", 40),
        )
        observed_ip = fetch_ip_with_selenium(driver)
        browse_ip_page(driver)
        if expected_ip != observed_ip:
            raise RuntimeError(
                f"Proxy verification failed: requests saw {expected_ip}, "
                f"but Selenium saw {observed_ip}."
            )
        logging.info("Proxy verification succeeded for iteration %d.", iteration)
    finally:
        if driver:
            driver.quit()
        time.sleep(2)


def main() -> None:
    if load_dotenv:
        load_dotenv()
    else:
        logging.info(
            "python-dotenv not installed; environment variables will be read from the "
            "current process only."
        )

    config = get_env_config()
    required = ["proxy_host", "proxy_port", "proxy_scheme"]
    missing = [field for field in required if not config.get(field)]
    if missing:
        raise RuntimeError(
            f"Missing required proxy configuration values: {', '.join(missing)}"
        )

    proxy_url = build_proxy_url(config)
    logging.info("Using proxy %s", proxy_url)

    if config.get("ankama_country"):
        logging.info("Configured Ankama country: %s", config["ankama_country"])

    for i in range(1, ITERATIONS + 1):
        verify_proxy_iteration(i, proxy_url, config)

    logging.info("All %d verification iterations completed successfully.", ITERATIONS)


if __name__ == "__main__":
    main()
