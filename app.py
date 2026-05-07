import os
import requests
from bs4 import BeautifulSoup
import json
import re
import time
import urllib.parse
import base64
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from flask import Flask, redirect, request, session, url_for, send_file
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from filter import should_keep_job

# Flask App setup
app = Flask(__name__, template_folder='.')
app.secret_key = os.urandom(24)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'

CLIENT_SECRETS_FILE = "google.json"
SCOPES = ['openid', 'https://www.googleapis.com/auth/userinfo.email', 'https://mail.google.com/']

LOCAL_APP_DATA = os.getenv('LOCALAPPDATA', os.path.expanduser('~\\AppData\\Local'))
DICE_DIR = os.path.join(LOCAL_APP_DATA, 'DICE')
os.makedirs(DICE_DIR, exist_ok=True)
TOKENS_FILE = os.path.join(DICE_DIR, 'tokens.json')
SETTINGS_FILE = os.path.join(DICE_DIR, 'settings.json')
SENT_EMAILS_FILE = os.path.join(DICE_DIR, 'Sent_emails.json')

is_scraping = False

@app.route('/')
def home():
    if 'credentials' not in session:
        if os.path.exists(TOKENS_FILE):
            try:
                with open(TOKENS_FILE, 'r') as f:
                    session['credentials'] = json.load(f)
                return redirect(url_for('dashboard'))
            except:
                pass
        return send_file('login.html')
    return redirect(url_for('dashboard'))

@app.route('/login')
def login_route():
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE, scopes=SCOPES)
        flow.redirect_uri = url_for('oauth2callback', _external=True)
        
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent')
        
        session['state'] = state
        return redirect(authorization_url)
    except Exception as e:
        return f"Error setting up Google Auth. Ensure google-auth-oauthlib is installed: {e}"

@app.route('/oauth2callback')
def oauth2callback():
    from google_auth_oauthlib.flow import Flow
    state = session.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES, state=state)
    flow.redirect_uri = url_for('oauth2callback', _external=True)

    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)

    credentials = flow.credentials
    token_data = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    session['credentials'] = token_data
    
    # Save tokens to LOCAL APP DATA
    with open(TOKENS_FILE, 'w') as f:
        json.dump(token_data, f)
        
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    if 'credentials' not in session:
        return redirect(url_for('home'))
    return send_file('index.html')

@app.route('/logout')
def logout():
    session.clear()
    if os.path.exists(TOKENS_FILE):
        try:
            os.remove(TOKENS_FILE)
        except:
            pass
    return redirect(url_for('home'))

@app.route('/api/save_settings', methods=['POST'])
def save_settings():
    data = request.json
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(data, f)
    return {"status": "success"}

@app.route('/api/get_settings', methods=['GET'])
def get_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

@app.route('/Jobs.json')
def jobs_json():
    return send_file('Jobs.json')

import threading

@app.route('/api/start_scrape', methods=['POST'])
def start_scrape():
    global is_scraping
    if is_scraping:
        return {"status": "error", "message": "Already scraping!"}
    
    t = threading.Thread(target=scrape_dice)
    t.start()
    return {"status": "success", "message": "Scraping started!"}

@app.route('/api/stop_scrape', methods=['POST'])
def stop_scrape():
    global is_scraping
    is_scraping = False
    return {"status": "success", "message": "Stopping scraper..."}

@app.route('/api/status', methods=['GET'])
def get_status():
    global is_scraping
    return {"is_scraping": is_scraping}

def get_est_now():
    """Returns current datetime in EST (UTC-4 for EDT)"""
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=4)

def get_valid_credentials():
    if not os.path.exists(TOKENS_FILE):
        return None
    try:
        with open(TOKENS_FILE, 'r') as f:
            creds_data = json.load(f)

        if not creds_data.get('refresh_token'):
            print("No refresh token found. Please login again.")
            return None

        # Always force a fresh access token using the refresh token
        # This avoids the issue where expiry is not stored and creds.valid is always True
        creds = Credentials(
            token=creds_data['token'],
            refresh_token=creds_data['refresh_token'],
            token_uri=creds_data['token_uri'],
            client_id=creds_data['client_id'],
            client_secret=creds_data['client_secret'],
            scopes=creds_data['scopes']
        )
        print("Refreshing access token...")
        creds.refresh(Request())
        # Save the newly refreshed token back
        creds_data['token'] = creds.token
        with open(TOKENS_FILE, 'w') as f:
            json.dump(creds_data, f)
        return creds
    except Exception as e:
        print("Error loading/refreshing credentials:", e)
        return None


def send_gmail(creds, to_email, reply_to, subject, body_text):
    msg = EmailMessage()
    msg.set_content(body_text)
    msg['To'] = to_email
    msg['Subject'] = subject
    if reply_to:
        msg['Reply-To'] = reply_to

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')
    headers = {
        'Authorization': f'Bearer {creds.token}',
        'Content-Type': 'application/json'
    }
    response = requests.post(
        'https://gmail.googleapis.com/gmail/v1/users/me/messages/send',
        headers=headers,
        json={'raw': raw}
    )
    if response.status_code != 200:
        print(f"  [GMAIL ERROR] Status: {response.status_code}, Message: {response.text}")
    return response.status_code == 200

def load_sent_emails():
    if os.path.exists(SENT_EMAILS_FILE):
        try:
            with open(SENT_EMAILS_FILE, 'r') as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_sent_email(job_url, sent_set):
    sent_set.add(job_url)
    with open(SENT_EMAILS_FILE, 'w') as f:
        json.dump(list(sent_set), f)

def convert_utc_to_est_str(utc_str):
    """Converts a UTC iso string like 2026-05-04T13:07:42.000Z to EST YYYY-MM-DD"""
    if utc_str == "Unknown" or not utc_str:
        return "Unknown", "Unknown"
    try:
        # Parse UTC string (handling optional milliseconds)
        clean_utc = utc_str.split('.')[0].replace("Z", "")
        dt_utc = datetime.strptime(clean_utc, "%Y-%m-%dT%H:%M:%S")
        # Convert to EST
        dt_est = dt_utc - timedelta(hours=4)
        return dt_est.strftime("%Y-%m-%d"), dt_est.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        return "Unknown", "Unknown"

def extract_emails(text):
    """Extract all email addresses from the given text."""
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    raw_emails = list(set(re.findall(email_pattern, text)))
    # Filter out common false positives and dice internal emails
    filtered = [e for e in raw_emails if not e.endswith(('@dice.com', '@sentry.io', '@w3.org')) and 'schema.org' not in e]
    return filtered

def fetch_job_details(job_url):
    """Fetch the individual job description, emails, job type, and date posted."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(job_url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None, [], "Unknown", "Unknown"
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        employment_type = "Unknown"
        date_posted = "Unknown"
        
        schemas = soup.find_all('script', type='application/ld+json')
        for s in schemas:
            try:
                data = json.loads(s.string)
                if isinstance(data, dict) and data.get('@type') == 'JobPosting':
                    emp_type = data.get('employmentType', "Unknown")
                    if isinstance(emp_type, list):
                        employment_type = ", ".join(emp_type)
                    else:
                        employment_type = str(emp_type)
                        
                    date_posted = data.get('datePosted', "Unknown")
                    break
            except:
                pass
        
        # The JD is often in an element whose class name contains 'jobDescription'
        jd_div = soup.find(class_=re.compile("jobDescription", re.I))
        if not jd_div:
            jd_div = soup.find(id="jobDescription")
            
        jd_text = jd_div.get_text(separator="\n", strip=True) if jd_div else ""
        
        if not jd_text:
            # Fallback if specific div is not found
            jd_text = soup.get_text(separator="\n", strip=True)
            
        # Extract emails from the entire HTML response to catch hidden recruiter emails
        emails = extract_emails(response.text)
        return jd_text, emails, employment_type, date_posted
    except Exception as e:
        print(f"Error fetching {job_url}: {e}")
        return "", [], "Unknown", "Unknown"

def scrape_dice():
    global is_scraping
    is_scraping = True
    
    roles = []
    destination_email = ""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
                keywords = settings.get('savedKeywords', '')
                destination_email = settings.get('savedEmail', '').strip()
                is_filter_enabled = settings.get('isFilterEnabled', False)
                title_filters = settings.get('titleFilters', '').strip() if is_filter_enabled else ""
                if keywords:
                    roles = [k.strip() for k in keywords.split(',') if k.strip()]
        except Exception as e:
            print("Error reading settings for roles:", e)
            
    if not roles:
        print("No keywords found in settings. Please enter keywords in the UI and save.")
        is_scraping = False
        return
            
    if not destination_email:
        print("Warning: No Destination Email found in settings. Emails will NOT be sent.")
        
    sent_emails_set = load_sent_emails()
    
    all_jobs = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    today_est_date = get_est_now().strftime("%Y-%m-%d")
    yesterday_est_date = (get_est_now() - timedelta(days=1)).strftime("%Y-%m-%d")
    valid_dates = [today_est_date, yesterday_est_date]
    print(f"Strict filtering for jobs posted exactly on (EST): {valid_dates}")

    for role in roles:
        if not is_scraping:
            break
        print(f"\n--- Scraping jobs for: {role} ---")
        encoded_role = urllib.parse.quote_plus(role)
        
        page = 1
        has_more_pages = True
        seen_urls = set()
        
        while has_more_pages and is_scraping:
            search_url = f"https://www.dice.com/jobs?filters.postedDate=ONE&filters.employmentType=CONTRACTS&q={encoded_role}&radiusUnit=mi&page={page}&pageSize=100&sort=date"
            
            response = requests.get(search_url, headers=headers)
            if response.status_code != 200:
                print(f"Failed to fetch search page {page} for {role}. Status: {response.status_code}")
                break
                
            soup = BeautifulSoup(response.text, "html.parser")
            
            job_links = []
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '/job-detail/' in href:
                    title = a.get_text(strip=True)
                    if title and title not in ["Easy Apply", "Apply Now", "Save"]:
                        full_url = href if href.startswith("http") else f"https://www.dice.com{href}"
                        if full_url not in seen_urls:
                            seen_urls.add(full_url)
                            job_links.append({"title": title, "job_url": full_url})
                            
            if not job_links:
                print(f"No more jobs found on page {page}. Moving to next role.")
                break
                
            print(f"Found {len(job_links)} jobs on page {page} for {role}. Processing...")
            
            for job_info in job_links:
                if not is_scraping:
                    break
                    
                job_url = job_info['job_url']
                job_title = job_info['title']

                # Check Title Filter BEFORE fetching details to save time
                if not should_keep_job(job_title, title_filters):
                    print(f"  [SKIPPED] Title does not match filters: {job_title}")
                    continue
                
                jd_text, emails, employment_type, date_posted_utc = fetch_job_details(job_url)
                
                # Check 1: Must have an email ID
                if not emails:
                    print(f"  [SKIPPED] No emails found in source: {job_info['title']}")
                    continue
                    
                # Check 2: Must be posted TODAY or YESTERDAY in EST
                est_date, est_datetime = convert_utc_to_est_str(date_posted_utc)
                if est_date not in valid_dates:
                    print(f"  [SKIPPED] Not posted today or yesterday (Posted on {est_date} EST): {job_info['title']}")
                    continue
                
                print(f"  [SAVED] {job_info['title']} - {est_datetime} EST - {len(emails)} emails")
                
                job_data = {
                    "search_role": role,
                    "date_posted_est": est_datetime,
                    "job_type": employment_type,
                    "job_title": job_info['title'],
                    "job_url": job_url,
                    "emails_found": emails,
                    "job_description": jd_text
                }
                all_jobs.append(job_data)
                
                # Save to JSON incrementally
                with open("Jobs.json", "w", encoding="utf-8") as f:
                    json.dump(all_jobs, f, indent=4)
                    
                # Send Email functionality
                if destination_email and job_url not in sent_emails_set:
                    # Get fresh credentials inside the loop
                    creds = get_valid_credentials()
                    if not creds:
                        print(f"  [EMAIL FAILED] No valid Google login. Please login again.")
                        continue
                        
                    subject = f"{{DICE}} Role/Tech : {job_info['title']}"
                    body = f"Date Posted : {est_datetime}\nJob Type : {employment_type}\nJob URL : {job_url}\nEmail id : {', '.join(emails)}\n\nTOTAL JD : \n{jd_text}"
                    reply_to = emails[0] if emails else None
                    
                    success = send_gmail(creds, destination_email, reply_to, subject, body)
                    if success:
                        print(f"  [EMAIL SENT] Successfully forwarded to {destination_email}")
                        save_sent_email(job_url, sent_emails_set)
                    else:
                        print(f"  [EMAIL FAILED] Could not send email for {job_url}")
                    
                time.sleep(0.5) # Polite delay
                
            # Usually, Dice returns 20 or 100 jobs per page depending on whether pageSize works.
            # If we got less than 20, we're definitely at the end.
            if len(job_links) < 20:
                has_more_pages = False
            else:
                page += 1
                
    is_scraping = False
    print(f"\nScraping complete. Saved {len(all_jobs)} valid jobs to Jobs.json.")

if __name__ == "__main__":
    print("Starting Flask server on http://localhost:5000")
    print("If you haven't yet, make sure to run: pip install flask google-auth-oauthlib requests")
    app.run(debug=True, port=5000)
