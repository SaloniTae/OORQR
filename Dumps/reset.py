#!/usr/bin/env python3
"""
crunchy_reset_cli.py

Usage:
  python3 crunchy_reset_cli.py your-email@example.com
  python3 crunchy_reset_cli.py               # will prompt for email
  python3 crunchy_reset_cli.py -e email -n  # pass flags; -n disables headless mode

Features:
 - Accepts email from CLI or interactive prompt
 - Handles OneTrust cookie banner overlays
 - Minimal safe Chrome options (same flags that worked for you)
 - Saves debug screenshot + HTML in crunchy_debug/ on error
"""
import argparse
import re
import time
import traceback
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import sys, os

URL = "https://sso.crunchyroll.com/reset-password"
DEBUG_DIR = Path("crunchy_debug")
DEBUG_DIR.mkdir(exist_ok=True)

EMAIL_REGEX = re.compile(r"[^@]+@[^@]+\.[^@]+")


def parse_args():
    p = argparse.ArgumentParser(description="Request Crunchyroll password reset (Selenium).")
    p.add_argument("email", nargs="?", help="Email address to request reset for")
    p.add_argument("-n", "--no-headless", action="store_true", help="Run with browser UI (not headless)")
    p.add_argument("-t", "--timeout", type=int, default=20, help="Element wait timeout in seconds (default: 20)")
    return p.parse_args()


def valid_email(addr: str) -> bool:
    return bool(addr and EMAIL_REGEX.fullmatch(addr.strip()))


def make_driver(headless=True):
    opts = Options()
    if headless:
        # use the simple headless flag which worked for you
        opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    # keep options minimal to avoid profile issues
    return webdriver.Chrome(options=opts)


def save_debug(driver, tag="error"):
    ts = int(time.time())
    png = DEBUG_DIR / f"{tag}_{ts}.png"
    html = DEBUG_DIR / f"{tag}_{ts}.html"
    try:
        driver.save_screenshot(str(png))
    except Exception as e:
        print("Failed to save screenshot:", e)
    try:
        with open(html, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception as e:
        print("Failed to save page source:", e)
    print("Saved debug files (if any) at:", DEBUG_DIR.resolve())
    if png.exists():
        print(" Screenshot:", png.resolve())
    if html.exists():
        print(" HTML:     ", html.resolve())


def try_close_cookie_banner(driver):
    """
    Try common OneTrust cookie selectors and remove fallback.
    Returns True if a candidate was clicked/removed.
    """
    candidates = [
        "#onetrust-accept-btn-handler",
        ".onetrust-close-btn-handler",
        "#onetrust-banner-sdk button",
        "div#onetrust-button-group button",
        "button[title='Accept']",
        "button[aria-label='accept']",
    ]
    clicked_any = False
    for sel in candidates:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if not els:
                continue
            # JS click to avoid interception
            try:
                driver.execute_script("arguments[0].click();", els[0])
            except Exception:
                try:
                    els[0].click()
                except Exception:
                    pass
            clicked_any = True
            # give DOM a moment to update
            time.sleep(0.6)
            # try to remove any lingering OneTrust nodes as last resort
            driver.execute_script("""
                try {
                  document.querySelectorAll('[id^="onetrust"], [class*="onetrust"], #onetrust-banner-sdk').forEach(e => e.remove());
                } catch(e) {}
            """)
            time.sleep(0.3)
            break
        except Exception:
            continue
    return clicked_any


def run_flow(email: str, headless: bool, timeout: int):
    driver = None
    try:
        print(f"Starting Chrome (headless={headless}) ...")
        driver = make_driver(headless=headless)
        wait = WebDriverWait(driver, timeout)

        print("Opening:", URL)
        driver.get(URL)

        # Try to dismiss cookie banner early
        try:
            closed = try_close_cookie_banner(driver)
            if closed:
                print("Cookie banner closed/removed (one of the selectors matched).")
            else:
                print("No cookie banner selector matched (or none present).")
        except Exception as e:
            print("Cookie banner handling error:", e)

        # locate email field
        email_sel = (By.CSS_SELECTOR, "input[name='email']")
        email_el = wait.until(EC.visibility_of_element_located(email_sel))
        email_el.clear()
        email_el.send_keys(email)
        print("Email entered:", email)

        # locate send button
        send_sel = (By.CSS_SELECTOR, "button[data-t='reset-password-button']")
        send_btn = wait.until(EC.presence_of_element_located(send_sel))

        # scroll into view
        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", send_btn)
        time.sleep(0.3)

        # attempt normal click, then JS fallback if intercepted
        try:
            wait.until(EC.element_to_be_clickable(send_sel))
            send_btn.click()
            print("Clicked Send (normal click).")
        except Exception as ex_click:
            print("Normal click failed/was intercepted, trying JS click fallback:", ex_click)
            # remove any one-trust nodes again (last resort) then click via JS
            try:
                driver.execute_script("""
                    try {
                      document.querySelectorAll('[id^="onetrust"], [class*="onetrust"], #onetrust-banner-sdk').forEach(e => e.remove());
                    } catch(e) {}
                """)
            except Exception:
                pass
            time.sleep(0.2)
            try:
                driver.execute_script("arguments[0].click();", send_btn)
                print("Clicked Send (JS click).")
            except Exception as ex_js:
                print("JS click also failed:", ex_js)
                raise

        # short wait for any visible confirmation message
        time.sleep(3)
        try:
            msg_el = driver.find_element(By.CSS_SELECTOR, "[role='alert'], [aria-live']")
            txt = msg_el.text.strip() if msg_el.text else "<no text>"
            print("In-page message detected:", txt)
        except Exception:
            print("No explicit success message found in-page. That doesn't mean email wasn't sent.")
            print("Check the inbox (and spam).")

        print("Done — check your inbox for the reset email.")
        return True

    except Exception as exc:
        print("Exception during flow:", exc)
        traceback.print_exc()
        if driver:
            save_debug(driver, tag="error_cli")
        return False

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def main():
    args = parse_args()
    email = args.email
    if not email:
        # interactive prompt
        email = input("Enter email for password reset: ").strip()
    if not valid_email(email):
        print("Invalid email format:", email)
        sys.exit(2)

    ok = run_flow(email=email, headless=(not args.no_headless), timeout=args.timeout)
    if ok:
        sys.exit(0)
    else:
        print("Flow did not report clear success. Check debug files in:", DEBUG_DIR.resolve())
        sys.exit(3)


if __name__ == "__main__":
    main()
