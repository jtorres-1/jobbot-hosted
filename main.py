import os
import time
import csv
import json
import datetime
import threading
import requests
from flask import Flask
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from datetime import datetime
from flask import Flask, request
import uuid
from selenium.webdriver.chrome.service import Service
import smtplib
from email.message import EmailMessage
from flask import send_file
from flask import send_from_directory, render_template_string



app = Flask(__name__)



# Load initial config
CONFIG_FILE = "config.json"
try:
    with open(CONFIG_FILE) as f:
        config = json.load(f)
except:
    config = {"keywords": [], "max_results": 50, "resume_path": "resume.pdf", "user_data": {}}

@app.route('/webhook', methods=['POST'])

def receive_tally():
    data = request.json
    print("[TALLY] Webhook hit")

    try:
        answers = {a['key']: a['value'] for a in data.get("answers", [])}

        unique_filename = f"resume_{uuid.uuid4().hex}.pdf"
        resume_path = os.path.join("resumes", unique_filename)

        default_config = {
            "keywords": answers.get("keywords", "").split(","),
            "resume_path": resume_path,
            "user_data": {
                "email": answers.get("email", ""),
                "location": answers.get("location", ""),
                "job_type": answers.get("job_type", "")
        }
    }


        resume_url = data.get("resume_url")
        if resume_url and "localhost" not in resume_url:
            for _ in range(3):
                try:
                    response = requests.get(resume_url, timeout=20)
                    with open("resume.pdf", "wb") as f:
                        f.write(response.content)
                    print("[TALLY ✅] Resume downloaded.")
                    break
                except Exception as e:
                    print(f"[TALLY RETRY] Resume download failed: {e}")
        else:
            print("[TALLY] Invalid or missing resume URL — using default")

        config["timestamp"] = str(datetime.utcnow())
        config.update(default_config)


        with open("config.json", "w") as f:
            json.dump(config, f, indent=2)
        print("[TALLY] Config updated.")

        bot_cycle()
        return "Success", 200

    except Exception as e:
        print("[TALLY ERROR]", str(e))
        return "Error", 500


def get_current_config():
    """Load current config from file"""
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except:
        return {"keywords": [], "max_results": 50, "resume_path": "resume.pdf", "user_data": {}}

def location_allowed(text):
    """Check if location is allowed - placeholder function"""
    # Add your location filtering logic here
    # For now, return True to allow all locations
    return True

CSV_PATH = "applied_jobs.csv"

def load_applied_urls():
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", newline="") as f:
            csv.writer(f).writerow(["timestamp", "title", "company", "url"])
        return set()
    with open(CSV_PATH, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        return {row[3] for row in reader if len(row) >= 4}

def log_application(job):
    print("[DEBUG] log_application() was called", flush=True)

    try:
        with open("config.json") as f:
            runtime_config = json.load(f)
    except Exception as e:
        print("[ERROR] Failed to load config.json:", e, flush=True)
        return

    user_data = runtime_config.get("user_data", {})
    ts = datetime.utcnow().isoformat()

    try:
        with open("applied_jobs.csv", mode="a", newline="") as file:
            writer = csv.writer(file)
            writer.writerow([
                ts,
                job["title"],
                job["company"],
                job["url"]
            ])
        print("[CSV ✅] Logged to CSV successfully", flush=True)

    except Exception as e:
        print(f"[CSV ERROR] {e}", flush=True)

    with open(CSV_PATH) as f:
        rows = list(csv.reader(f))
    if len(rows) > 1000:
        with open(CSV_PATH, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(rows[-1000:])


def scrape_remotive():
    config = get_current_config()
    keywords = [kw.lower() for kw in config.get("keywords", [])]
    max_results = config.get("max_results", 50)
    
    print("[SCRAPE] Remotive...", flush=True)
    url  = "https://remotive.io/remote-jobs/software-dev"
    jobs = []
    try:
        r = requests.get(url, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        for tile in soup.select("div.job-tile")[:max_results]:
            t = tile.select_one(".job-tile-title")
            l = tile.select_one("a")
            c = tile.select_one(".job-tile-company")
            if not (t and l): continue
            title = t.get_text(strip=True)
            company = c.get_text(strip=True) if c else "Unknown"
            href = l["href"]
            full = href if href.startswith("http") else f"https://remotive.io{href}"
            text = (title + " " + company + " " + full).lower()
            if any(kw in text for kw in keywords) and location_allowed(text):
                jobs.append({"url": full, "title": title, "company": company})
    except Exception as e:
        print(f"[ERROR] Remotive: {e}", flush=True)
    return jobs

def scrape_remoteok():
    config = get_current_config()
    keywords = [kw.lower() for kw in config.get("keywords", [])]
    max_results = config.get("max_results", 50)
    
    print("[SCRAPE] RemoteOK...", flush=True)
    url = "https://remoteok.io/remote-dev-jobs"
    jobs = []
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        for row in soup.select("tr.job")[:max_results]:
            l = row.select_one("a.preventLink")
            if not l: continue
            full_url = "https://remoteok.io" + l["href"]
            title = row.get("data-position", "Remote Job")
            company = row.get("data-company", "Unknown")
            text = (title + " " + company + " " + full_url).lower()
            if any(kw in text for kw in keywords) and location_allowed(text):
                jobs.append({"url": full_url, "title": title, "company": company})
    except Exception as e:
        print(f"[ERROR] RemoteOK: {e}", flush=True)
    return jobs

def scrape_weworkremotely():
    config = get_current_config()
    keywords = [kw.lower() for kw in config.get("keywords", [])]
    max_results = config.get("max_results", 50)
    
    print("[SCRAPE] WeWorkRemotely...", flush=True)
    url = "https://weworkremotely.com/categories/remote-programming-jobs"
    jobs = []
    try:
        r = requests.get(url, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        for sec in soup.select("section.jobs li.feature")[:max_results]:
            l = sec.select_one("a")
            if not l: continue
            href = l["href"]
            full_url = "https://weworkremotely.com" + href
            title = sec.get_text(strip=True)
            text = (title + " " + full_url).lower()
            if any(kw in title.lower() for kw in keywords) and location_allowed(text):
                jobs.append({"url": full_url, "title": title, "company": "Unknown"})
    except Exception as e:
        print(f"[ERROR] WWR: {e}", flush=True)
    return jobs

def scrape_jobspresso():
    config = get_current_config()
    keywords = [kw.lower() for kw in config.get("keywords", [])]
    max_results = config.get("max_results", 50)
    
    print("[SCRAPE] Jobspresso...", flush=True)
    url = "https://jobspresso.co/remote-developer-jobs/"
    jobs = []
    try:
        r = requests.get(url, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        for li in soup.select("ul.jobs li.job_listing")[:max_results]:
            a = li.select_one("a")
            if not a: continue
            href = a["href"]
            title = a.get("title", "Remote Job")
            company = li.select_one(".company")
            company_name = company.get_text(strip=True) if company else "Unknown"
            text = (title + " " + company_name + " " + href).lower()
            if any(kw in title.lower() for kw in keywords) and location_allowed(text):
                jobs.append({"url": href, "title": title, "company": company_name})
    except Exception as e:
        print(f"[ERROR] Jobspresso: {e}", flush=True)
    return jobs

def scrape_remoteco():
    config = get_current_config()
    keywords = [kw.lower() for kw in config.get("keywords", [])]
    max_results = config.get("max_results", 50)
    
    print("[SCRAPE] Remote.co...", flush=True)
    url = "https://remote.co/remote-jobs/developer/"
    jobs = []
    try:
        r = requests.get(url, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        for row in soup.select("li.job_listing")[:max_results]:
            a = row.select_one("a")
            if not a: continue
            href = a["href"]
            title = a.get("title", "Remote Job")
            company = row.select_one(".company")
            company_name = company.get_text(strip=True) if company else "Unknown"
            text = (title + " " + company_name + " " + href).lower()
            if any(kw in title.lower() for kw in keywords) and location_allowed(text):
                jobs.append({"url": href, "title": title, "company": company_name})
    except Exception as e:
        print(f"[ERROR] Remote.co: {e}", flush=True)
    return jobs

def get_jobs():
    config = get_current_config()
    max_results = config.get("max_results", 50)
    
    all_jobs = []
    for fn in (scrape_remotive, scrape_remoteok, scrape_weworkremotely, scrape_jobspresso, scrape_remoteco):
        try:
            jobs = fn()
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"[SCRAPE ERROR] {fn.__name__}: {e}", flush=True)
        time.sleep(3)  # Delay between scrapers to reduce timeout risk

    seen, unique = set(), []
    for j in all_jobs:
        if j["url"] not in seen:
            seen.add(j["url"])
            unique.append(j)
        if len(unique) >= max_results:
            break

    print(f"[SCRAPE] {len(unique)} unique jobs found", flush=True)
    return unique

def get_chrome_options():
    """Get Chrome options suitable for hosted environments"""
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-plugins")
    opts.add_argument("--disable-images")
    opts.add_argument(f"--user-data-dir=/tmp/profile-{uuid.uuid4()}")
    
    # Try to set binary location if it exists
    possible_paths = ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
    for path in possible_paths:
        if os.path.exists(path):
            opts.binary_location = path
            break
    
    return opts

def safe_get(driver, url, retries=3, wait=5):
    for attempt in range(retries):
        try:
            driver.get(url)
            return True
        except Exception as e:
            print(f"[SAFE_GET ERROR] Attempt {attempt + 1} failed: {e}", flush=True)
            time.sleep(wait)
    print(f"[SAFE_GET FAIL] Failed to load {url} after {retries} tries", flush=True)
    return False


def apply_to_job(job):
    config = get_current_config()
    user_data = config.get("user_data", {})
    resume_path = config.get("resume_path", "resume.pdf")
    
    print(f"[AUTO] Applying → {job['url']}", flush=True)

    if not os.path.exists(resume_path):
        print(f"[ERROR] Resume file not found: {resume_path}")
        return

    opts = get_chrome_options()
    
    try:
        # Try to create Chrome service
        possible_driver_paths = ["/usr/bin/chromedriver", "/usr/local/bin/chromedriver"]
        chrome_service = None
        for path in possible_driver_paths:
            if os.path.exists(path):
                chrome_service = Service(path)
                break
        
        if chrome_service:
            driver = webdriver.Chrome(service=chrome_service, options=opts)
        else:
            driver = webdriver.Chrome(options=opts)  # Let it find chromedriver automatically
    except Exception as e:
        print(f"[AUTO ERROR] Failed to create driver: {e}", flush=True)
        return

    try:
        if not safe_get(driver, job["url"]):
            driver.quit()
            return

        time.sleep(4)

        for inp in driver.find_elements(By.TAG_NAME, "input"):
            name = inp.get_attribute("name") or ""
            if "email" in name.lower():
                inp.send_keys(user_data.get("email", ""))
            elif "name" in name.lower():
                inp.send_keys(user_data.get("full_name", ""))
            elif "phone" in name.lower():
                inp.send_keys(user_data.get("phone", ""))

        for f in driver.find_elements(By.CSS_SELECTOR, "input[type='file']"):
            f.send_keys(os.path.abspath(resume_path))

        for btn in driver.find_elements(By.TAG_NAME, "button"):
            t = btn.text.lower()
            if "submit" in t or "apply" in t:
                btn.click()
                break

        print("[AUTO] Success", flush=True)

    except Exception as e:
        print(f"[AUTO ERROR] {e}", flush=True)

    finally:
        driver.quit()
        print("[DEBUG] log_application() triggered in finally", flush=True)
        log_application(job)

def bot_cycle():

    if os.path.exists(CSV_PATH):
        os.remove(CSV_PATH)
    print("[CSV] Cleared old log")

    applied = load_applied_urls()
    print(f"[BOT] {len(applied)} URLs loaded", flush=True)
    jobs = get_jobs()
    print(f"[BOT] {len(jobs)} jobs fetched", flush=True)
    
    config = get_current_config()
    user_data = config.get("user_data", {})
    
    for job in jobs:
        if job["url"] in applied:
            print(f"[BOT] Skipping {job['url']}", flush=True)
            continue
        apply_to_job(job)
        applied.add(job["url"])
    
    print("[BOT] Cycle complete", flush=True)
    send_email_report(user_data.get("email", ""))

def send_email_report(recipient_email):
    if not recipient_email or "@" not in recipient_email:
        print("[EMAIL SKIP] No valid email")
        return

    try:
        print("[EMAIL] Sending CSV to", recipient_email)
        msg = EmailMessage()
        msg["Subject"] = "Your JobBot Report – Jobs Applied"
        msg["From"] = os.getenv("EMAIL_USER")
        msg["To"] = recipient_email
        msg.set_content("Here is your job application report. We've successfully applied to jobs on your behalf.")

        if os.path.exists("applied_jobs.csv"):
            with open("applied_jobs.csv", "rb") as f:
                msg.add_attachment(f.read(), maintype="application", subtype="octet-stream", filename="applied_jobs.csv")

        with smtplib.SMTP_SSL("smtp.mail.yahoo.com", 465) as smtp:
            smtp.login(os.getenv("EMAIL_USER"), os.getenv("EMAIL_PASS"))
            smtp.send_message(msg)
        print("[EMAIL ✅] Report sent.")
    except Exception as e:
        print("[EMAIL ❌]", str(e))

def scheduler():
    bot_cycle()
    while True:
        time.sleep(3600)  # Run every hour instead of 30 seconds
        bot_cycle()
@app.route("/log")
def view_log():
    if not os.path.exists("applied_jobs.csv"):
        return "No log file found.", 404
    with open("applied_jobs.csv") as f:
        return "<h2>JobBot Logs</h2><pre>" + f.read().replace("\n", "<br>") + "</pre>"

@app.route("/download-log")
def download_log():
    if not os.path.exists("applied_jobs.csv"):
        return "No file to download", 404
    return send_file("applied_jobs.csv", as_attachment=True)

@app.route("/")
def homepage():
    with open("index.html") as f:
        html_content = f.read()
    return render_template_string(html_content)

@app.route("/applied_jobs.csv")
def download_csv():
    return send_from_directory(".", "applied_jobs.csv", as_attachment=True)

@app.route("/download")
def download_logs():
    return send_file("logs.csv", as_attachment=True)


    
if __name__ == "__main__":
    th = threading.Thread(target=scheduler, daemon=True)
    th.start()
    print("[MAIN] Scheduler started", flush=True)
    app.run(host="0.0.0.0", port=3000)
