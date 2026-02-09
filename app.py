import os
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

app = Flask(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TARGET_URL = "https://waitwhile.com/locations/torreypinesgolf/"
TORREY_PINES_LAT = 32.9005
TORREY_PINES_LNG = -117.2453
PACIFIC_TZ = pytz.timezone("America/Los_Angeles")

# In-memory storage for entries and job status
entries = {}       # id -> entry dict
job_log = []       # list of {id, timestamp, status, message}

scheduler = BackgroundScheduler(timezone=PACIFIC_TZ)
scheduler.start()


# ─── SELENIUM AUTOMATION ──────────────────────────────────────────────────────
def run_automation(entry_id):
    """Run the waitlist submission for a single entry."""
    entry = entries.get(entry_id)
    if not entry:
        log_event(entry_id, "error", "Entry not found")
        return

    log_event(entry_id, "started", "Automation started")
    entries[entry_id]["status"] = "running"

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.action_chains import ActionChains

    # Auto-install matching chromedriver if not found
    try:
        import chromedriver_autoinstaller
        chromedriver_autoinstaller.install()
    except Exception:
        pass  # Fall back to system chromedriver

    driver = None
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(30)

        # ── Spoof geolocation to Torrey Pines parking lot ──
        driver.execute_cdp_cmd("Emulation.setGeolocationOverride", {
            "latitude": TORREY_PINES_LAT,
            "longitude": TORREY_PINES_LNG,
            "accuracy": 1
        })
        # Grant geolocation permission
        driver.execute_cdp_cmd("Browser.grantPermissions", {
            "permissions": ["geolocation"],
            "origin": "https://waitwhile.com"
        })
        log_event(entry_id, "info", f"Geolocation set to Torrey Pines ({TORREY_PINES_LAT}, {TORREY_PINES_LNG})")

        wait = WebDriverWait(driver, 20)

        # ══════════════════════════════════════════════════════════════════════
        # STEP 1: Navigate to welcome page and click "Join waitlist"
        # ══════════════════════════════════════════════════════════════════════
        WELCOME_URL = "https://waitwhile.com/locations/torreypinesgolf/welcome?registration=waitlist"
        DETAILS_URL = "https://waitwhile.com/locations/torreypinesgolf/details?registration=waitlist"

        log_event(entry_id, "info", f"Navigating to welcome page...")
        driver.get(WELCOME_URL)

        # Click "Join waitlist" button
        join_btn = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(text(), 'Join waitlist')]")
        ))
        log_event(entry_id, "info", "Found 'Join waitlist' button, clicking...")
        join_btn.click()

        # ══════════════════════════════════════════════════════════════════════
        # STEP 2: Wait for details form to load
        # ══════════════════════════════════════════════════════════════════════
        first_name_field = wait.until(EC.presence_of_element_located(
            (By.ID, "form_firstName")
        ))
        log_event(entry_id, "info", "Form loaded — filling fields...")

        # ══════════════════════════════════════════════════════════════════════
        # STEP 3: Fill in the form fields AS FAST AS POSSIBLE
        # ══════════════════════════════════════════════════════════════════════

        # First Name (required)
        first_name_field.clear()
        first_name_field.send_keys(entry["first_name"])

        # Last Name (required)
        last_name_field = driver.find_element(By.ID, "form_lastName")
        last_name_field.clear()
        last_name_field.send_keys(entry["last_name"])

        # Phone (optional but we fill it)
        phone_field = driver.find_element(By.ID, "form_phone")
        phone_field.clear()
        phone_field.send_keys(entry["phone"])

        # Email (optional but we fill it)
        email_field = driver.find_element(By.ID, "form_email")
        email_field.clear()
        email_field.send_keys(entry["email"])

        log_event(entry_id, "info", "Text fields filled, setting course...")

        # ── Course (react-select dropdown) ──
        # Map our values to the exact text options on the form
        course_map = {
            "first_available": "First Avail.",
            "north": "North",
            "south": "South",
        }
        course_text = course_map.get(entry["course"], "First Avail.")

        # Click the course dropdown to open it
        course_container = driver.find_element(
            By.CSS_SELECTOR, "[name='form_pwopKQXa6Wv8dHY4S8ja']"
        ).find_element(By.XPATH, "./..")
        course_input = course_container.find_element(By.CSS_SELECTOR, "input[role='combobox']")
        course_input.click()
        time.sleep(0.3)

        # Type the course name to filter, then select
        course_input.send_keys(course_text)
        time.sleep(0.3)
        course_input.send_keys(Keys.ENTER)

        log_event(entry_id, "info", f"Course set to: {course_text}")

        # ── Players (react-select dropdown) ──
        players_val = str(entry["players"])

        players_container = driver.find_element(
            By.CSS_SELECTOR, "[name='form_FMJU7hEVVDPzkHX4OOot']"
        ).find_element(By.XPATH, "./..")
        players_input = players_container.find_element(By.CSS_SELECTOR, "input[role='combobox']")
        players_input.click()
        time.sleep(0.3)

        players_input.send_keys(players_val)
        time.sleep(0.3)
        players_input.send_keys(Keys.ENTER)

        log_event(entry_id, "info", f"Players set to: {players_val}")

        # ══════════════════════════════════════════════════════════════════════
        # STEP 4: Submit the form
        # ══════════════════════════════════════════════════════════════════════
        log_event(entry_id, "info", "All fields filled — submitting form...")

        submit_btn = wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(text(), 'Join the line')]")
        ))
        submit_btn.click()

        log_event(entry_id, "info", "Submit button clicked! Waiting for confirmation...")

        # Wait a moment for the submission to process
        time.sleep(3)

        # Check if we got redirected or got a success message
        current_url = driver.current_url
        page_text = driver.find_element(By.TAG_NAME, "body").text

        if "confirm" in page_text.lower() or "success" in page_text.lower() or "you" in page_text.lower()[:200]:
            log_event(entry_id, "success", f"✅ SUBMITTED SUCCESSFULLY for {entry['first_name']} {entry['last_name']}!")
            entries[entry_id]["status"] = "completed"
        elif "details" not in current_url:
            # We navigated away from the form page — likely success
            log_event(entry_id, "success", f"✅ Form submitted — redirected to: {current_url}")
            entries[entry_id]["status"] = "completed"
        else:
            # Still on form page — might have an error
            log_event(entry_id, "warning", f"Submit clicked but still on form page. Page text: {page_text[:200]}")
            entries[entry_id]["status"] = "unknown"

    except Exception as e:
        log_event(entry_id, "error", f"Automation failed: {str(e)}")
        entries[entry_id]["status"] = "failed"
        # Try to capture a screenshot for debugging
        if driver:
            try:
                screenshot_path = f"/tmp/error_{entry_id[:8]}.png"
                driver.save_screenshot(screenshot_path)
                log_event(entry_id, "info", f"Error screenshot saved to {screenshot_path}")
            except Exception:
                pass
    finally:
        if driver:
            driver.quit()


def run_automation_batch(entry_ids):
    """Run automation for multiple entries concurrently using threads."""
    threads = []
    for eid in entry_ids:
        t = threading.Thread(target=run_automation, args=(eid,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()


def log_event(entry_id, status, message):
    """Log an automation event."""
    event = {
        "entry_id": entry_id,
        "timestamp": datetime.now(PACIFIC_TZ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "status": status,
        "message": message,
    }
    job_log.append(event)
    print(f"[{event['timestamp']}] [{status.upper()}] Entry {entry_id[:8]}... - {message}")


# ─── ROUTES ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/entries", methods=["GET"])
def get_entries():
    return jsonify(list(entries.values()))


@app.route("/api/entries", methods=["POST"])
def add_entry():
    data = request.json
    entry_id = str(uuid.uuid4())
    entry = {
        "id": entry_id,
        "first_name": data["first_name"],
        "last_name": data["last_name"],
        "email": data["email"],
        "phone": data["phone"],
        "course": data["course"],
        "players": data["players"],
        "status": "ready",
        "created_at": datetime.now(PACIFIC_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }
    entries[entry_id] = entry
    return jsonify(entry), 201


@app.route("/api/entries/<entry_id>", methods=["DELETE"])
def delete_entry(entry_id):
    if entry_id in entries:
        del entries[entry_id]
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


@app.route("/api/run-now", methods=["POST"])
def run_now():
    """Run automation immediately for all ready entries."""
    ready_ids = [eid for eid, e in entries.items() if e["status"] == "ready"]
    if not ready_ids:
        return jsonify({"error": "No entries ready to run"}), 400

    # Run in background thread so we don't block the response
    thread = threading.Thread(target=run_automation_batch, args=(ready_ids,))
    thread.start()

    return jsonify({"message": f"Running {len(ready_ids)} entries now", "entry_ids": ready_ids})


@app.route("/api/schedule", methods=["POST"])
def schedule_run():
    """Schedule automation for the next opening time."""
    data = request.json or {}
    custom_time = data.get("custom_time")  # Optional: "HH:MM" format

    now = datetime.now(PACIFIC_TZ)
    today_dow = now.weekday()  # 0=Mon, 6=Sun

    if custom_time:
        hour, minute = map(int, custom_time.split(":"))
    else:
        # Default schedule: Mon-Fri = 4:30 AM, Sat-Sun & Holidays = 3:30 AM
        if today_dow in (5, 6):  # Sat, Sun
            hour, minute = 3, 30
        else:  # Mon-Fri
            hour, minute = 4, 30

    # Figure out the next run time
    run_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_time <= now:
        run_time += timedelta(days=1)

    # Adjust for day-of-week defaults if needed
    run_dow = run_time.weekday()
    if not custom_time:
        if run_dow in (5, 6):  # Sat, Sun
            run_time = run_time.replace(hour=3, minute=30)
        else:  # Mon-Fri
            run_time = run_time.replace(hour=4, minute=30)

    ready_ids = [eid for eid, e in entries.items() if e["status"] == "ready"]
    if not ready_ids:
        return jsonify({"error": "No entries ready to schedule"}), 400

    # Remove any existing scheduled jobs
    for job in scheduler.get_jobs():
        if job.id.startswith("waitlist_"):
            job.remove()

    job_id = f"waitlist_{uuid.uuid4().hex[:8]}"
    scheduler.add_job(
        run_automation_batch,
        "date",
        run_date=run_time,
        args=[ready_ids],
        id=job_id,
        misfire_grace_time=60,
    )

    for eid in ready_ids:
        entries[eid]["status"] = "scheduled"
        entries[eid]["scheduled_for"] = run_time.strftime("%Y-%m-%d %H:%M:%S PT")

    return jsonify({
        "message": f"Scheduled {len(ready_ids)} entries",
        "run_time": run_time.strftime("%Y-%m-%d %H:%M:%S PT"),
        "entry_ids": ready_ids,
    })


@app.route("/api/cancel-schedule", methods=["POST"])
def cancel_schedule():
    """Cancel all scheduled jobs."""
    removed = 0
    for job in scheduler.get_jobs():
        if job.id.startswith("waitlist_"):
            job.remove()
            removed += 1

    for eid, entry in entries.items():
        if entry["status"] == "scheduled":
            entry["status"] = "ready"
            if "scheduled_for" in entry:
                del entry["scheduled_for"]

    return jsonify({"message": f"Cancelled {removed} scheduled jobs"})


@app.route("/api/logs", methods=["GET"])
def get_logs():
    return jsonify(job_log[-100:])  # Last 100 log entries


@app.route("/api/status", methods=["GET"])
def get_status():
    """Get overall system status."""
    scheduled_jobs = [j for j in scheduler.get_jobs() if j.id.startswith("waitlist_")]
    next_run = None
    if scheduled_jobs:
        next_run = scheduled_jobs[0].next_run_time.strftime("%Y-%m-%d %H:%M:%S PT")

    return jsonify({
        "total_entries": len(entries),
        "ready": sum(1 for e in entries.values() if e["status"] == "ready"),
        "scheduled": sum(1 for e in entries.values() if e["status"] == "scheduled"),
        "running": sum(1 for e in entries.values() if e["status"] == "running"),
        "completed": sum(1 for e in entries.values() if e["status"] in ("completed", "completed_placeholder")),
        "failed": sum(1 for e in entries.values() if e["status"] == "failed"),
        "next_scheduled_run": next_run,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
