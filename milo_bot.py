import random
import re
import time
import threading
import traceback
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
# Hardcoded working proxy. Set to None to run without a proxy.
PROXY = {"addr": "105.235.201.234:32639", "scheme": "socks4", "type": "socks4"}
# PROXY = None

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


def log(msg):
    """Print to stdout AND send to Telegram."""
    print(msg)
    send_telegram(msg)


def log_error(context, exc):
    """Send the error + last part of the traceback to Telegram."""
    tb = traceback.format_exc()
    msg = f"ERROR in {context}\n{type(exc).__name__}: {exc}\n\n{tb[-1500:]}"
    print(msg)
    send_telegram(msg)


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


# --- MAIL.TM ---
MAIL_TM_BASE = "https://api.mail.tm"
PHONE_NUMBER = "07011229862"


def create_mail_tm_account():
    log("[mail.tm] Requesting domain list...")
    resp = requests.get(f"{MAIL_TM_BASE}/domains")
    domain = resp.json()["hydra:member"][0]["domain"]
    log(f"[mail.tm] Domain: {domain}")

    username = f"user_{random.randint(10000, 99999)}"
    email = f"{username}@{domain}"
    password = "SecurePassword123!"

    payload = {"address": email, "password": password}
    headers = {"Content-Type": "application/json"}
    log(f"[mail.tm] Creating {email} ...")
    requests.post(f"{MAIL_TM_BASE}/accounts", json=payload, headers=headers)

    log("[mail.tm] Requesting token...")
    token_resp = requests.post(f"{MAIL_TM_BASE}/token", json=payload, headers=headers)
    token = token_resp.json()["token"]

    log(f"[mail.tm] Ready: {email}")
    return email, token


def fetch_otp_from_mail_tm(token):
    log("[OTP] Waiting for OTP email...")
    headers = {"Authorization": f"Bearer {token}"}
    for i in range(20):
        time.sleep(3)
        msg_resp = requests.get(f"{MAIL_TM_BASE}/messages", headers=headers)
        messages = msg_resp.json().get("hydra:member", [])
        if messages:
            msg_id = messages[0]["id"]
            full_msg = requests.get(
                f"{MAIL_TM_BASE}/messages/{msg_id}", headers=headers
            ).json()
            text_content = full_msg.get("text", "") or full_msg.get("intro", "")
            digits = re.findall(r"\b\d{4,6}\b", text_content)
            if digits:
                otp = digits[0]
                log(f"[OTP] Extracted: {otp}")
                return otp
        if i % 5 == 0:
            log(f"[OTP] Still waiting... ({i * 3}s)")
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
        log("[Dismiss] Clicked.")
        with count_lock:
            dismiss_count += 1
            d = dismiss_count
            e = entry_count
        send_status(PHONE_NUMBER, d, e)
        return True
    except Exception:
        log("[Dismiss] Button not present — skipping.")
        return False


log("milo_bot started.")
if PROXY:
    log(f"[proxy] Using {PROXY['scheme']}://{PROXY['addr']}")
else:
    log("[proxy] Running without a proxy")


# --- MAIN LOOP ---
entry_number = 1
while True:
    log(f"=== Starting entry #{entry_number} ===")

    try:
        temp_email, mail_token = create_mail_tm_account()
    except Exception as e:
        log_error("create_mail_tm_account", e)
        time.sleep(5)
        entry_number += 1
        continue

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.binary_location = (
        "/opt/hostedtoolcache/setup-chrome/chromium/stable/x64/chrome"
    )
    if PROXY:
        chrome_options.add_argument(
            f"--proxy-server={PROXY['scheme']}://{PROXY['addr']}"
        )

    log("[chrome] Launching browser...")
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(30)
    except Exception as e:
        log_error("webdriver.Chrome launch", e)
        entry_number += 1
        continue

    wait = WebDriverWait(driver, 20)

    try:
        log("[nav] Opening target URL...")
        driver.get("https://milotextandwinpromo.com.ng/")
        log(f"[nav] Title: {driver.title}")
        log(f"[nav] URL: {driver.current_url}")

        log("[form] Filling phone...")
        phone_input = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[1]/div/input")
            )
        )
        phone_input.send_keys(PHONE_NUMBER)

        log("[form] Filling email...")
        email_input = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[2]/div/input"
        )
        email_input.send_keys(temp_email)

        log("[form] Clicking proceed (step 1)...")
        proceed_btn_1 = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/button"
        )
        driver.execute_script("arguments[0].click();", proceed_btn_1)

        otp_code = fetch_otp_from_mail_tm(mail_token)

        log("[form] Entering OTP...")
        otp_field = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "/html/body/div[4]/div/div/form/div/div/div/input[1]")
            )
        )
        otp_field.send_keys(otp_code)

        log("[form] Clicking proceed (OTP modal)...")
        proceed_btn_2 = driver.find_element(
            By.XPATH, "/html/body/div[4]/div/div/form/button"
        )
        proceed_btn_2.click()

        log("[form] Filling first name...")
        first_name_input = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[1]/div[1]/div/input")
            )
        )
        first_name_input.send_keys("david")

        log("[form] Filling last name...")
        last_name_input = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[1]/div[2]/div/input"
        )
        last_name_input.send_keys("danjuma")

        log("[form] Opening state dropdown...")
        dropdown_btn = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[2]/button/div"
        )
        dropdown_btn.click()

        log("[form] Selecting state...")
        state_option = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "/html/body/div[3]/div/div/div/div/div/div/div[1]/div")
            )
        )
        state_option.click()

        random_9_digit = str(random.randint(100000000, 999999987))
        log(f"[form] Promo code: {random_9_digit}")
        promo_input = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/div[3]/div/input"
        )
        promo_input.send_keys(random_9_digit)

        log("[form] Clicking enter promo...")
        enter_promo_btn = driver.find_element(
            By.XPATH, "/html/body/main/div/div[2]/div[2]/form/button"
        )
        driver.execute_script("arguments[0].click();", enter_promo_btn)
        log("[form] Promo submitted.")

        time.sleep(3)
        try_click_dismiss(driver)

    except Exception as e:
        log_error(f"entry #{entry_number} flow", e)

    finally:
        driver.quit()

    with count_lock:
        entry_count += 1

    log(f"[done] Entry #{entry_number} finished. Total entries: {entry_count}")
    entry_number += 1
    time.sleep(2)
