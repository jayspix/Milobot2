import random
import re
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# --- TELEGRAM CONFIG ---
TELEGRAM_TOKEN = "8982012958:AAEYxhk9rbm7WcLntD41OpxADY7HW08qSDA"
TELEGRAM_CHAT_ID = "7493468196"

# --- PROXY CONFIG ---
# Format: "IP:PORT" or "IP:PORT|type" (http, socks4, socks5)
PROXY_LIST = [
79.127.149.144:3 | https
41.184.92.221:80 | http
197.156.240.66:5678 | socks4
105.235.201.234:32639 | socks4
45.222.101.11:8080 | http
41.216.169.82:5678 | socks4
196.1.182.158:1080 | socks4
154.113.147.1:4080 | http
41.203.83.242:8080 | http
196.1.182.130:1080 | socks4
41.184.92.220:80 | http
154.113.209.162:8082 | http
102.90.0.149:8080 | http
102.212.104.129:8082 | http
41.75.84.86:4153 | socks4
102.69.146.59:7080 | socks5
188.215.31.189:10808 | socks5
155.93.96.21:8080 | http
102.220.189.201:8080 | http
154.113.196.0:5678 | socks4
79.127.149.194:443 | https,
]

PROXY_TEST_URL = "https://httpbin.org/ip"
PROXY_TIMEOUT = 7
PROXY_MAX_THREADS = 20

active_proxy = None
proxy_lock = threading.Lock()

# --- COUNTERS ---
dismiss_count = 0
entry_count = 0
count_lock = threading.Lock()

last_status_message_id = None
status_lock = threading.Lock()


def send_telegram(message, parse_mode=None):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"Telegram send failed: {data}")
        return None
    except Exception as e:
        print(f"Telegram send failed: {type(e).__name__}: {e}")
        return None


def delete_telegram_message(message_id):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram delete failed: {type(e).__name__}: {e}")


def send_status(phone_number, dismisses, entries):
    global last_status_message_id
    with status_lock:
        if last_status_message_id is not None:
            delete_telegram_message(last_status_message_id)
            last_status_message_id = None
        text = (
            f"<b>{phone_number}</b>\n"
            f"Dismiss clicks: {dismisses}\n"
            f"Entries: {entries}"
        )
        new_id = send_telegram(text, parse_mode="HTML")
        if new_id:
            last_status_message_id = new_id


# --- PROXY TESTING ---
def parse_proxy_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None, None
    if "|" in line:
        parts = line.split("|")
        addr = parts[0].strip()
        ptype = parts[1].strip().lower()
    else:
        addr = line
        ptype = "http"
    return addr, ptype


def test_proxy(proxy_info):
    addr, ptype = proxy_info
    scheme_map = {
        "http": "http",
        "https": "http",
        "socks4": "socks4",
        "socks5": "socks5",
    }
    scheme = scheme_map.get(ptype, "http")
    proxy_url = f"{scheme}://{addr}"
    proxies = {"http": proxy_url, "https": proxy_url}

    start = time.time()
    try:
        r = requests.get(PROXY_TEST_URL, proxies=proxies, timeout=PROXY_TIMEOUT)
        if r.status_code == 200:
            latency = round(time.time() - start, 2)
            external_ip = r.json().get("origin", "unknown")
            return {
                "addr": addr,
                "type": ptype,
                "scheme": scheme,
                "status": "WORKING",
                "latency": latency,
                "external_ip": external_ip,
            }
    except Exception:
        pass

    return {
        "addr": addr,
        "type": ptype,
        "scheme": scheme,
        "status": "FAILED",
        "latency": None,
        "external_ip": None,
    }


def test_and_pick_proxy():
    if not PROXY_LIST:
        print("[*] No proxies configured — running without a proxy.")
        return None

    print(f"[*] Testing {len(PROXY_LIST)} proxies...")
    tasks = [parse_proxy_line(line) for line in PROXY_LIST]
    tasks = [t for t in tasks if t[0]]

    working = []
    with ThreadPoolExecutor(max_workers=PROXY_MAX_THREADS) as ex:
        futures = {ex.submit(test_proxy, t): t for t in tasks}
        for fut in as_completed(futures):
            res = fut.result()
            if res["status"] == "WORKING":
                working.append(res)
                print(
                    f"  [OK] {res['addr']:<22} {res['latency']}s "
                    f"(exit IP: {res['external_ip']})"
                )
            else:
                print(f"  [--] {res['addr']:<22} failed")

    if not working:
        print("[!] No working proxies found.")
        send_telegram("milo_bot: no working proxies found")
        return None

    best = min(working, key=lambda p: p["latency"])
    print(
        f"[*] Best proxy: {best['addr']} ({best['type']}, "
        f"{best['latency']}s, exit {best['external_ip']})"
    )
    send_telegram(
        f"proxy: {best['addr']} ({best['type']}, {best['latency']}s, "
        f"exit {best['external_ip']})"
    )
    return best


def refresh_proxy():
    global active_proxy
    best = test_and_pick_proxy()
    with proxy_lock:
        active_proxy = best
    return best


# --- MAIL.TM ---
MAIL_TM_BASE = "https://api.mail.tm"
PHONE_NUMBER = "07011229862"


def create_mail_tm_account():
    resp = requests.get(f"{MAIL_TM_BASE}/domains")
    domain = resp.json()["hydra:member"][0]["domain"]

    username = f"user_{random.randint(10000, 99999)}"
    email = f"{username}@{domain}"
    password = "SecurePassword123!"

    payload = {"address": email, "password": password}
    headers = {"Content-Type": "application/json"}
    requests.post(f"{MAIL_TM_BASE}/accounts", json=payload, headers=headers)

    token_resp = requests.post(f"{MAIL_TM_BASE}/token", json=payload, headers=headers)
    token = token_resp.json()["token"]

    print(f"Generated temporary email: {email}")
    return email, token


def fetch_otp_from_mail_tm(token):
    print("Waiting for OTP email to arrive via mail.tm...")
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(20):
        time.sleep(3)
        msg_resp = requests.get(f"{MAIL_TM_BASE}/messages", headers=headers)
        messages = msg_resp.json().get("hydra:member", [])
        if messages:
            msg_id = messages[0]["id"]
            full_msg = requests.get(
                f"{MAIL_TM_BASE}/messages/{msg_id}", headers=headers
            ).json()
            text_content = full_msg.get("text", "") or full_msg.get("intro", "")
            print("Email content retrieved successfully.")
            digits = re.findall(r"\b\d{4,6}\b", text_content)
            if digits:
                otp = digits[0]
                print(f"Extracted OTP: {otp}")
                return otp
    raise Exception("Timeout: OTP email did not arrive in time.")


def try_click_dismiss(driver, timeout=5):
    global dismiss_count
    try:
        el = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable(
                (By.XPATH, "/html/body/div[4]/div/button")
            )
        )
        try:
            el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", el)
        print("Dismiss button clicked.")
        with count_lock:
            dismiss_count += 1
            d = dismiss_count
            e = entry_count
        send_status(PHONE_NUMBER, d, e)
        return True
    except Exception:
        print("Dismiss button not present — skipping.")
        return False


send_telegram("milo_bot started.")

# Pick the best proxy once at startup
refresh_proxy()


# --- MAIN LOOP ---
entry_number = 1
while True:
    print(f"\n=== Starting entry #{entry_number} ===")

    try:
        temp_email, mail_token = create_mail_tm_account()
    except Exception as e:
        print(f"Could not create temp email: {type(e).__name__}: {e}")
        time.sleep(5)
        entry_number += 1
        continue

    with proxy_lock:
        current_proxy = active_proxy

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.binary_location = (
        "/opt/hostedtoolcache/setup-chrome/chromium/stable/x64/chrome"
    )

    if current_proxy:
        chrome_options.add_argument(
            f"--proxy-server={current_proxy['scheme']}://{current_proxy['addr']}"
        )

    driver = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(driver, 20)

    proxy_failed = False
    try:
        driver.get("https://milotextandwinpromo.com.ng/")

        phone_input = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[1]/div/input")
            )
        )
        phone_input.send_keys(PHONE_NUMBER)

        email_input = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[2]/div/input"
        )
        email_input.send_keys(temp_email)

        proceed_btn_1 = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/button"
        )
        driver.execute_script("arguments[0].click();", proceed_btn_1)

        otp_code = fetch_otp_from_mail_tm(mail_token)

        otp_field = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "/html/body/div[4]/div/div/form/div/div/div/input[1]")
            )
        )
        otp_field.send_keys(otp_code)

        proceed_btn_2 = driver.find_element(
            By.XPATH, "/html/body/div[4]/div/div/form/button"
        )
        proceed_btn_2.click()

        first_name_input = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[1]/div[1]/div/input")
            )
        )
        first_name_input.send_keys("david")

        last_name_input = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[1]/div[2]/div/input"
        )
        last_name_input.send_keys("danjuma")

        dropdown_btn = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[2]/button/div"
        )
        dropdown_btn.click()

        state_option = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "/html/body/div[3]/div/div/div/div/div/div/div[1]/div")
            )
        )
        state_option.click()

        random_9_digit = str(random.randint(100000000, 999999987))
        promo_input = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[3]/div/input"
        )
        promo_input.send_keys(random_9_digit)
        print(f"Generated and entered promo code: {random_9_digit}")

        enter_promo_btn = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/button"
        )
        driver.execute_script("arguments[0].click();", enter_promo_btn)
        print("Automation sequence completed successfully!")

        time.sleep(3)
        try_click_dismiss(driver)

    except Exception as e:
        msg = str(e).lower()
        if any(k in msg for k in ("proxy", "net::err", "err_proxy", "err_tunnel")):
            proxy_failed = True
        print(f"Entry #{entry_number} failed: {type(e).__name__}: {e}")

    finally:
        driver.quit()

    if proxy_failed:
        print("[*] Proxy appears dead — re-testing all proxies...")
        refresh_proxy()

    with count_lock:
        entry_count += 1

    print("1 entry")
    entry_number += 1
    time.sleep(2)
