#!/usr/bin/env python3
"""
netflix_stealth.py

Stealthy Netflix reset requester (improved human-like behavior + CDP stealth).
Saves debug screenshots and HTML (top-of-page screenshot + others).

Usage:
  python3 netflix_stealth.py                 # will prompt for email
  python3 netflix_stealth.py you@example.com --no-headless

Notes:
 - If CAPTCHA appears, run with --no-headless to solve manually.
 - These techniques reduce automation footprint but cannot guarantee bypassing bot protections.
"""

import argparse, time, random, traceback, os
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ---------- CONFIG ----------
URL = "https://www.netflix.com/in/LoginHelp"
DEBUG_DIR = Path("netflix_debug")
DEBUG_DIR.mkdir(exist_ok=True)
DEFAULT_TIMEOUT = 20

# A reasonable human-like user agent for desktop Chrome (change if desired)
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")

# ---------- Helpers ----------
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("email", nargs="?", help="Email address for reset")
    p.add_argument("-n", "--no-headless", action="store_true", help="Show browser UI (no headless) — useful to solve CAPTCHAs")
    p.add_argument("-t", "--timeout", type=int, default=DEFAULT_TIMEOUT, help="Element wait timeout (seconds)")
    return p.parse_args()

def random_sleep(a=0.35, b=1.1):
    """Human-like small randomized pause."""
    s = random.uniform(a, b)
    time.sleep(s)

def human_typing(el, text, min_delay=0.04, max_delay=0.16):
    """Type into element one character at a time with random delay."""
    for ch in text:
        el.send_keys(ch)
        time.sleep(random.uniform(min_delay, max_delay))
    # tiny pause after typing
    time.sleep(random.uniform(0.12, 0.35))

def save_debug(driver, tag="debug"):
    ts = int(time.time())
    png = DEBUG_DIR / f"{tag}_{ts}.png"
    html = DEBUG_DIR / f"{tag}_{ts}.html"
    try:
        driver.save_screenshot(str(png))
    except Exception as e:
        print("Screenshot save failed:", e)
    try:
        with open(html, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception as e:
        print("HTML save failed:", e)
    return png.resolve(), html.resolve()

def apply_cdp_stealth(driver, user_agent=DEFAULT_UA):
    """
    Apply several stealth patches using CDP:
     - set user-agent
     - set Accept-Language header
     - inject JS to override navigator.webdriver, plugins, languages, chrome runtime, etc.
    """
    try:
        # set UA
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {"userAgent": user_agent})
        # set Accept-Language header
        driver.execute_cdp_cmd('Network.setExtraHTTPHeaders', {"headers": {"Accept-Language": "en-US,en;q=0.9"}})

        # JS to run on every new document to mask automation properties
        js = r"""
        // Pass the Webdriver test
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        // Pass the plugins length test
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        // Pass the languages test
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
        // Provide window.chrome
        window.chrome = window.chrome || { runtime: {} };
        // Mock permissions query for notifications
        const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (originalQuery) {
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
            );
        }
        """
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": js})
    except Exception as e:
        print("CDP stealth apply failed:", e)

def try_close_cookie_banner(driver):
    """Try to click common cookie selectors and remove OneTrust nodes as fallback."""
    selectors = [
        "#onetrust-accept-btn-handler",
        "div#onetrust-button-group button",
        ".onetrust-close-btn-handler",
        "button[aria-label='Accept']",
        "button[title='Accept']",
        "button.cookie-consent-accept"
    ]
    took = False
    for sel in selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if not els:
                continue
            try:
                # JS click to avoid interception
                driver.execute_script("arguments[0].click();", els[0])
            except:
                try:
                    els[0].click()
                except:
                    pass
            took = True
            time.sleep(0.6)
            # remove lingering onetrust dom nodes
            driver.execute_script("""
                try {
                    document.querySelectorAll('[id^="onetrust"], [class*="onetrust"], #onetrust-banner-sdk, .onetrust-pc-dark-filter').forEach(e => e.remove());
                } catch(e) {}
            """)
            time.sleep(0.25)
            break
        except Exception:
            continue
    return took

def move_and_click(driver, element):
    """Move mouse in a human-like manner and click using ActionChains; fallback to JS click."""
    try:
        ac = ActionChains(driver)
        # random offset inside element
        size = element.size
        w = max(2, int(size['width'] * 0.2))
        h = max(2, int(size['height'] * 0.2))
        offset_x = random.randint(0, max(1, w))
        offset_y = random.randint(0, max(1, h))
        # perform chain
        ac.move_to_element_with_offset(element, offset_x, offset_y).pause(random.uniform(0.05, 0.25)).click().perform()
        return True
    except Exception as e:
        # fallback: try JS click
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
            time.sleep(random.uniform(0.08,0.25))
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            print("move_and_click fallback failed:", e)
            return False

def detect_recaptcha(driver):
    try:
        src = driver.page_source.lower()
        if "recaptcha" in src or "g-recaptcha" in src:
            return True
        if driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']"):
            return True
    except Exception:
        pass
    return False

# ---------- Main flow ----------
def run(email, headless=True, timeout=DEFAULT_TIMEOUT, success_wait=20):
    driver = None
    try:
        opts = Options()
        if headless:
            # use simple headless; some servers flag --headless=new — choose what works
            opts.add_argument("--headless")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        # make automation less obvious
        opts.add_experimental_option("excludeSwitches", ["enable-automation","enable-logging"])
        opts.add_experimental_option('useAutomationExtension', False)

        driver = webdriver.Chrome(options=opts)
        # apply CDP stealth overrides
        apply_cdp_stealth(driver)

        wait = WebDriverWait(driver, timeout)

        print("Opening:", URL)
        driver.get(URL)
        random_sleep(0.3, 0.9)

        # TOP-OF-PAGE screenshot: ensure we are at top and then save
        try:
            driver.execute_script("window.scrollTo(0,0);")
        except Exception:
            pass
        pre_top_png, pre_top_html = save_debug(driver, tag="pre_click_top")
        print("Saved TOP-OF-PAGE screenshot:", pre_top_png)

        # Try close cookie banner if present
        try:
            closed = try_close_cookie_banner(driver)
            if closed:
                print("Tried to close cookie/privacy banner.")
        except Exception as e:
            print("Cookie close error (continuing):", e)

        # Wait a little like a human
        random_sleep(0.25, 0.8)

        # Fill email with human typing
        email_sel = (By.CSS_SELECTOR, "input[data-uia='email']")
        email_el = wait.until(EC.visibility_of_element_located(email_sel))
        # click to focus
        move_and_click(driver, email_el)
        random_sleep(0.12, 0.35)
        # clear and type
        try:
            email_el.clear()
        except Exception:
            pass
        human_typing(email_el, email, min_delay=0.03, max_delay=0.14)

        random_sleep(0.4, 1.2)

        # Locate emailMe button
        btn_sel = (By.CSS_SELECTOR, "button[data-uia='emailMeButton']")
        btn_el = wait.until(EC.presence_of_element_located(btn_sel))
        # Move mouse near it and click
        moved = move_and_click(driver, btn_el)
        print("Clicked 'Email Me' (human-like):", moved)

        # immediate post-click debug
        post_png, post_html = save_debug(driver, tag="post_click_top")
        print("Saved immediate post-click debug:", post_png)

        # Wait for success element (Email Sent) up to success_wait
        print(f"Waiting up to {success_wait}s for success element...")
        try:
            succ_waiter = WebDriverWait(driver, success_wait)
            succ_el = succ_waiter.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "h1[data-uia='email-sent-title']")))
            print("Success element detected.")
            success_png, success_html = save_debug(driver, tag="success_top")
            print("Saved success debug:", success_png)
            return {"status":"sent","pre_top":pre_top_png,"post":post_png,"success":success_png}
        except Exception as ex:
            print("Success element not found within wait:", ex)
            # Save a final screenshot (top-of-page again) and return why
            try:
                driver.execute_script("window.scrollTo(0,0);")
            except:
                pass
            final_png, final_html = save_debug(driver, tag="final_top_after_wait")
            if detect_recaptcha(driver):
                print("CAPTCHA detected. Automation cannot bypass it.")
                return {"status":"captcha","pre_top":pre_top_png,"post":post_png,"final":final_png}
            # if page source contains "we've sent" etc, return sent_by_keyword
            page_src = driver.page_source.lower()
            for kw in ["we've sent", "we have sent", "email sent", "check your email", "sent you an email"]:
                if kw in page_src:
                    print("Found success keyword:", kw)
                    s_png, s_html = save_debug(driver, tag="sent_keyword_top")
                    return {"status":"sent_by_keyword","keyword":kw,"pre_top":pre_top_png,"post":post_png,"sent":s_png}
            return {"status":"unknown","pre_top":pre_top_png,"post":post_png,"final":final_png}

    except Exception as e:
        print("Exception:", e)
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

# ---------- Entry ----------
if __name__ == "__main__":
    args = parse_args()
    email = args.email
    if not email:
        email = input("Email for Netflix reset: ").strip()
    if not email:
        print("No email given. Exiting.")
        raise SystemExit(2)

    result = run(email=email, headless=(not args.no_headless), timeout=args.timeout, success_wait=18)
    print("RESULT:", result)
    print("\nSaved debug files in:", DEBUG_DIR.resolve())
    for f in sorted(DEBUG_DIR.iterdir()):
        print(" -", f.name)
    print("\nIf a CAPTCHA appears, re-run with --no-headless to solve it manually (then the script will detect success).")
