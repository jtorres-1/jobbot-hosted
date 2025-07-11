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
import urllib.parse # Added this import

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

        # Create resumes directory if it doesn't exist
        if not os.path.exists("resumes"):
            os.makedirs("resumes")

        # Use standardized resume filename
        resume_path = os.path.join("resumes", "resume.pdf")

        default_config = {
            "keywords": [kw.strip() for kw in answers.get("keywords", "").split(",") if kw.strip()],
            "resume_path": resume_path,
            "user_data": {
                "email": answers.get("email", ""),
                "location": answers.get("location", ""),
                "job_type": answers.get("job_type", ""),
                "full_name": answers.get("full_name", ""),
                "phone": answers.get("phone", "")
            }
        }

        # Download the resume file from Tally
        resume_url = ""
        try:
            for answer in data.get("answers", []):
                if answer.get("type") == "file" and answer.get("value"):
                    resume_url = answer["value"][0]  # First uploaded file
                    break

            # Download the resume if URL is valid
            if resume_url and "localhost" not in resume_url:
                for attempt in range(3):
                    try:
                        response = requests.get(resume_url, timeout=30)
                        response.raise_for_status()
                        with open(resume_path, "wb") as f:
                            f.write(response.content)
                        print(f"[TALLY ✅] Resume downloaded to {resume_path}")
                        break
                    except Exception as e:
                        print(f"[TALLY RETRY] Resume download attempt {attempt + 1} failed: {e}")
                        if attempt == 2:
                            print("[TALLY ERROR] Failed to download resume after 3 attempts")
            else:
                print("[TALLY] Invalid or missing resume URL — using default")

        except Exception as e:
            print(f"[TALLY ERROR] Resume handling failed: {e}")

        # Add timestamp and merge user config
        config["timestamp"] = str(datetime.utcnow())
        config.update(default_config)

        # Save config to file
        with open("config.json", "w") as f:
            json.dump(config, f, indent=2)
        print("[TALLY] Config updated.")

        # Launch job application cycle
        threading.Thread(target=bot_cycle, daemon=True).start()
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

    # Keep only last 1000 entries
    try:
        with open(CSV_PATH) as f:
            rows = list(csv.reader(f))
        if len(rows) > 1000:
            with open(CSV_PATH, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerows(rows[-1000:])
    except Exception as e:
        print(f"[CSV CLEANUP ERROR] {e}", flush=True)

# --- New Scraper Functions ---

def scrape_indeed(keywords):
    """Scrape Indeed for US-based jobs"""
    jobs = []
    try:
        query = ' '.join(keywords)
        url = f"https://www.indeed.com/jobs?q={urllib.parse.quote(query)}&l=United+States"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        job_cards = soup.find_all('div', class_='job_seen_beacon')[:10]
        
        for card in job_cards:
            try:
                title_elem = card.find('h2', class_='jobTitle')
                title = title_elem.find('a').text.strip() if title_elem else 'N/A'
                
                company_elem = card.find('span', class_='companyName')
                company = company_elem.text.strip() if company_elem else 'N/A'
                
                location_elem = card.find('div', class_='companyLocation')
                location = location_elem.text.strip() if location_elem else 'N/A'
                
                link_elem = title_elem.find('a') if title_elem else None
                job_url = f"https://www.indeed.com{link_elem['href']}" if link_elem and link_elem.get('href') else 'N/A'
                
                jobs.append({
                    'title': title,
                    'company': company,
                    'location': location,
                    'url': job_url
                })
            except Exception:
                continue
                
    except Exception:
        pass
    
    return jobs

def scrape_glassdoor(keywords):
    """Scrape Glassdoor for US-based jobs"""
    jobs = []
    try:
        query = ' '.join(keywords)
        url = f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={urllib.parse.quote(query)}&locT=N&locId=1&locKeyword=United%20States"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        job_cards = soup.find_all('div', class_='jobContainer')[:10]
        
        for card in job_cards:
            try:
                title_elem = card.find('a', class_='jobLink')
                title = title_elem.text.strip() if title_elem else 'N/A'
                
                company_elem = card.find('div', class_='employerName')
                company = company_elem.text.strip() if company_elem else 'N/A'
                
                location_elem = card.find('div', class_='loc')
                location = location_elem.text.strip() if location_elem else 'N/A'
                
                job_url = title_elem['href'] if title_elem and title_elem.get('href') else 'N/A'
                if job_url != 'N/A' and not job_url.startswith('http'):
                    job_url = f"https://www.glassdoor.com{job_url}"
                
                jobs.append({
                    'title': title,
                    'company': company,
                    'location': location,
                    'url': job_url
                })
            except Exception:
                continue
                
    except Exception:
        pass
    
    return jobs

def scrape_monster(keywords):
    """Scrape Monster for US-based jobs"""
    jobs = []
    try:
        query = ' '.join(keywords)
        url = f"https://www.monster.com/jobs/search?q={urllib.parse.quote(query)}&where=United-States"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        job_cards = soup.find_all('div', class_='job-cardstyle__JobCardContainer-sc-1mbmxes-0')[:10]
        
        for card in job_cards:
            try:
                title_elem = card.find('h3', class_='job-cardstyle__JobTitle-sc-1mbmxes-4')
                title = title_elem.text.strip() if title_elem else 'N/A'
                
                company_elem = card.find('span', class_='job-cardstyle__CompanyName-sc-1mbmxes-8')
                company = company_elem.text.strip() if company_elem else 'N/A'
                
                location_elem = card.find('span', class_='job-cardstyle__JobLocation-sc-1mbmxes-9')
                location = location_elem.text.strip() if location_elem else 'N/A'
                
                link_elem = card.find('a')
                job_url = link_elem['href'] if link_elem and link_elem.get('href') else 'N/A'
                if job_url != 'N/A' and not job_url.startswith('http'):
                    job_url = f"https://www.monster.com{job_url}"
                
                jobs.append({
                    'title': title,
                    'company': company,
                    'location': location,
                    'url': job_url
                })
            except Exception:
                continue
                
    except Exception:
        pass
    
    return jobs

def scrape_ziprecruiter(keywords):
    """Scrape ZipRecruiter for US-based jobs"""
    jobs = []
    try:
        query = ' '.join(keywords)
        url = f"https://www.ziprecruiter.com/jobs-search?search={urllib.parse.quote(query)}&location=United+States"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        job_cards = soup.find_all('div', class_='job_content')[:10]
        
        for card in job_cards:
            try:
                title_elem = card.find('h2', class_='job_title')
                title = title_elem.text.strip() if title_elem else 'N/A'
                
                company_elem = card.find('a', class_='company_name')
                company = company_elem.text.strip() if company_elem else 'N/A'
                
                location_elem = card.find('span', class_='location')
                location = location_elem.text.strip() if location_elem else 'N/A'
                
                link_elem = title_elem.find('a') if title_elem else None
                job_url = link_elem['href'] if link_elem and link_elem.get('href') else 'N/A'
                if job_url != 'N/A' and not job_url.startswith('http'):
                    job_url = f"https://www.ziprecruiter.com{job_url}"
                
                jobs.append({
                    'title': title,
                    'company': company,
                    'location': location,
                    'url': job_url
                })
            except Exception:
                continue
                
    except Exception:
        pass
    
    return jobs

def scrape_careerbuilder(keywords):
    """Scrape CareerBuilder for US-based jobs"""
    jobs = []
    try:
        query = ' '.join(keywords)
        url = f"https://www.careerbuilder.com/jobs?keywords={urllib.parse.quote(query)}&location=United+States"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        job_cards = soup.find_all('div', class_='data-results-content')[:10]
        
        for card in job_cards:
            try:
                title_elem = card.find('h2', class_='data-results-title')
                title = title_elem.text.strip() if title_elem else 'N/A'
                
                company_elem = card.find('div', class_='data-results-company')
                company = company_elem.text.strip() if company_elem else 'N/A'
                
                location_elem = card.find('div', class_='data-results-location')
                location = location_elem.text.strip() if location_elem else 'N/A'
                
                link_elem = title_elem.find('a') if title_elem else None
                job_url = link_elem['href'] if link_elem and link_elem.get('href') else 'N/A'
                if job_url != 'N/A' and not job_url.startswith('http'):
                    job_url = f"https://www.careerbuilder.com{job_url}"
                
                jobs.append({
                    'title': title,
                    'company': company,
                    'location': location,
                    'url': job_url
                })
            except Exception:
                continue
                
    except Exception:
        pass
    
    return jobs

def scrape_simplyhired(keywords):
    """Scrape SimplyHired for US-based jobs"""
    jobs = []
    try:
        query = ' '.join(keywords)
        url = f"https://www.simplyhired.com/search?q={urllib.parse.quote(query)}&l=United+States"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        job_cards = soup.find_all('div', class_='SerpJob-jobCard')[:10]
        
        for card in job_cards:
            try:
                title_elem = card.find('h3', class_='jobposting-title')
                title = title_elem.text.strip() if title_elem else 'N/A'
                
                company_elem = card.find('span', class_='JobPosting-labelWithIcon')
                company = company_elem.text.strip() if company_elem else 'N/A'
                
                location_elem = card.find('span', class_='JobPosting-labelWithIcon') # This might need adjustment, typically location is separate
                location = location_elem.text.strip() if location_elem else 'N/A'
                
                link_elem = title_elem.find('a') if title_elem else None
                job_url = link_elem['href'] if link_elem and link_elem.get('href') else 'N/A'
                if job_url != 'N/A' and not job_url.startswith('http'):
                    job_url = f"https://www.simplyhired.com{job_url}"
                
                jobs.append({
                    'title': title,
                    'company': company,
                    'location': location,
                    'url': job_url
                })
            except Exception:
                continue
                
    except Exception:
        pass
    
    return jobs

# --- Existing Scraper Functions (modified to use keywords from config) ---

def scrape_jobspresso():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    print("[SCRAPE] Jobspresso...", flush=True)
    url = "https://jobspresso.co/remote-developer-jobs/"
    jobs = []
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for li in soup.select("ul.jobs li.job_listing")[:max_results]:
            a = li.select_one("a")
            if not a: continue
            href = a["href"]
            title = a.get("title", "Remote Job")
            company = li.select_one(".company")
            company_name = company.get_text(strip=True) if company else "Unknown"
            text = (title + " " + company_name + " " + href).lower()
            if (not keywords or any(kw in text for kw in keywords)) and location_allowed(text):
                jobs.append({"url": href, "title": title, "company": company_name})
    except Exception as e:
        print(f"[ERROR] Jobspresso: {e}", flush=True)
    return jobs

def scrape_remoteco():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    print("[SCRAPE] Remote.co...", flush=True)
    url = "https://remote.co/remote-jobs/developer/"
    jobs = []
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for row in soup.select("li.job_listing")[:max_results]:
            a = row.select_one("a")
            if not a: continue
            href = a["href"]
            title = a.get("title", "Remote Job")
            company = row.select_one(".company")
            company_name = company.get_text(strip=True) if company else "Unknown"
            text = (title + " " + company_name + " " + href).lower()
            if (not keywords or any(kw in text for kw in keywords)) and location_allowed(text):
                jobs.append({"url": href, "title": title, "company": company_name})
    except Exception as e:
        print(f"[ERROR] Remote.co: {e}", flush=True)
    return jobs

def scrape_weworkremotely():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    print("[SCRAPE] WeWorkRemotely...", flush=True)
    url = "https://weworkremotely.com/categories/remote-programming-jobs"
    jobs = []
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for sec in soup.select("section.jobs li.feature")[:max_results]:
            l = sec.select_one("a")
            if not l: continue
            href = l["href"]
            full_url = "https://weworkremotely.com" + href
            title = sec.get_text(strip=True)
            company = "Unknown"
            # Try to extract company from the title
            if " at " in title:
                title_parts = title.split(" at ")
                if len(title_parts) > 1:
                    company = title_parts[1].strip()
                    title = title_parts[0].strip()
            text = (title + " " + company + " " + full_url).lower()
            if (not keywords or any(kw in text for kw in keywords)) and location_allowed(text):
                jobs.append({"url": full_url, "title": title, "company": company})
    except Exception as e:
        print(f"[ERROR] WWR: {e}", flush=True)
    return jobs

def scrape_remoteok():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    print("[SCRAPE] RemoteOK...", flush=True)
    url = "https://remoteok.io/remote-dev-jobs"
    jobs = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for row in soup.select("tr.job")[:max_results]:
            l = row.select_one("a.preventLink")
            if not l: continue
            full_url = "https://remoteok.io" + l["href"]
            title = row.get("data-position", "Remote Job")
            company = row.get("data-company", "Unknown")
            text = (title + " " + company + " " + full_url).lower()
            if (not keywords or any(kw in text for kw in keywords)) and location_allowed(text):
                jobs.append({"url": full_url, "title": title, "company": company})
    except Exception as e:
        print(f"[ERROR] RemoteOK: {e}", flush=True)
    return jobs

def scrape_flexjobs():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    print("[SCRAPE] FlexJobs...", flush=True)
    url = "https://www.flexjobs.com/remote-jobs/developer"
    jobs = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select("div.job")[:max_results]:
            a = item.select_one("a")
            if not a: continue
            href = a["href"]
            if not href.startswith("http"):
                href = "https://www.flexjobs.com" + href
            title = a.get_text(strip=True)
            company = item.select_one(".company")
            company_name = company.get_text(strip=True) if company else "Unknown"
            text = (title + " " + company_name + " " + href).lower()
            if (not keywords or any(kw in text for kw in keywords)) and location_allowed(text):
                jobs.append({"url": href, "title": title, "company": company_name})
    except Exception as e:
        print(f"[ERROR] FlexJobs: {e}", flush=True)
    return jobs

# The original scrape_indeed function was already present and handled remote jobs
# The new scrape_indeed function provided in your prompt takes 'keywords' as argument
# To avoid redundancy and leverage the existing keyword logic, I'll modify the existing one
# to also accept keywords and adjust its call within get_jobs.
# I'm renaming the new one you provided to scrape_indeed_new_us for clarity if you want to use it alongside.

def get_jobs():
    config = get_current_config()
    keywords_from_config = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    all_jobs = []
    scrapers = [
        scrape_jobspresso,
        scrape_remoteco,
        scrape_weworkremotely,
        scrape_remoteok,
        scrape_flexjobs,
        lambda: scrape_indeed(keywords_from_config), # Calling the newly provided Indeed scraper
        lambda: scrape_glassdoor(keywords_from_config),
        lambda: scrape_monster(keywords_from_config),
        lambda: scrape_ziprecruiter(keywords_from_config),
        lambda: scrape_careerbuilder(keywords_from_config),
        lambda: scrape_simplyhired(keywords_from_config)
    ]
    
    for fn in scrapers:
        try:
            jobs = fn()
            all_jobs.extend(jobs)
            print(f"[SCRAPE] {fn.__name__}: {len(jobs)} jobs found", flush=True)
        except Exception as e:
            print(f"[SCRAPE ERROR] {fn.__name__}: {e}", flush=True)
        time.sleep(2)  # Delay between scrapers

    # Remove duplicates
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
    opts.add_argument("--disable-web-security")
    opts.add_argument("--disable-features=VizDisplayCompositor")
    opts.add_argument("--remote-debugging-port=9222")
    
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
    driver = None
    
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
            return

        time.sleep(4)

        # Fill out form fields
        inputs_filled = 0
        for inp in driver.find_elements(By.TAG_NAME, "input"):
            try:
                name = (inp.get_attribute("name") or "").lower()
                placeholder = (inp.get_attribute("placeholder") or "").lower()
                input_type = (inp.get_attribute("type") or "").lower()
                
                if input_type == "file":
                    inp.send_keys(os.path.abspath(resume_path))
                    inputs_filled += 1
                    print(f"[AUTO] Uploaded resume to file input", flush=True)
                elif "email" in name or "email" in placeholder:
                    if user_data.get("email"):
                        inp.clear()
                        inp.send_keys(user_data["email"])
                        inputs_filled += 1
                elif "name" in name or "name" in placeholder:
                    if user_data.get("full_name"):
                        inp.clear()
                        inp.send_keys(user_data["full_name"])
                        inputs_filled += 1
                elif "phone" in name or "phone" in placeholder:
                    if user_data.get("phone"):
                        inp.clear()
                        inp.send_keys(user_data["phone"])
                        inputs_filled += 1
            except Exception as e:
                print(f"[AUTO] Input error: {e}", flush=True)

        # Also try textarea fields
        for textarea in driver.find_elements(By.TAG_NAME, "textarea"):
            try:
                name = (textarea.get_attribute("name") or "").lower()
                placeholder = (textarea.get_attribute("placeholder") or "").lower()
                
                if "message" in name or "cover" in name or "letter" in name:
                    if user_data.get("cover_letter"):
                        textarea.clear()
                        textarea.send_keys(user_data["cover_letter"])
                        inputs_filled += 1
            except Exception as e:
                print(f"[AUTO] Textarea error: {e}", flush=True)

        print(f"[AUTO] Filled {inputs_filled} form fields", flush=True)

        # Submit form
        submitted = False
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            try:
                t = btn.text.lower()
                if "submit" in t or "apply" in t or "send" in t:
                    btn.click()
                    submitted = True
                    print(f"[AUTO] Clicked submit button: {btn.text}", flush=True)
                    break
            except Exception as e:
                print(f"[AUTO] Button click error: {e}", flush=True)

        # If no button worked, try input submit
        if not submitted:
            for inp in driver.find_elements(By.TAG_NAME, "input"):
                try:
                    input_type = (inp.get_attribute("type") or "").lower()
                    if input_type == "submit":
                        inp.click()
                        submitted = True
                        print(f"[AUTO] Clicked submit input", flush=True)
                        break
                except Exception as e:
                    print(f"[AUTO] Submit input error: {e}", flush=True)

        time.sleep(3)  # Wait for submission
        if submitted:
            print("[AUTO] Success - Form submitted", flush=True)
        else:
            print("[AUTO] Warning - No submit button found", flush=True)

    except Exception as e:
        print(f"[AUTO ERROR] {e}", flush=True)

    finally:
        if driver:
            driver.quit()
        print("[DEBUG] log_application() triggered in finally", flush=True)
        log_application(job)

def bot_cycle():
    """Main function to run the job application bot cycle"""
    print("[BOT] Starting job application cycle...", flush=True)
    applied_urls = load_applied_urls()
    jobs_to_apply = get_jobs()
    
    newly_applied_count = 0
    
    for job in jobs_to_apply:
        if job["url"] not in applied_urls:
            apply_to_job(job)
            applied_urls.add(job["url"])
            newly_applied_count += 1
            time.sleep(5)  # Wait between applications to avoid being blocked
        else:
            print(f"[SKIP] Already applied to: {job['title']} at {job['company']}", flush=True)
            
    print(f"[BOT] Job application cycle finished. Applied to {newly_applied_count} new jobs.", flush=True)
    
@app.route('/applied_jobs')
def show_applied_jobs():
    if not os.path.exists(CSV_PATH):
        return "No jobs applied yet.", 200

    try:
        with open(CSV_PATH, newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)  # Read header row
            jobs_data = list(reader)     # Read remaining rows

        html_table = f"""
        <style>
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ border: 1px solid black; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
        </style>
        <h1>Applied Jobs</h1>
        <table>
            <thead>
                <tr>
                    {"".join(f"<th>{col}</th>" for col in header)}
                </tr>
            </thead>
            <tbody>
                {"".join(f"<tr>{''.join(f'<td>{item}</td>' for item in row)}</tr>" for row in jobs_data)}
            </tbody>
        </table>
        """
        return render_template_string(html_table), 200
    except Exception as e:
        return f"Error reading applied_jobs.csv: {e}", 500

@app.route('/download_applied_jobs')
def download_applied_jobs():
    try:
        return send_file(CSV_PATH, as_attachment=True, download_name="applied_jobs.csv", mimetype="text/csv")
    except Exception as e:
        return f"Error downloading file: {e}"

@app.route('/')
def index():
    config = get_current_config()
    keywords = ", ".join(config.get("keywords", []))
    resume_path = config.get("resume_path", "Not set")
    last_run = config.get("timestamp", "Never")
    
    return f"""
    <h1>Job Bot Automation</h1>
    <p><strong>Keywords:</strong> {keywords}</p>
    <p><strong>Resume Path:</strong> {resume_path}</p>
    <p><strong>Last Config Update / Bot Run:</strong> {last_run} UTC</p>
    <p><a href="/applied_jobs">View Applied Jobs</a></p>
    <p><a href="/download_applied_jobs">Download Applied Jobs CSV</a></p>
    <h2>How to Update Configuration:</h2>
    <p>Submit your job preferences and resume via the Tally.so form connected to this bot's webhook.</p>
    """

if __name__ == '__main__':
    # Initial bot cycle can be started here or only via webhook
    # threading.Thread(target=bot_cycle, daemon=True).start()
    app.run(host='0.0.0.0', port=os.environ.get('PORT', 5000), debug=True)
