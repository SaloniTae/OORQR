from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import random
import sys
import os
import time

# List of passwords
PASSWORDS = [
    "oor@forever", "oor@aurora", "oor@legacy", "oor@popcorn", "oor@elite",
    "oor@bliss", "oor@essence", "oor@timeless", "oor@flix", "oor@verse",
    "oor@storm", "oor@unstoppable", "oor@eternal", "oor@beyond", "oor@awakened",
    "oor@evenmore", "oor@true", "oor@drift", "oor@pulse", "oor@vibe",
    "oor@echo", "oor@dawn", "oor@orbit", "oor@hype", "oor@realm",
    "oor@space", "oor@nova"
]

TEMP_DIR = "crunchy_debug"
os.makedirs(TEMP_DIR, exist_ok=True)

def reset_password_flow(reset_link):
    opts = Options()
    opts.add_argument('--headless')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')

    driver = webdriver.Chrome(options=opts)
    wait = WebDriverWait(driver, 15)

    try:
        print(f"Opening reset link: {reset_link}")
        driver.get(reset_link)

        # Handle cookie banner if present
        try:
            cookie_btn = wait.until(EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler")))
            print("Cookie consent detected. Accepting...")
            cookie_btn.click()
            time.sleep(1)
        except:
            print("No cookie banner detected, continuing...")

        # Choose random password
        new_password = random.choice(PASSWORDS)
        print(f"Chosen new password: {new_password}")

        # Fill in password fields
        wait.until(EC.presence_of_element_located((By.NAME, "newPassword"))).send_keys(new_password)
        wait.until(EC.presence_of_element_located((By.NAME, "retypePassword"))).send_keys(new_password)

        # Click reset button
        btn_el = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-t='reset-password-button']")))
        btn_el.click()
        print("Password reset form submitted.")

        time.sleep(2)

        # Screenshot success page
        driver.save_screenshot(os.path.join(TEMP_DIR, f"reset_done_{int(time.time())}.png"))
        print("Saved screenshot of result.")

    except Exception as e:
        ts = int(time.time())
        driver.save_screenshot(os.path.join(TEMP_DIR, f"reset_error_{ts}.png"))
        with open(os.path.join(TEMP_DIR, f"reset_error_{ts}.html"), "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"Error in reset password flow: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    reset_link = input("Enter your Crunchyroll reset link: ").strip()
    reset_password_flow(reset_link)
