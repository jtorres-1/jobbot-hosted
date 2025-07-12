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
from datetime import datetime
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
    config = {"keywords": [], "max_results": 50, "resume_path": "resumes/default_resume.pdf", "user_data": {}}
    logger.warning(f"Config file {CONFIG_FILE} not found. Initialized with default config.")
except json.JSONDecodeError:
    config = {"keywords": [], "max_results": 50, "resume_path": "resumes/default_resume.pdf", "user_data": {}}
    logger.error(f"Error decoding {CONFIG_FILE}. Initialized with default config.")
except Exception as e:
    config = {"keywords": [], "max_results": 50, "resume_path": "resumes/default_resume.pdf", "user_data": {}}
    logger.error(f"An unexpected error occurred loading config: {e}. Initialized with default config.")

# Path for applied jobs CSV
CSV_PATH = "applied_jobs.csv"

# Ensure resumes directory exists
if not os.path.exists("resumes"):
    os.makedirs("resumes")
    logger.info("Created 'resumes' directory.")

# Create a dummy default resume if it doesn't exist
DEFAULT_RESUME_PATH = "resumes/default_resume.pdf"
if not os.path.exists(DEFAULT_RESUME_PATH):
    try:
        from reportlab.pdfgen import canvas
        c = canvas.Canvas(DEFAULT_RESUME_PATH)
        c.drawString(100, 750, "This is a default resume. Please upload your own!")
        c.save()
        logger.info(f"Created a dummy default resume at {DEFAULT_RESUME_PATH}.")
    except ImportError:
        logger.warning(f"reportlab not installed. Please manually create a dummy PDF at {DEFAULT_RESUME_PATH} if you don't upload a resume via Tally.")
    except Exception as e:
        logger.error(f"Failed to create default resume PDF: {e}")


# --- Helper Functions ---

def get_current_config():
    """Load current config from file"""
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load config.json: {e}. Returning default config.")
        return {"keywords": [], "max_results": 50, "resume_path": DEFAULT_RESUME_PATH, "user_data": {}}

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

# --- Core Request/BeautifulSoup Helper ---

def _make_request(url, headers=None, timeout=15):
    """Helper to make robust HTTP requests with default User-Agent header."""
    if headers is None:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Connection': 'keep-alive',
        }
    try:
        session = requests.Session()
        response = session.get(url, headers=headers, timeout=timeout)
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
        return BeautifulSoup(response.content, 'html.parser')
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed for {url}: {e}")
        return None

# --- New Requests + BeautifulSoup Scrapers ---

def scrape_jobicy(keyword, location):
    """Scrape Jobicy for remote jobs using requests + BeautifulSoup."""
    jobs = []
    # Jobicy is primarily remote, so location might be ignored or used in keyword search
    query = f"{keyword} {location}".strip() if location and location.lower() != "remote" else keyword
    if not query:
        logger.warning("[SCRAPE] Jobicy: No keywords provided. Skipping.")
        return jobs
    
    encoded_query = urllib.parse.quote_plus(query)
    url = f"https://jobicy.com/jobs?q={encoded_query}"
    
    logger.info(f"[SCRAPE] Scraping Jobicy for '{keyword}' in '{location}'...")
    
    soup = _make_request(url)
    if not soup:
        logger.warning(f"[SCRAPE] Jobicy returned 0 jobs (request failed or page not found).")
        return jobs

    job_cards = soup.find_all('div', class_='job-card') # This selector might need adjustment
    
    for card in job_cards:
        try:
            title_elem = card.find('h2', class_='job-card__title')
            title = title_elem.text.strip() if title_elem else 'N/A'
            
            company_elem = card.find('p', class_='job-card__company') # Adjusted based on typical structure
            company = company_elem.text.strip() if company_elem else 'N/A'
            
            # Jobicy is remote-focused, so location is often implicitly remote
            location_text = "Remote" 
            
            link_elem = card.find('a', href=True)
            job_url = link_elem['href'] if link_elem else 'N/A'
            if job_url != 'N/A' and not job_url.startswith('http'):
                job_url = f"https://jobicy.com{job_url}" # Ensure absolute URL

            jobs.append({
                'title': title,
                'company': company,
                'location': location_text, 
                'url': job_url
            })
        except Exception as e:
            logger.debug(f"Error parsing Jobicy job card: {e}")
            continue
    logger.info(f"[SCRAPE] Jobicy returned {len(jobs)} jobs.")
    return jobs

def scrape_jooble(keyword, location):
    """Scrape Jooble for jobs using requests + BeautifulSoup."""
    jobs = []
    query = f"{keyword} {location}".strip()
    if not query:
        logger.warning("[SCRAPE] Jooble: No keywords provided. Skipping.")
        return jobs

    encoded_query = urllib.parse.quote_plus(query)
    # Jooble URL structure for search; 'pn' is page number
    url = f"https://us.jooble.org/jobs-{encoded_query}"
    
    logger.info(f"[SCRAPE] Scraping Jooble for '{keyword}' in '{location}'...")
    
    # Jooble often requires pagination to get more results. Let's try first page.
    # To implement more pages, you'd loop through `pn` parameter.
    
    soup = _make_request(url)
    if not soup:
        logger.warning(f"[SCRAPE] Jooble returned 0 jobs (request failed or page not found).")
        return jobs

    # Inspect Jooble's HTML for job listing containers
    # Common selectors for job cards on job boards: 'div.job-item', 'article.job-card', 'li.job-listing'
    job_cards = soup.find_all('article', class_='job-card') # This selector might need adjustment
    
    for card in job_cards:
        try:
            title_elem = card.find('a', class_='job-card__title-link') # Adjusted based on typical structure
            title = title_elem.text.strip() if title_elem else 'N/A'
            job_url = title_elem['href'] if title_elem and title_elem.get('href') else 'N/A'
            if job_url != 'N/A' and not job_url.startswith('http'):
                job_url = f"https://us.jooble.org{job_url}" # Ensure absolute URL
            
            company_elem = card.find('p', class_='job-card__company') # Adjusted based on typical structure
            company = company_elem.text.strip() if company_elem else 'N/A'
            
            location_elem = card.find('p', class_='job-card__location') # Adjusted based on typical structure
            location_text = location_elem.text.strip() if location_elem else 'N/A'
            
            jobs.append({
                'title': title,
                'company': company,
                'location': location_text, 
                'url': job_url
            })
        except Exception as e:
            logger.debug(f"Error parsing Jooble job card: {e}")
            continue
    logger.info(f"[SCRAPE] Jooble returned {len(jobs)} jobs.")
    return jobs

def scrape_careerpage(keyword, location):
    """Scrape Careerpage.co for jobs using requests + BeautifulSoup."""
    jobs = []
    # Careerpage.co often hosts specific company career pages, so a broad search might be difficult.
    # It might be more effective to search for specific company career pages on careerpage.co if known.
    # For a general search, we'll try to use their search functionality.
    query = f"{keyword} {location}".strip()
    if not query:
        logger.warning("[SCRAPE] Careerpage.co: No keywords provided. Skipping.")
        return jobs
    
    encoded_query = urllib.parse.quote_plus(query)
    # The search URL for careerpage.co can vary significantly based on their internal structure.
    # This is a generic attempt, may not work for all instances.
    url = f"https://www.careerpage.co/jobs?q={encoded_query}" 
    
    logger.info(f"[SCRAPE] Scraping Careerpage.co for '{keyword}' in '{location}'...")
    
    soup = _make_request(url)
    if not soup:
        logger.warning(f"[SCRAPE] Careerpage.co returned 0 jobs (request failed or page not found).")
        return jobs

    # Inspect Careerpage.co HTML for job listing containers. This is highly variable.
    job_cards = soup.find_all('div', class_='job-listing-card') # This selector is a guess and will likely need adjustment
    
    if not job_cards:
        # Fallback to more generic search if specific card class not found
        job_cards = soup.find_all('a', class_='job-link') # Another common pattern
        
    for card in job_cards:
        try:
            # Assuming the card itself or an anchor within it holds the main info
            title_elem = card.find('h3', class_='job-title') or card.find('h2') or card.find('span', class_='title')
            title = title_elem.text.strip() if title_elem else 'N/A'
            
            company_elem = card.find('span', class_='company-name') or card.find('div', class_='company')
            company = company_elem.text.strip() if company_elem else 'N/A'
            
            location_elem = card.find('span', class_='job-location') or card.find('div', class_='location')
            location_text = location_elem.text.strip() if location_elem else 'N/A'
            
            job_url = card.get('href') if card.name == 'a' else card.find('a', href=True).get('href')
            if not job_url:
                job_url = 'N/A'
            elif not job_url.startswith('http'):
                job_url = f"https://www.careerpage.co{job_url}" # Ensure absolute URL

            jobs.append({
                'title': title,
                'company': company,
                'location': location_text, 
                'url': job_url
            })
        except Exception as e:
            logger.debug(f"Error parsing Careerpage.co job card: {e}")
            continue
    logger.info(f"[SCRAPE] Careerpage.co returned {len(jobs)} jobs.")
    return jobs


def scrape_workable(keyword, location):
    """Scrape Workable.com for jobs using requests + BeautifulSoup."""
    jobs = []
    query = f"{keyword}" # Workable's public search often emphasizes keywords, location can be an issue.
    # Workable is a platform for companies, so individual company career pages hosted on Workable are common.
    # A generic search on Workable.com might be limited. We'll attempt a global search if available.
    
    if not query:
        logger.warning("[SCRAPE] Workable: No keywords provided. Skipping.")
        return jobs

    encoded_keyword = urllib.parse.quote_plus(keyword)
    encoded_location = urllib.parse.quote_plus(location)
    # Workable's main jobs page has a search, but it can often redirect to company-specific pages.
    # This URL is a common pattern for their aggregate search.
    url = f"https://www.workable.com/job-search?query={encoded_keyword}&location={encoded_location}"
    
    logger.info(f"[SCRAPE] Scraping Workable.com for '{keyword}' in '{location}'...")
    
    soup = _make_request(url)
    if not soup:
        logger.warning(f"[SCRAPE] Workable returned 0 jobs (request failed or page not found).")
        return jobs

    # Inspect Workable's HTML for job listing containers. This is also highly variable.
    job_cards = soup.find_all('li', class_='job-card') # This selector is a guess
    
    for card in job_cards:
        try:
            title_elem = card.find('h2', class_='job-title') or card.find('a', class_='job-link-title')
            title = title_elem.text.strip() if title_elem else 'N/A'
            
            company_elem = card.find('span', class_='company-name') or card.find('div', class_='company')
            company = company_elem.text.strip() if company_elem else 'N/A'
            
            location_elem = card.find('span', class_='job-location') or card.find('div', class_='location')
            location_text = location_elem.text.strip() if location_elem else 'N/A'
            
            link_elem = card.find('a', href=True)
            job_url = link_elem['href'] if link_elem else 'N/A'
            if job_url != 'N/A' and not job_url.startswith('http'):
                job_url = f"https://www.workable.com{job_url}" # Ensure absolute URL

            jobs.append({
                'title': title,
                'company': company,
                'location': location_text, 
                'url': job_url
            })
        except Exception as e:
            logger.debug(f"Error parsing Workable job card: {e}")
            continue
    logger.info(f"[SCRAPE] Workable returned {len(jobs)} jobs.")
    return jobs

def scrape_lensa(keyword, location):
    """Scrape Lensa.com for jobs using requests + BeautifulSoup."""
    jobs = []
    # Lensa's search allows for keywords and location
    query = f"{keyword}"
    if not query:
        logger.warning("[SCRAPE] Lensa: No keywords provided. Skipping.")
        return jobs
    
    encoded_keyword = urllib.parse.quote_plus(keyword)
    encoded_location = urllib.parse.quote_plus(location)
    
    url = f"https://lensa.com/job-search/{encoded_keyword}-jobs-in-{encoded_location}"
    
    logger.info(f"[SCRAPE] Scraping Lensa.com for '{keyword}' in '{location}'...")
    
    soup = _make_request(url)
    if not soup:
        logger.warning(f"[SCRAPE] Lensa returned 0 jobs (request failed or page not found).")
        return jobs

    # Inspect Lensa's HTML for job listing containers.
    job_cards = soup.find_all('div', class_='job-listing-card') # This selector is a guess and will likely need adjustment
    
    for card in job_cards:
        try:
            title_elem = card.find('h2', class_='job-title') or card.find('a', class_='job-title-link')
            title = title_elem.text.strip() if title_elem else 'N/A'
            
            company_elem = card.find('p', class_='company-name') or card.find('span', class_='company')
            company = company_elem.text.strip() if company_elem else 'N/A'
            
            location_elem = card.find('span', class_='location') or card.find('div', class_='job-location')
            location_text = location_elem.text.strip() if location_elem else 'N/A'
            
            link_elem = card.find('a', href=True)
            job_url = link_elem['href'] if link_elem else 'N/A'
            if job_url != 'N/A' and not job_url.startswith('http'):
                job_url = f"https://lensa.com{job_url}" # Ensure absolute URL

            jobs.append({
                'title': title,
                'company': company,
                'location': location_text, 
                'url': job_url
            })
        except Exception as e:
            logger.debug(f"Error parsing Lensa job card: {e}")
            continue
    logger.info(f"[SCRAPE] Lensa returned {len(jobs)} jobs.")
    return jobs

# --- Original requests-based scrapers (Kept for completeness, can be removed if desired) ---

def scrape_remoteok():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    logger.info("[SCRAPE] RemoteOK...")
    url = "https://remoteok.io/remote-dev-jobs"
    jobs = []
    
    soup = _make_request(url)
    if not soup:
        logger.warning(f"[SCRAPE] RemoteOK returned 0 jobs (request failed).")
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
                jobs.append({"url": full_url, "title": title, "company": company, "location": "Remote"})
        except Exception as e:
            logger.warning(f"Error parsing RemoteOK job entry: {e}")
            continue
    logger.info(f"[SCRAPE] RemoteOK returned {len(jobs)} jobs.")
    return jobs

def scrape_flexjobs():
    config = get_current_config()
    keywords = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    logger.info("[SCRAPE] FlexJobs...")
    url = "https://www.flexjobs.com/remote-jobs/developer"
    jobs = []
    
    soup = _make_request(url)
    if not soup:
        logger.warning(f"[SCRAPE] FlexJobs returned 0 jobs (request failed).")
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
                jobs.append({"url": href, "title": title, "company": company_name, "location": "Remote"})
        except Exception as e:
            logger.warning(f"Error parsing FlexJobs job entry: {e}")
            continue
    logger.info(f"[SCRAPE] FlexJobs returned {len(jobs)} jobs.")
    return jobs

def scrape_wellfound(keywords):
    """Scrape Wellfound (formerly AngelList) for remote jobs using requests."""
    jobs = []
    query = '+'.join(keywords)
    url = f"https://wellfound.com/jobs?q={urllib.parse.quote(query)}&location=Remote"
    
    soup = _make_request(url)
    if not soup:
        logger.warning(f"[SCRAPE] Wellfound returned 0 jobs (request failed).")
        return jobs

    job_listings = soup.find_all('div', class_='job-listing') # Adjust selector
    for listing in job_listings:
        try:
            title_elem = listing.find('h2', class_='job-title') # Adjust selector
            title = title_elem.text.strip() if title_elem else 'N/A'

            company_elem = listing.find('div', class_='company-name') # Adjust selector
            company = company_elem.text.strip() if company_elem else 'N/A'

            link_elem = listing.find('a', class_='job-link') # Adjust selector
            job_url = link_elem['href'] if link_elem and link_elem.get('href') else 'N/A'
            if job_url != 'N/A' and not job_url.startswith('http'):
                job_url = f"https://wellfound.com{job_url}"

            jobs.append({
                'title': title,
                'company': company,
                'location': 'Remote',
                'url': job_url
            })
        except Exception as e:
            logger.debug(f"Error parsing Wellfound job listing: {e}")
            continue
    logger.info(f"[SCRAPE] Wellfound returned {len(jobs)} jobs.")
    return jobs

def scrape_powertofly(keywords):
    """Scrape PowerToFly for remote jobs using requests."""
    jobs = []
    query = '+'.join(keywords)
    url = f"https://powertofly.com/jobs?query={urllib.parse.quote(query)}&is_remote=true"
    
    soup = _make_request(url)
    if not soup:
        logger.warning(f"[SCRAPE] PowerToFly returned 0 jobs (request failed).")
        return jobs

    job_cards = soup.find_all('div', class_='job-card') # Adjust selector
    for card in job_cards:
        try:
            title_elem = card.find('h3', class_='job-card-title') # Adjust selector
            title = title_elem.text.strip() if title_elem else 'N/A'

            company_elem = card.find('div', class_='job-card-company') # Adjust selector
            company = company_elem.text.strip() if company_elem else 'N/A'

            link_elem = card.find('a', class_='job-card-link') # Adjust selector
            job_url = link_elem['href'] if link_elem and link_elem.get('href') else 'N/A'
            if job_url != 'N/A' and not job_url.startswith('http'):
                job_url = f"https://powertofly.com{job_url}"

            jobs.append({
                'title': title,
                'company': company,
                'location': 'Remote',
                'url': job_url
            })
        except Exception as e:
            logger.debug(f"Error parsing PowerToFly job card: {e}")
            continue
    logger.info(f"[SCRAPE] PowerToFly returned {len(jobs)} jobs.")
    return jobs


def get_jobs():
    """Aggregates jobs from all defined scrapers."""
    config = get_current_config()
    keywords_from_config = [kw.lower().strip() for kw in config.get("keywords", []) if kw.strip()]
    max_results = config.get("max_results", 50)
    
    all_jobs = []
    
    # Define location for location-specific scrapers (can be made dynamic from Tally form)
    location_param = config.get("user_data", {}).get("location", "United States") 

    scrapers = [
        # New requests+BeautifulSoup scrapers
        lambda: scrape_jobicy(" ".join(keywords_from_config), location_param),
        lambda: scrape_jooble(" ".join(keywords_from_config), location_param),
        lambda: scrape_careerpage(" ".join(keywords_from_config), location_param),
        lambda: scrape_workable(" ".join(keywords_from_config), location_param),
        lambda: scrape_lensa(" ".join(keywords_from_config), location_param),

        # Existing requests-based scrapers (kept if not explicitly asked to remove)
        lambda: scrape_remoteok(), 
        lambda: scrape_flexjobs(),
        lambda: scrape_wellfound(keywords_from_config), 
        lambda: scrape_powertofly(keywords_from_config)
    ]
    
    for fn in scrapers:
        try:
            jobs = fn()
            all_jobs.extend(jobs)
        except Exception as e:
            logger.error(f"[SCRAPE ERROR] {fn.__name__}: {e}")
        time.sleep(2)  # Delay between scrapers to be polite and avoid hammering sites

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

# --- Flask Routes (No changes needed for these, they interact with the config and scraper output) ---

def apply_to_job(job):
    """
    Placeholder for actual application logic.
    For requests-based scraping, direct application might be complex or impossible
    without knowing the exact form structure of the external job URL.
    This function currently logs the attempt and always returns False for "success"
    since it's not actually submitting forms. If you want to build automated
    applications for specific sites, you'd need to inspect each job URL
    and write specific `requests.post` or `requests.put` logic.
    """
    config = get_current_config()
    user_data = config.get("user_data", {})
    resume_path = config.get("resume_path", DEFAULT_RESUME_PATH)

    logger.info(f"[AUTO] Attempting to 'apply' (log only) to → {job.get('url', 'N/A')}")

    if not os.path.exists(resume_path):
        logger.error(f"[AUTO ERROR] Resume file not found at: {resume_path}. Cannot truly apply.")
        log_application(job) # Log attempt even if no resume
        return False

    # This part of the code would traditionally contain Selenium
    # to navigate and fill out application forms.
    # Since we are explicitly NOT using Selenium, this function
    # cannot actually "apply" in the sense of filling out forms on external sites.
    # It will only log the *attempt* to apply.

    logger.warning(f"[AUTO] Actual form submission not possible without browser automation (Selenium) or specific API knowledge for {job.get('url', 'N/A')}. Logging as attempted.")
    
    log_application(job) # Log the attempt
    return False # Indicate that a true application was not performed

def bot_cycle():
    """Main function to run the job application bot cycle."""
    logger.info("[BOT] Starting job application cycle...")
    applied_urls = load_applied_urls()
    jobs_to_apply = get_jobs()
    
    newly_applied_count = 0
    
    for job in jobs_to_apply:
        if job["url"] not in applied_urls:
            logger.info(f"[BOT] Considering job for 'application': {job.get('title', 'N/A')} at {job.get('company', 'N/A')} - {job.get('url', 'N/A')}")
            # The apply_to_job function now only logs and returns False for actual submission
            success = apply_to_job(job) 
            if success: # This will currently always be False
                newly_applied_count += 1
            applied_urls.add(job["url"]) # Add to set to prevent re-application in same cycle
            time.sleep(5)  # Wait between "applications" (logging attempts)
        else:
            logger.info(f"[SKIP] Already logged or applied to: {job.get('url', 'N/A')}")

    logger.info(f"[BOT] Job application cycle finished. Attempted {newly_applied_count} new job logs (no actual submissions).")


@app.route('/webhook', methods=['POST'])
def receive_tally():
    data = request.json
    logger.info("[TALLY] Webhook hit from Tally.so")
    logger.debug(f"[TALLY DEBUG] Raw incoming data: {json.dumps(data, indent=2)}")

    try:
        # Tally webhook data structure: answers is a list of dicts.
        # Each dict has 'key' (the field ID/name) and 'value'.
        # We need to map these to more readable names and handle potential data types.
        
        parsed_answers = {}
        for item in data.get("answers", []):
            field_key = item.get("key")
            field_value = item.get("value")
            field_type = item.get("type") # Useful for knowing if it's a file, text, etc.
            
            logger.debug(f"[TALLY DEBUG] Processing field: key={field_key}, value={field_value}, type={field_type}")

            if field_key:
                parsed_answers[field_key] = field_value

        logger.debug(f"[TALLY DEBUG] Parsed answers dictionary: {json.dumps(parsed_answers, indent=2)}")

        # Extract specific fields using .get() for safety
        # Ensure these 'keys' match the actual 'keys' or 'ids' in your Tally form fields.
        # For example, if your keywords field in Tally is named 'keywords_input', use that.
        
        # Keywords: Expecting a comma-separated string, convert to list
        keywords_raw = parsed_answers.get("keywords", "") # Assumed Tally field key is 'keywords'
        new_keywords = [kw.strip() for kw in keywords_raw.split(",") if kw.strip()]
        logger.info(f"[TALLY] Extracted Keywords: {new_keywords}")

        # Resume URL: Tally file uploads return a list, take the first URL
        resume_url = ""
        # Look for a field explicitly named 'resume' or 'resume_file' or similar.
        # Alternatively, iterate through answers again, checking 'type' == 'file'.
        for answer_item in data.get("answers", []):
            if answer_item.get("type") == "file" and answer_item.get("value"):
                if isinstance(answer_item["value"], list) and answer_item["value"]:
                    resume_url = answer_item["value"][0]
                else:
                    resume_url = answer_item["value"] # Handle single string case
                logger.info(f"[TALLY] Extracted Resume URL (raw): {resume_url}")
                break # Assuming only one resume file
        
        # Location, Job Type, and other user data
        new_user_data = {
            "email": parsed_answers.get("email", ""),         # Assumed Tally field key is 'email'
            "location": parsed_answers.get("location", ""),   # Assumed Tally field key is 'location'
            "job_type": parsed_answers.get("job_type", ""),   # Assumed Tally field key is 'job_type'
            "full_name": parsed_answers.get("full_name", ""), # Assumed Tally field key is 'full_name'
            "phone": parsed_answers.get("phone", ""),         # Assumed Tally field key is 'phone'
            "cover_letter": parsed_answers.get("cover_letter", "") # Assumed Tally field key is 'cover_letter'
        }
        logger.info(f"[TALLY] Extracted User Data: {new_user_data}")

        # Ensure resumes directory exists
        if not os.path.exists("resumes"):
            os.makedirs("resumes")
            logger.info("Created 'resumes' directory.")

        # Determine the target resume path
        current_resume_path = os.path.join("resumes", "resume.pdf")

        # Download the resume file from Tally
        if resume_url and "localhost" not in resume_url: # Avoid attempting to download local URLs
            logger.info(f"Attempting to download resume from: {resume_url}")
            download_success = False
            for attempt in range(3):
                try:
                    response = requests.get(resume_url, timeout=30)
                    response.raise_for_status() # Check for HTTP errors
                    with open(current_resume_path, "wb") as f:
                        f.write(response.content)
                    logger.info(f"[TALLY ✅] Resume downloaded to {current_resume_path}")
                    download_success = True
                    break # Exit loop on success
                except requests.exceptions.RequestException as e:
                    logger.warning(f"[TALLY RETRY] Resume download attempt {attempt + 1} failed: {e}")
            if not download_success:
                logger.warning(f"[TALLY] Falling back to default resume due to download failure.")
                current_resume_path = DEFAULT_RESUME_PATH
        else:
            logger.warning(f"[TALLY] No valid resume URL from webhook or URL is local. Falling back to default resume.")
            current_resume_path = DEFAULT_RESUME_PATH

        # Update global config (or create if not exists)
        global config # Indicate we're modifying the global config
        config["timestamp"] = str(datetime.utcnow())
        config["keywords"] = new_keywords
        config["resume_path"] = current_resume_path # Update to the *chosen* resume path
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

# --- Web Interface Routes ---

# Simple HTML template for the UI
UI_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Job Application Bot</title>
    <style>
        body { font-family: sans-serif; margin: 2em; background-color: #f4f4f4; color: #333; }
        .container { max-width: 800px; margin: 0 auto; background-color: #fff; padding: 2em; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1, h2 { color: #0056b3; }
        pre { background-color: #eee; padding: 1em; border-radius: 4px; overflow-x: auto; }
        .button {
            display: inline-block;
            padding: 10px 20px;
            margin-top: 1em;
            background-color: #007bff;
            color: white;
            text-decoration: none;
            border-radius: 5px;
            transition: background-color 0.3s ease;
            border: none;
            cursor: pointer;
        }
        .button:hover { background-color: #0056b3; }
        .job-list { list-style: none; padding: 0; }
        .job-item { background-color: #e9e9e9; margin-bottom: 0.5em; padding: 1em; border-radius: 4px; }
        .job-item a { color: #007bff; text-decoration: none; }
        .job-item a:hover { text-decoration: underline; }
        .success { color: green; }
        .error { color: red; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Job Application Bot Control Panel</h1>

        <h2>Current Configuration</h2>
        <pre>{{ config_json }}</pre>

        <h2>Latest Applied Jobs</h2>
        <pre>{{ applied_jobs_csv }}</pre>

        <form action="/run_bot" method="post">
            <button type="submit" class="button">Run Bot Now</button>
        </form>
        <p>This will start the job search and (attempted) application process based on the current configuration.</p>

        <h2>Tally.so Webhook URL:</h2>
        <p>Set this URL in your Tally.so form's Webhooks integration settings to receive submissions:</p>
        <pre>{{ webhook_url }}</pre>
        <p>Remember to match your Tally form field "Keys" (IDs) to what the webhook expects (e.g., 'keywords', 'location', 'resume', 'email', 'full_name', 'phone', 'cover_letter').</p>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    current_config = get_current_config()
    config_json = json.dumps(current_config, indent=2)

    applied_jobs_data = ""
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, 'r') as f:
            applied_jobs_data = f.read()
    else:
        applied_jobs_data = "No applied jobs data available yet."

    # Get the base URL for the webhook
    # In a production environment, this would be your public domain.
    # For local testing, it might be localhost or ngrok URL.
    webhook_base_url = request.url_root.rstrip('/')
    webhook_url = f"{webhook_base_url}/webhook"

    return render_template_string(
        UI_HTML,
        config_json=config_json,
        applied_jobs_csv=applied_jobs_data,
        webhook_url=webhook_url
    )

@app.route('/run_bot', methods=['POST'])
def run_bot_endpoint():
    logger.info("[UI] Manual bot run requested via UI.")
    threading.Thread(target=bot_cycle, daemon=True).start()
    return "Bot cycle initiated. Check server logs for progress.", 202

@app.route('/download_resume')
def download_resume():
    config = get_current_config()
    resume_path = config.get("resume_path", DEFAULT_RESUME_PATH)
    if os.path.exists(resume_path):
        return send_file(resume_path, as_attachment=True, download_name="current_resume.pdf")
    return "Resume not found.", 404

# --- Main execution ---
if __name__ == '__main__':
    # You might want to run this with gunicorn in production
    # For development, this is fine
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False) 
    # use_reloader=False because the bot_cycle runs in a separate thread
    # and reloader might cause issues.
