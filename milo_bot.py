import random
import re
import time
import threading
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# --- TELEGRAM CONFIG ---
TELEGRAM_TOKEN = "8982012958:AAEYxhk9rbm7WcLntD41OpxADY7HW08qSDA"
TELEGRAM_CHAT_ID = "7493468196"

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


# --- MAIL.TM ---
MAIL_TM_BASE = "https://api.mail.tm"
PHONE_NUMBER = "07012249321"


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


def report_loop():
    while True:
        time.sleep(600)
        with count_lock:
            d = dismiss_count
            e = entry_count
        pct = (d / e * 100) if e else 0
        send_status(PHONE_NUMBER, d, e)
        send_telegram(f"periodic: {d}/{e} ({pct:.1f}%)")


threading.Thread(target=report_loop, daemon=True).start()
send_telegram("milo_bot started.")


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

    chrome_options = Options()
    # Headless mode (required — GitHub's runner has no display)
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    # Point at the Chrome binary that browser-actions/setup-chrome installs
    chrome_options.binary_location = "/opt/hostedtoolcache/setup-chrome/chromium/stable/x64/chrome"

    driver = webdriver.Chrome(options=chrome_options)
    wait = WebDriverWait(driver, 20)

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
        print(f"Entry #{entry_number} failed: {type(e).__name__}: {e}")

    finally:
        driver.quit()

    with count_lock:
        entry_count += 1

    print("1 entry")
    entry_number += 1
    time.sleep(2)
