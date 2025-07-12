import os
import time
import csv
import json
import logging
import datetime
import threading
import requests
from flask import Flask, request, send_file, render_template_string
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import uuid
import urllib.parse

# --- Configuration and Logging ---

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Load initial config
CONFIG_FILE = "config.json"
try:
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    logger.info(f"Loaded initial config from {CONFIG_FILE}")
except FileNotFoundError:
    config = {"keywords": [], "max_results": 50, "resume_path": "resumes/resume.pdf", "user_data": {}}
    logger.warning(f"Config file {CONFIG_FILE} not found. Initialized with default config.")
except json.JSONDecodeError:
    config = {"keywords": [], "max_results": 50, "resume_path": "resumes/resume.pdf", "user_data": {}}
    logger.error(f"Error decoding {CONFIG_FILE}. Initialized with default config.")
except Exception as e:
    config = {"keywords": [], "max_results": 50, "resume_path": "resumes/resume.pdf", "user_data": {}}
    logger.error(f"An unexpected error occurred loading config: {e}. Initialized with default config.")

# Path for applied jobs CSV
CSV_PATH = "applied_jobs.csv"

# Ensure resumes directory exists
if not os.path.exists("resumes"):
    os.makedirs("resumes")
    logger.info("Created 'resumes' directory.")

# --- Helper Functions ---

def get_current_config():
    """Load current config from file"""
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load config.json: {e}. Returning default config.")
        return {"keywords": [], "max_results": 50, "resume_path": "resumes/resume.pdf", "user_data": {}}

def location_allowed(text):
    """Placeholder: Add your location filtering logic here"""
    # For now, return True to allow all locations
    return True

def load_applied_urls():
    """Loads URLs of previously applied jobs from CSV_PATH."""
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", newline="") as f:
            csv.writer(f).writerow(["timestamp", "title", "company", "url"])
        logger.info(f"Created new CSV file: {CSV_PATH}")
        return set()
    try:
        with open(CSV_PATH, newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header
            return {row[3] for row in reader if len(row) >= 4}
    except Exception as e:
        logger.error(f"Error loading applied URLs from CSV: {e}")
        return set()

def log_application(job):
    """Logs a successful job application to the CSV_PATH file."""
    logger.debug("log_application() was called")

    runtime_config = get_current_config() # Always get the latest config
    if not runtime_config:
        logger.error("Could not load runtime config for logging application.")
        return

    ts = datetime.utcnow().isoformat()

    try:
        with open(CSV_PATH, mode="a", newline="") as file:
            writer = csv.writer(file)
            writer.writerow([
                ts,
                job.get("title", "N/A"),
                job.get("company", "N/A"),
                job.get("url", "N/A")
            ])
        logger.info(f"[CSV ✅] Logged application: {job.get('title', 'N/A')} at {job.get('company', 'N/A')}")
    except Exception as e:
        logger.error(f"[CSV ERROR] Failed to log application to CSV: {e}")

    # Keep only last 1000 entries for performance/size control
    try:
        with open(CSV_PATH, 'r') as f:
            rows = list(csv.reader(f))
        if len(rows) > 1000:
            with open(CSV_PATH, "w", newline="") as f:
                writer = csv.writer(f)
                # Keep header + last 999 entries
                writer.writerows([rows[0]] + rows[-999:])
            logger.info(f"Trimmed {CSV_PATH} to last 1000 entries.")
    except Exception as e:
        logger.error(f"[CSV CLEANUP ERROR] Failed to clean up CSV: {e}")

# --- Scraper Functions ---
# Note: For production, consider using a dedicated proxy or a service like Scrapy Cloud
# for more reliable and scalable scraping, as direct requests can be easily blocked.

def _make_request(url, headers, timeout=15):
    """Helper to make robust HTTP requests."""
    try:
        session = requests.Session()
        response = session.get(url, headers=headers, timeout=timeout)
        response.raise_for_status() # Raise an exception for HTTP errors
        return BeautifulSoup(response.content, 'html.parser')
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed for {url}: {e}")
        return None

def scrape_indeed(keywords):
    """Scrape Indeed for US-based jobs."""
    jobs = []
    query = ' '.join(keywords)
    url = f"https://www.indeed.com/jobs?q={urllib.parse.quote(query)}&l=United+States"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    soup = _make_request(url, headers)
    if not soup:
        return jobs

    job_cards = soup.find_all('div', class_='job_seen_beacon')[:10] # Limit to 10 for quick scan
    
    for card in job_cards:
        try:
            title_elem = card.find('h2', class_='jobTitle')
            title = title_elem.find('a').text.strip() if title_elem and title_elem.find('a') else 'N/A'
            
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
        except Exception as e:
            logger.warning(f"Error parsing Indeed job card: {e}")
            continue
    logger.info(f"Scraped {len(jobs)} jobs from Indeed.")
    return jobs

def scrape_glassdoor(keywords):
    """Scrape Glassdoor for US-based jobs."""
    jobs = []
    query = ' '.join(keywords)
    url = f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={urllib.parse.quote(query)}&locT=N&locId=1&locKeyword=United%20States"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    soup = _make_request(url, headers)
    if not soup:
        return jobs
        
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
        except Exception as e:
            logger.warning(f"Error parsing Glassdoor job card: {e}")
            continue
    logger.info(f"Scraped {len(jobs)} jobs from Glassdoor.")
    return jobs

def scrape_monster(keywords):
    """Scrape Monster for US-based jobs."""
    jobs = []
    query = ' '.join(keywords)
    url = f"https://www.monster.com/jobs/search?q={urllib.parse.quote(query)}&where=United-States"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    soup = _make_request(url, headers)
    if not soup:
        return jobs
        
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
        except Exception as e:
            logger.warning(f"Error parsing Monster job card: {e}")
            continue
    logger.info(f"Scraped {len(jobs)} jobs from Monster.")
    return jobs

def scrape_ziprecruiter(keywords):
    """Scrape ZipRecruiter for US-based jobs."""
    jobs = []
    query = ' '.join(keywords)
    url = f"https://www.ziprecruiter.com/jobs-search?search={urllib.parse.quote(query)}&location=United+States"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    soup = _make_request(url, headers)
    if not soup:
        return jobs
        
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
        except Exception as e:
            logger.warning(f"Error parsing ZipRecruiter job card: {e}")
            continue
    logger.info(f"Scraped {len(jobs)} jobs from ZipRecruiter.")
    return jobs

def scrape_careerbuilder(keywords):
    """Scrape CareerBuilder for US-based jobs."""
    jobs = []
    query = ' '.join(keywords)
    url = f"https://www.careerbuilder.com/jobs?keywords={urllib.parse.quote(query)}&location=United+States"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    soup = _make_request(url, headers)
    if not soup:
        return jobs
        
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
        except Exception as e:
            logger.warning(f"Error parsing CareerBuilder job card: {e}")
            continue
    logger.info(f"Scraped {len(jobs)} jobs from CareerBuilder.")
    return jobs

def scrape_simplyhired(keywords):
    """Scrape SimplyHired for US-based jobs."""
    jobs = []
    query = ' '.join(keywords)
    url = f"https://www.simplyhired.com/search?q={urllib.parse.quote(query)}&l=United+States"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    soup = _make_request(url, headers)
    if not soup:
        return jobs
        
    job_cards = soup.find_all('div', class_='SerpJob-jobCard')[:10]
    
    for card in job_cards:
        try:
            title_elem = card.find('h3', class_='jobposting-title')
            title = title_elem.text.strip() if title_elem else 'N/A'
            
            # SimplyHired sometimes puts company and location in the same span or nearby
            company_elem = card.find('span', {'data-testid': 'companyName'}) or card.find('span', class_='JobPosting-labelWithIcon')
            company = company_elem.text.strip() if company_elem else 'N/A'
            
            location_elem = card.find('span', {'data-testid': 'location'}) or card.find('span', class_='JobPosting-labelWithIcon')
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
        except Exception as e:
            logger.warning(f"Error parsing SimplyHired job card: {e}")
            continue
    logger.info(f"Scraped {len(jobs)} jobs from SimplyHired.")
    return jobs

def scrape_jobspresso():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    logger.info("[SCRAPE] Jobspresso...")
    url = "https://jobspresso.co/remote-developer-jobs/"
    jobs = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    soup = _make_request(url, headers)
    if not soup:
        return jobs

    for li in soup.select("ul.jobs li.job_listing")[:max_results]:
        try:
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
            logger.warning(f"Error parsing Jobspresso job entry: {e}")
            continue
    logger.info(f"Scraped {len(jobs)} jobs from Jobspresso.")
    return jobs

def scrape_remoteco():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    logger.info("[SCRAPE] Remote.co...")
    url = "https://remote.co/remote-jobs/developer/"
    jobs = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    soup = _make_request(url, headers)
    if not soup:
        return jobs

    for row in soup.select("li.job_listing")[:max_results]:
        try:
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
            logger.warning(f"Error parsing Remote.co job entry: {e}")
            continue
    logger.info(f"Scraped {len(jobs)} jobs from Remote.co.")
    return jobs

def scrape_weworkremotely():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    logger.info("[SCRAPE] WeWorkRemotely...")
    url = "https://weworkremotely.com/categories/remote-programming-jobs"
    jobs = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    soup = _make_request(url, headers)
    if not soup:
        return jobs

    for sec in soup.select("section.jobs li.feature")[:max_results]:
        try:
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
            logger.warning(f"Error parsing WeWorkRemotely job entry: {e}")
            continue
    logger.info(f"Scraped {len(jobs)} jobs from WeWorkRemotely.")
    return jobs

def scrape_remoteok():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    logger.info("[SCRAPE] RemoteOK...")
    url = "https://remoteok.io/remote-dev-jobs"
    jobs = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    soup = _make_request(url, headers)
    if not soup:
        return jobs

    for row in soup.select("tr.job")[:max_results]:
        try:
            l = row.select_one("a.preventLink")
            if not l: continue
            full_url = "https://remoteok.io" + l["href"]
            title = row.get("data-position", "Remote Job")
            company = row.get("data-company", "Unknown")
            text = (title + " " + company + " " + full_url).lower()
            if (not keywords or any(kw in text for kw in keywords)) and location_allowed(text):
                jobs.append({"url": full_url, "title": title, "company": company})
        except Exception as e:
            logger.warning(f"Error parsing RemoteOK job entry: {e}")
            continue
    logger.info(f"Scraped {len(jobs)} jobs from RemoteOK.")
    return jobs

def scrape_flexjobs():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    logger.info("[SCRAPE] FlexJobs...")
    url = "https://www.flexjobs.com/remote-jobs/developer"
    jobs = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    soup = _make_request(url, headers)
    if not soup:
        return jobs

    for item in soup.select("div.job")[:max_results]:
        try:
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
            logger.warning(f"Error parsing FlexJobs job entry: {e}")
            continue
    logger.info(f"Scraped {len(jobs)} jobs from FlexJobs.")
    return jobs

def get_jobs():
    """Aggregates jobs from all defined scrapers."""
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
        lambda: scrape_indeed(keywords_from_config), 
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
            logger.info(f"[SCRAPE] {fn.__name__}: {len(jobs)} jobs found")
        except Exception as e:
            logger.error(f"[SCRAPE ERROR] {fn.__name__}: {e}")
        time.sleep(2)  # Delay between scrapers to be polite

    # Remove duplicates
    seen, unique = set(), []
    for j in all_jobs:
        if j["url"] not in seen:
            seen.add(j["url"])
            unique.append(j)
        if len(unique) >= max_results:
            break

    logger.info(f"[SCRAPE] Found {len(unique)} unique jobs across all sources.")
    return unique

# --- Selenium Automation Functions ---

def get_chrome_options():
    """
    Get Chrome options suitable for Render.
    Assumes Chromium and ChromeDriver are installed via build script or Dockerfile.
    """
    opts = Options()
    opts.add_argument("--headless") # Run in headless mode
    opts.add_argument("--no-sandbox") # Bypass OS security model, necessary for Docker
    opts.add_argument("--disable-dev-shm-usage") # Overcome limited resource problems
    opts.add_argument("--disable-gpu") # Applicable to older OSs
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-plugins")
    # opts.add_argument("--disable-images") # Can speed up, but might break some forms
    opts.add_argument(f"--user-data-dir=/tmp/chrome-profile-{uuid.uuid4()}") # Unique profile for each run
    opts.add_argument("--remote-debugging-port=9222") # For potential remote debugging

    # Common paths for Chromium/ChromeDriver on Linux systems like Render
    # Prioritize environment variables if set for custom paths
    chrome_binary_path = os.environ.get('CHROME_BINARY_PATH', '/usr/bin/google-chrome')
    chromedriver_path = os.environ.get('CHROMEDRIVER_PATH', '/usr/bin/chromedriver')

    if os.path.exists(chrome_binary_path):
        opts.binary_location = chrome_binary_path
        logger.info(f"Using Chrome binary at: {chrome_binary_path}")
    else:
        logger.warning(f"Chrome binary not found at {chrome_binary_path}. Hope it's in PATH.")

    return opts, chromedriver_path

def safe_get(driver, url, retries=3, wait_time=5):
    """Safely navigates to a URL with retries."""
    for attempt in range(retries):
        try:
            driver.get(url)
            # Wait for page to load (e.g., body element to be present)
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            logger.info(f"Successfully loaded {url} on attempt {attempt + 1}")
            return True
        except Exception as e:
            logger.error(f"[SAFE_GET ERROR] Attempt {attempt + 1} failed for {url}: {e}")
            time.sleep(wait_time)
    logger.error(f"[SAFE_GET FAIL] Failed to load {url} after {retries} tries")
    return False

def apply_to_job(job):
    """Attempts to apply to a job using Selenium."""
    config = get_current_config()
    user_data = config.get("user_data", {})
    resume_path = config.get("resume_path", "resumes/resume.pdf") # Default to 'resumes/resume.pdf'

    logger.info(f"[AUTO] Attempting to apply to → {job.get('url', 'N/A')}")

    if not os.path.exists(resume_path):
        logger.error(f"[AUTO ERROR] Resume file not found at: {resume_path}. Skipping application.")
        return False

    opts, chromedriver_path = get_chrome_options()
    driver = None
    
    try:
        # Create Chrome service
        chrome_service = None
        if os.path.exists(chromedriver_path):
            chrome_service = Service(chromedriver_path)
            driver = webdriver.Chrome(service=chrome_service, options=opts)
            logger.info(f"Using Chromedriver from service path: {chromedriver_path}")
        else:
            # Fallback if chromedriver_path doesn't exist (e.g., if it's in PATH)
            driver = webdriver.Chrome(options=opts)
            logger.warning(f"Chromedriver not found at {chromedriver_path}. Attempting to use default PATH.")

    except Exception as e:
        logger.critical(f"[AUTO ERROR] Failed to initialize Chrome driver: {e}")
        return False

    try:
        if not safe_get(driver, job["url"]):
            logger.error(f"Could not load job URL: {job.get('url', 'N/A')}. Skipping application.")
            return False

        # Wait for potential initial page load/animations
        time.sleep(3) 

        # --- Form Filling Logic (More robust) ---
        inputs_filled = 0

        # Attempt to fill email
        email = user_data.get("email")
        if email:
            try:
                email_input = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//input[contains(@name, 'email') or contains(@placeholder, 'email') or contains(@id, 'email') or contains(@type, 'email')]"))
                )
                email_input.clear()
                email_input.send_keys(email)
                inputs_filled += 1
                logger.debug(f"[AUTO] Filled email: {email}")
            except Exception as e:
                logger.debug(f"Could not fill email field: {e}")
        
        # Attempt to fill full name
        full_name = user_data.get("full_name")
        if full_name:
            try:
                name_input = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//input[contains(@name, 'name') or contains(@placeholder, 'name') or contains(@id, 'name') or contains(@aria-label, 'name') or contains(@autocomplete, 'name')]"))
                )
                name_input.clear()
                name_input.send_keys(full_name)
                inputs_filled += 1
                logger.debug(f"[AUTO] Filled full name: {full_name}")
            except Exception as e:
                logger.debug(f"Could not fill name field: {e}")

        # Attempt to fill phone
        phone = user_data.get("phone")
        if phone:
            try:
                phone_input = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//input[contains(@name, 'phone') or contains(@placeholder, 'phone') or contains(@id, 'phone') or contains(@type, 'tel')]"))
                )
                phone_input.clear()
                phone_input.send_keys(phone)
                inputs_filled += 1
                logger.debug(f"[AUTO] Filled phone: {phone}")
            except Exception as e:
                logger.debug(f"Could not fill phone field: {e}")

        # Attempt to upload resume
        try:
            file_input = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//input[@type='file' and (contains(@name, 'resume') or contains(@id, 'resume') or contains(@aria-label, 'resume') or contains(@class, 'resume'))]"))
            )
            file_input.send_keys(os.path.abspath(resume_path))
            inputs_filled += 1
            logger.info(f"[AUTO] Uploaded resume from {os.path.abspath(resume_path)}")
        except Exception as e:
            logger.warning(f"Could not find or upload resume file input: {e}")

        # Attempt to fill cover letter (if provided in user_data)
        cover_letter = user_data.get("cover_letter")
        if cover_letter:
            try:
                cover_letter_textarea = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//textarea[contains(@name, 'cover_letter') or contains(@placeholder, 'cover letter') or contains(@id, 'cover_letter') or contains(@aria-label, 'cover letter') or contains(@name, 'message')]"))
                )
                cover_letter_textarea.clear()
                cover_letter_textarea.send_keys(cover_letter)
                inputs_filled += 1
                logger.debug("[AUTO] Filled cover letter.")
            except Exception as e:
                logger.debug(f"Could not find or fill cover letter textarea: {e}")

        logger.info(f"[AUTO] Filled {inputs_filled} form fields.")

        # --- Submit Form Logic ---
        submitted = False
        submit_attempts = 0
        
        # Try finding a button by text or specific attributes
        try:
            submit_button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, 
                    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit') or "
                    "contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply') or "
                    "contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'send')] | "
                    "//input[@type='submit' or @type='button' and (contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit') or contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply'))]"
                ))
            )
            submit_button.click()
            submitted = True
            logger.info(f"[AUTO] Clicked submit button/input.")
            submit_attempts += 1
        except Exception as e:
            logger.warning(f"Failed to find and click a generic submit button/input: {e}")

        # Add a short delay after submission attempt
        time.sleep(3)

        # Check for common success indicators (optional but good)
        current_url = driver.current_url
        if submitted and ("success" in current_url or "confirmation" in current_url or "thank-you" in current_url):
            logger.info("[AUTO] Application likely successful (URL changed to success page).")
            return True
        elif submitted:
            logger.info("[AUTO] Application submitted. No clear success page detected, but button was clicked.")
            return True
        else:
            logger.warning("[AUTO] No submit button found or clicked for this page.")
            return False

    except Exception as e:
        logger.error(f"[AUTO ERROR] An error occurred during application for {job.get('url', 'N/A')}: {e}")
        return False

    finally:
        if driver:
            driver.quit()
            logger.info("Chrome driver quit.")
        log_application(job) # Log regardless of Selenium success/failure to at least track attempts

def bot_cycle():
    """Main function to run the job application bot cycle."""
    logger.info("[BOT] Starting job application cycle...")
    applied_urls = load_applied_urls()
    jobs_to_apply = get_jobs()
    
    newly_applied_count = 0
    
    for job in jobs_to_apply:
        if job["url"] not in applied_urls:
            logger.info(f"[BOT] Attempting to apply to: {job.get('title', 'N/A')} at {job.get('company', 'N/A')} - {job.get('url', 'N/A')}")
            success = apply_to_job(job)
            if success:
                newly_applied_count += 1
            applied_urls.add(job["url"]) # Add to set to prevent re-application in same cycle
            time.sleep(5)  # Wait between applications to avoid being blocked
        else:
            logger.info(f"[SKIP] Already applied to: {job.get('url', 'N/A')}")

    logger.info(f"[BOT] Job application cycle finished. Applied to {newly_applied_count} new jobs.")

# --- Flask Routes ---

@app.route('/webhook', methods=['POST'])
def receive_tally():
    data = request.json
    logger.info("[TALLY] Webhook hit from Tally.so")

    try:
        answers = {a['key']: a['value'] for a in data.get("answers", [])}

        # Create resumes directory if it doesn't exist
        if not os.path.exists("resumes"):
            os.makedirs("resumes")

        # Use standardized resume filename
        resume_filename = "resume.pdf" # Standard name
        resume_path = os.path.join("resumes", resume_filename)

        # Build new config from webhook data
        new_keywords = [kw.strip() for kw in answers.get("keywords", "").split(",") if kw.strip()]
        new_user_data = {
            "email": answers.get("email", ""),
            "location": answers.get("location", ""),
            "job_type": answers.get("job_type", ""),
            "full_name": answers.get("full_name", ""),
            "phone": answers.get("phone", ""),
            "cover_letter": answers.get("cover_letter", "") # Assuming you might add a cover letter field in Tally
        }

        # Download the resume file from Tally
        resume_url = ""
        try:
            for answer in data.get("answers", []):
                if answer.get("type") == "file" and answer.get("value"):
                    # Tally file uploads return a list, take the first URL
                    resume_url = answer["value"][0]
                    break

            if resume_url and "localhost" not in resume_url: # Avoid attempting to download local URLs
                logger.info(f"Attempting to download resume from: {resume_url}")
                for attempt in range(3):
                    try:
                        response = requests.get(resume_url, timeout=30)
                        response.raise_for_status() # Check for HTTP errors
                        with open(resume_path, "wb") as f:
                            f.write(response.content)
                        logger.info(f"[TALLY ✅] Resume downloaded to {resume_path}")
                        break # Exit loop on success
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"[TALLY RETRY] Resume download attempt {attempt + 1} failed: {e}")
                        if attempt == 2:
                            logger.error("[TALLY ERROR] Failed to download resume after 3 attempts. Bot will use existing or default resume.")
            else:
                logger.warning("[TALLY] Invalid or missing resume URL from webhook. Bot will use existing or default resume.")

        except Exception as e:
            logger.error(f"[TALLY ERROR] Resume handling failed during download: {e}")

        # Update global config (or create if not exists)
        global config # Indicate we're modifying the global config
        config["timestamp"] = str(datetime.utcnow())
        config["keywords"] = new_keywords
        config["resume_path"] = resume_path # Update to the downloaded path
        config["user_data"] = new_user_data
        
        # Save config to file
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        logger.info("[TALLY] Config updated and saved to config.json.")

        # Launch job application cycle in a separate thread
        threading.Thread(target=bot_cycle, daemon=True).start()
        return "Success", 200

    except Exception as e:
        logger.exception("[TALLY ERROR] An unexpected error occurred in webhook processing.")
        return "Error", 500

@app.route('/applied_jobs')
def show_applied_jobs():
    """
    Displays a table of applied jobs from the CSV_PATH file.
    """
    if not os.path.exists(CSV_PATH):
        return "No jobs applied yet.", 200

    try:
        with open(CSV_PATH, newline="", encoding='utf-8') as f: # Specify encoding for robustness
            reader = csv.reader(f)
            header = next(reader, None)  # Read header row
            jobs_data = list(reader)     # Read remaining rows

        if not header:
            header = ["Timestamp", "Title", "Company", "URL"] # Default header if CSV is empty but exists

        html_table = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Applied Jobs</title>
            <style>
                body {{ font-family: sans-serif; margin: 20px; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; vertical-align: top; }}
                th {{ background-color: #f2f2f2; font-weight: bold; }}
                tr:nth-child(even) {{ background-color: #f9f9f9; }}
                a {{ color: #007bff; text-decoration: none; }}
                a:hover {{ text-decoration: underline; }}
                h1 {{ color: #333; }}
                .button-link {{
                    display: inline-block;
                    padding: 10px 15px;
                    margin-top: 20px;
                    background-color: #28a745;
                    color: white;
                    text-align: center;
                    border-radius: 5px;
                    text-decoration: none;
                    font-weight: bold;
                }}
                .button-link:hover {{
                    background-color: #218838;
                }}
            </style>
        </head>
        <body>
            <h1>Applied Jobs</h1>
            <table>
                <thead>
                    <tr>
                        {"".join(f"<th>{col}</th>" for col in header)}
                    </tr>
                </thead>
                <tbody>
                    {"".join(f"<tr>{''.join(f'<td><a href=\"{item}\" target=\"_blank\">Link</a>' if col_name == 'url' and item.startswith('http') else item for item, col_name in zip(row, header))}</tr>" for row in jobs_data)}
                </tbody>
            </table>
            <a href="/download_applied_jobs" class="button-link">Download CSV</a>
            <p><a href="/">Back to Home</a></p>
        </body>
        </html>
        """
        return render_template_string(html_table), 200
    except Exception as e:
        logger.exception("Error displaying applied_jobs.csv:")
        return f"Error reading applied_jobs.csv: {e}", 500

@app.route('/download_applied_jobs')
def download_applied_jobs():
    """
    Allows users to download the applied_jobs.csv file.
    """
    if not os.path.exists(CSV_PATH):
        return "No jobs applied yet to download.", 404
    try:
        return send_file(CSV_PATH, as_attachment=True, download_name="applied_jobs.csv", mimetype="text/csv")
    except Exception as e:
        logger.exception("Error downloading applied_jobs.csv:")
        return f"Error downloading file: {e}", 500

@app.route('/')
def index():
    """
    The main landing page for the Job Bot Automation, displaying configuration
    details and links to other features.
    """
    current_config = get_current_config() # Ensure we get the absolute latest config
    
    keywords = ", ".join(current_config.get("keywords", []))
    resume_path = current_config.get("resume_path", "Not set")
    last_run = current_config.get("timestamp", "Never")
    
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Job Bot Automation</title>
        <style>
            body {{ font-family: sans-serif; margin: 20px; line-height: 1.6; }}
            h1 {{ color: #333; }}
            h2 {{ color: #555; margin-top: 30px; }}
            p {{ margin-bottom: 10px; }}
            strong {{ font-weight: bold; }}
            .link-section a {{
                display: inline-block;
                padding: 10px 15px;
                margin-right: 10px;
                margin-bottom: 10px;
                background-color: #007bff;
                color: white;
                text-align: center;
                border-radius: 5px;
                text-decoration: none;
                font-weight: bold;
            }}
            .link-section a:hover {{
                background-color: #0056b3;
            }}
        </style>
    </head>
    <body>
        <h1>Job Bot Automation Dashboard</h1>
        <p><strong>Keywords:</strong> {keywords if keywords else "None configured"}</p>
        <p><strong>Resume Path:</strong> {resume_path}</p>
        <p><strong>Last Config Update / Bot Run:</strong> {last_run} UTC</p>
        
        <div class="link-section">
            <a href="/applied_jobs">View Applied Jobs</a>
            <a href="/download_applied_jobs">Download Applied Jobs CSV</a>
        </div>
        
        <h2>How to Update Configuration:</h2>
        <p>Submit your job preferences and resume via the <a href="YOUR_TALLY_FORM_URL_HERE" target="_blank">Tally.so form</a> connected to this bot's webhook.</p>
        <p>Ensure the webhook URL in Tally.so points to <code>YOUR_RENDER_APP_URL/webhook</code></p>
    </body>
    </html>
    """

if __name__ == '__main__':
    # Initial bot cycle can be started here or only via webhook.
    # For Render, it's often better to trigger via webhook or an external scheduler (like cron.yaml on Render)
    # to avoid the bot running every time the web service starts/restarts.
    # Uncomment the line below if you want the bot to start running when the Flask app starts.
    # threading.Thread(target=bot_cycle, daemon=True).start()
    
    # Render sets the PORT environment variable automatically
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_DEBUG') == '1')
    logger.info(f"Flask app running on port {port}")
