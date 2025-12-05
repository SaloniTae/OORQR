#!/usr/bin/env python3
"""
netflix_newpassword_stealth.py

Selenium+CDP "stealth" script to open a Netflix password-reset link, fill:
 - current-email (email you provide)
 - new-password (password you provide)
 - reeneter-new-password (confirm)
then click Save.

Saves debug screenshots + HTML in netflix_debug/:
 - pre_click_top_*.png/html  (top-of-page before interacting)
 - post_click_top_*.png/html (immediately after clicking Save)
 - final_top_after_wait_*.png/html
 - success_top_*.png/html (if detected)
"""
import argparse, time, random, traceback, os
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --------------- CONFIG ---------------
URL_HINT = "Paste your Netflix reset link (the one you got in email)"
DEBUG_DIR = Path("netflix_debug")
DEBUG_DIR.mkdir(exist_ok=True)
DEFAULT_TIMEOUT = 20
# --------------------------------------

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("link", nargs="?", help="Netflix reset link (optional). If omitted, you'll be prompted.")
    p.add_argument("-n","--no-headless", action="store_true", help="Show browser UI (no headless) - useful to solve CAPTCHAs")
    p.add_argument("-t","--timeout", type=int, default=DEFAULT_TIMEOUT, help="Wait timeout seconds")
    p.add_argument("-s","--success-wait", type=int, default=18, help="Seconds to wait for success after clicking Save")
    return p.parse_args()

# ---------- Stealth & human helpers ----------
def random_sleep(a=0.25, b=0.9):
    time.sleep(random.uniform(a, b))

def human_typing(el, text, min_delay=0.04, max_delay=0.16):
    for ch in text:
        el.send_keys(ch)
        time.sleep(random.uniform(min_delay, max_delay))
    time.sleep(random.uniform(0.08, 0.25))

def save_debug(driver, tag="debug"):
    ts = int(time.time())
    png = DEBUG_DIR / f"{tag}_{ts}.png"
    html = DEBUG_DIR / f"{tag}_{ts}.html"
    try:
        driver.save_screenshot(str(png))
    except Exception as e:
        print("Screenshot failed:", e)
    try:
        with open(html, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception as e:
        print("HTML save failed:", e)
    return png.resolve(), html.resolve()

def apply_cdp_stealth(driver):
    # Small set of stealth tweaks using CDP
    try:
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")
        })
        driver.execute_cdp_cmd('Network.setExtraHTTPHeaders', {"headers": {"Accept-Language": "en-US,en;q=0.9"}})
        js = r"""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
        window.chrome = window.chrome || { runtime: {} };
        """
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": js})
    except Exception as e:
        print("CDP stealth apply failed (continuing):", e)

def try_close_cookie_banner(driver):
    selectors = [
        "#onetrust-accept-btn-handler",
        "div#onetrust-button-group button",
        ".onetrust-close-btn-handler",
        "button[aria-label='Accept']",
        "button[title='Accept']",
    ]
    for sel in selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if not els:
                continue
            try:
                driver.execute_script("arguments[0].click();", els[0])
            except Exception:
                try:
                    els[0].click()
                except Exception:
                    pass
            time.sleep(0.6)
            # remove lingering OneTrust DOM nodes as last resort
            driver.execute_script("""
                try { document.querySelectorAll('[id^="onetrust"], [class*="onetrust"], #onetrust-banner-sdk').forEach(e => e.remove()); } catch(e) {}
            """)
            time.sleep(0.3)
            return True
        except Exception:
            continue
    return False

def move_and_click(driver, element):
    try:
        ac = ActionChains(driver)
        size = element.size
        w = max(2, int(size.get('width', 50) * 0.3))
        h = max(2, int(size.get('height', 20) * 0.3))
        ox = random.randint(1, w)
        oy = random.randint(1, h)
        ac.move_to_element_with_offset(element, ox, oy).pause(random.uniform(0.05,0.25)).click().perform()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
            time.sleep(random.uniform(0.05,0.18))
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception as e:
            print("Click fallback failed:", e)
            return False

def detect_recaptcha(driver):
    try:
        src = (driver.page_source or "").lower()
        if "recaptcha" in src or "g-recaptcha" in src:
            return True
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for fr in frames:
            title = (fr.get_attribute("title") or "").lower()
            src_attr = (fr.get_attribute("src") or "").lower()
            if "recaptcha" in title or "recaptcha" in src_attr or "captcha" in title or "captcha" in src_attr:
                return True
    except Exception:
        pass
    return False

# ----------------- Main flow -----------------
def run_flow(reset_link, email, new_password, headless=True, timeout=DEFAULT_TIMEOUT, success_wait=18):
    driver = None
    try:
        opts = Options()
        if headless:
            opts.add_argument("--headless")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_experimental_option("excludeSwitches", ["enable-automation","enable-logging"])
        opts.add_experimental_option('useAutomationExtension', False)

        driver = webdriver.Chrome(options=opts)
        apply_cdp_stealth(driver)
        wait = WebDriverWait(driver, timeout)

        print("Opening reset link...")
        driver.get(reset_link)
        random_sleep(0.2, 0.8)

        # ensure top-of-page screenshot
        try:
            driver.execute_script("window.scrollTo(0,0);")
        except Exception:
            pass
        pre_top_png, pre_top_html = save_debug(driver, tag="pre_click_top")
        print("Saved top-of-page pre-click:", pre_top_png)

        # try close cookie banner
        try_close_cookie_banner(driver)

        random_sleep(0.2, 0.7)

        # Fill email - the field may be aria-disabled; attempt to remove disabled attribute if present
        email_sel = (By.NAME, "current-email")
        email_el = wait.until(EC.presence_of_element_located(email_sel))
        # Some Netflix reset pages mark the email input disabled (aria-disabled). Enable it if needed.
        try:
            if email_el.get_attribute("disabled") or email_el.get_attribute("aria-disabled") == "true":
                driver.execute_script("arguments[0].removeAttribute('disabled'); arguments[0].removeAttribute('aria-disabled');", email_el)
                random_sleep(0.08,0.18)
        except Exception:
            pass
        # focus & human type
        try:
            move_and_click(driver, email_el)
        except Exception:
            pass
        random_sleep(0.08,0.25)
        try:
            email_el.clear()
        except Exception:
            pass
        human_typing(email_el, email, min_delay=0.03, max_delay=0.12)
        print("Entered email.")

        random_sleep(0.3, 0.9)

        # Fill new password and confirm
        newpass_sel = (By.NAME, "new-password")
        confirm_sel = (By.NAME, "reeneter-new-password")
        new_el = wait.until(EC.visibility_of_element_located(newpass_sel))
        conf_el = wait.until(EC.visibility_of_element_located(confirm_sel))
        move_and_click(driver, new_el)
        random_sleep(0.08,0.25)
        try:
            new_el.clear()
        except Exception:
            pass
        human_typing(new_el, new_password, min_delay=0.03, max_delay=0.12)

        random_sleep(0.18,0.45)
        move_and_click(driver, conf_el)
        random_sleep(0.08,0.2)
        try:
            conf_el.clear()
        except Exception:
            pass
        human_typing(conf_el, new_password, min_delay=0.03, max_delay=0.12)
        print("Entered new password and confirmation.")

        random_sleep(0.4, 1.1)

        # Save (click Save button)
        save_btn_sel = (By.CSS_SELECTOR, "button[data-uia='change-password-form+save-button']")
        save_btn = wait.until(EC.presence_of_element_located(save_btn_sel))
        # scroll top then ensure click from top area
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", save_btn)
        random_sleep(0.12,0.35)
        moved = move_and_click(driver, save_btn)
        print("Clicked Save (moved click):", moved)

        # immediate post-click debug (top)
        post_png, post_html = save_debug(driver, tag="post_click_top")
        print("Saved immediate post-click:", post_png)

        # wait for success element or changes
        print(f"Waiting up to {success_wait}s for a success indicator ...")
        try:
            succ_wait = WebDriverWait(driver, success_wait)
            # common success indicator on Netflix after password change is a header or redirect - try the known selector if exists
            succ_el = succ_wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "h1[data-uia='email-sent-title'], .change-password-success, div[data-uia='password-change-success']")))
            print("Detected success element.")
            success_png, success_html = save_debug(driver, tag="success_top")
            print("Saved success debug:", success_png)
            return {"status":"sent","success":success_png,"pre":pre_top_png,"post":post_png}
        except Exception as ex_wait:
            # Not found - save final snapshot and check for captcha or error text
            print("Success element not found within wait:", ex_wait)
            final_png, final_html = save_debug(driver, tag="final_top_after_wait")
            if detect_recaptcha(driver):
                print("Detected reCAPTCHA on the page. Please re-run with --no-headless and solve it manually.")
                return {"status":"captcha","pre":pre_top_png,"post":post_png,"final":final_png}
            # check for textual indicators of success or failure
            src = (driver.page_source or "").lower()
            if "password changed" in src or "password updated" in src or "success" in src:
                s_png, s_html = save_debug(driver, tag="success_keyword_top")
                return {"status":"sent_by_keyword","pre":pre_top_png,"post":post_png,"final":s_png}
            # unknown
            return {"status":"unknown","pre":pre_top_png,"post":post_png,"final":final_png}

    except Exception as e:
        print("Exception during run:", e)
        traceback.print_exc()
        if driver:
            png, html = save_debug(driver, tag="fatal_top")
            print("Saved fatal debug:", png)
        return {"status":"exception","error":str(e)}
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

# ---------------- Entry point ----------------
if __name__ == "__main__":
    args = parse_args()
    reset_link = args.link or input("Paste Netflix reset link: ").strip()
    email = input("Email to fill into form: ").strip()
    new_password = input("New password to set: ").strip()

    if not reset_link or not reset_link.lower().startswith("http"):
        print("Invalid reset link. Exiting.")
        raise SystemExit(2)
    if not email:
        print("No email provided. Exiting.")
        raise SystemExit(2)
    if not new_password:
        print("No password provided. Exiting.")
        raise SystemExit(2)

    print("Running in headless mode =", not args.no_headless)
    result = run_flow(reset_link, email, new_password, headless=(not args.no_headless),
                      timeout=args.timeout, success_wait=args.success_wait)
    print("RESULT:", result)
    print("\nSaved debug files in:", DEBUG_DIR.resolve())
    for f in sorted(DEBUG_DIR.iterdir()):
        print(" -", f.name)
    if result.get("status") == "captcha":
        print("\nDetected CAPTCHA. Run with --no-headless to solve it in the opened browser, then re-run or press Enter in terminal as needed.")
