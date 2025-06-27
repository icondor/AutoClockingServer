import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import random
import time
import json
import os
import logging
import sqlite3
from datetime import datetime, timedelta
import pytz
import argparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('checkin_simulator.log'),
        logging.StreamHandler()
    ]
)

# Define constants
DEFAULT_SERVER_URL = "http://192.168.50.170:3001/"
romania_tz = pytz.timezone('Europe/Bucharest')


# Load configuration
def load_config(config_file='../config.json'):
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        logging.info(f"Loaded config from {config_file}: {config}")
        return config
    except Exception as e:
        logging.error(f"Error loading config file {config_file}: {e}")
        raise


CONFIG = load_config()


# Load authorized hostnames
def load_authorized_hosts():
    excel_config = CONFIG['excel']
    file_path = excel_config['file_path']
    if not os.path.exists(file_path):
        logging.error(f"Excel file not found: {file_path}")
        return []
    try:
        df = pd.read_excel(file_path, usecols=['Hostname'])
        hostnames = df['Hostname'].tolist()
        logging.info(f"Loaded {len(hostnames)} hostnames from {file_path}")
        return hostnames
    except Exception as e:
        logging.error(f"Error loading Excel file: {e}")
        return []


# Select a percentage of hostnames
def select_simulation_hosts(hostnames, percentage=0.8):
    num_hosts = len(hostnames)
    num_to_select = int(num_hosts * percentage)
    selected_hosts = random.sample(hostnames, num_to_select)
    logging.info(f"Selected {num_to_select} out of {num_hosts} hostnames for simulation ({percentage * 100:.0f}%)")
    return selected_hosts


# Check if a host has checked in
def has_checked_in(hostname, date, db_path):
    try:
        logging.debug(f"Checking check-in for {hostname} on {date} in database {db_path}")
        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=10)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT COUNT(*) FROM checkins WHERE hostname = ? AND date = ?", (hostname, date))
        count = cursor.fetchone()[0]
        conn.close()
        logging.debug(f"Checked {hostname} for {date}: {'exists' if count > 0 else 'not found'}")
        return count > 0
    except sqlite3.Error as e:
        logging.error(f"Error checking check-in status for {hostname} on {date}: {e}")
        return False


# Clear the database
def clear_database(db_path):
    try:
        logging.info(f"Clearing database: {db_path}")
        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=10)
        conn.execute("DELETE FROM checkins")
        conn.commit()
        cursor = conn.execute("SELECT COUNT(*) FROM checkins")
        count = cursor.fetchone()[0]
        conn.close()
        logging.info(f"Database cleared successfully. Current row count: {count}")
    except sqlite3.Error as e:
        logging.error(f"Error clearing database: {e}")
        raise


# Directly insert check-in
def insert_checkin_directly(hostname, checkin_time, date, db_path):
    try:
        checkin_dt = datetime.fromisoformat(checkin_time)
        norma = 8
        random_minutes = random.randint(-20, 40)
        total_minutes = (norma * 60) + random_minutes
        hours_to_add = total_minutes // 60
        minutes_to_add = total_minutes % 60
        checkout_dt = checkin_dt + timedelta(hours=hours_to_add, minutes=minutes_to_add)
        checkout_time = checkout_dt.isoformat()

        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=10)
        conn.execute(
            'INSERT OR IGNORE INTO checkins (hostname, checkin_time, date, checkout_time) VALUES (?, ?, ?, ?)',
            (hostname, checkin_time, date, checkout_time)
        )
        conn.commit()
        count = conn.total_changes
        conn.close()
        if count > 0:
            logging.info(f"Directly inserted check-in for {hostname} on {date}")
            return True
        else:
            logging.debug(f"Skipped direct insert for {hostname} on {date}: already exists")
            return False
    except sqlite3.Error as e:
        logging.error(f"Error inserting check-in for {hostname} on {date}: {e}")
        return False


# Simulate check-in via server
def simulate_checkin(hostname, checkin_time, server_url):
    checkin_url = f"{server_url.rstrip('/')}/checkin"
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
    session.mount('http://', HTTPAdapter(max_retries=retries))

    try:
        payload = {"hostname": hostname, "checkin_time": checkin_time}
        logging.debug(f"Sending check-in payload: {payload}")
        response = session.post(checkin_url, json=payload, timeout=5)
        response.raise_for_status()
        logging.info(
            f"Check-in for {hostname} at {checkin_time} to {checkin_url}: {response.status_code} - {response.json()}")
        return response.status_code
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to check in for {hostname} at {checkin_time} to {checkin_url}: {e}")
        return None


# Simulate check-ins for a specific date
def simulate_checkins_for_date(hostnames, date, server_url, db_path, max_retries=3):
    date_str = date.isoformat()
    logging.info(f"Simulating check-ins for {date_str} via server")
    successful_checkins = 0
    skipped_hosts = 0
    for attempt in range(1, max_retries + 1):
        for hostname in hostnames:
            if has_checked_in(hostname, date_str, db_path):
                logging.debug(f"Skipping {hostname} for {date_str}: already checked in")
                skipped_hosts += 1
                continue
            hour = random.randint(7, 10)
            minute = random.randint(0, 59)
            checkin_dt = romania_tz.localize(datetime(date.year, date.month, date.day, hour, minute))
            status_code = simulate_checkin(hostname, checkin_dt.isoformat(), server_url)
            if status_code == 200:
                successful_checkins += 1
            time.sleep(0.5)
        if successful_checkins > 0 or skipped_hosts == len(hostnames):
            break
        logging.warning(
            f"No successful check-ins for {date_str} (attempt {attempt}/{max_retries}). Retrying after {2 ** attempt} seconds.")
        check_db_state(db_path)
        time.sleep(2 ** attempt)
    logging.info(f"Completed check-ins for {date_str}: {successful_checkins} successful, {skipped_hosts} skipped")
    return successful_checkins, skipped_hosts


# Directly insert check-ins
def insert_checkins_directly(hostnames, date, db_path):
    date_str = date.isoformat()
    logging.info(f"Directly inserting check-ins for {date_str}")
    successful_checkins = 0
    skipped_hosts = 0
    for hostname in hostnames:
        if has_checked_in(hostname, date_str, db_path):
            logging.debug(f"Skipping {hostname} for {date_str}: already checked in")
            skipped_hosts += 1
            continue
        hour = random.randint(7, 10)
        minute = random.randint(0, 59)
        checkin_dt = romania_tz.localize(datetime(date.year, date.month, date.day, hour, minute))
        if insert_checkin_directly(hostname, checkin_dt.isoformat(), date_str, db_path):
            successful_checkins += 1
        time.sleep(0.1)
    logging.info(f"Completed direct inserts for {date_str}: {successful_checkins} successful, {skipped_hosts} skipped")
    return successful_checkins, skipped_hosts


# Check database state
def check_db_state(db_path):
    try:
        logging.info(f"Checking database state: {db_path}")
        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=10)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM checkins")
        result = cursor.fetchone()
        count, min_date, max_date = result['COUNT(*)'], result['MIN(date)'], result['MAX(date)']
        conn.close()
        logging.info(f"DB state: {count} rows, oldest date: {min_date}, newest date: {max_date}")
        return count, min_date, max_date
    except sqlite3.Error as e:
        logging.error(f"Error checking DB state: {e}")
        return None, None, None


# Test PDF generation
def test_pdf_generation(dates, server_url):
    results = []
    for date_str in dates:
        pdf_url = f"{server_url.rstrip('/')}/generate_pdf?date={date_str}"
        try:
            response = requests.get(pdf_url, timeout=5)
            response.raise_for_status()
            logging.info(f"PDF generated for {date_str}: {response.status_code}")
            results.append(True)
        except requests.exceptions.RequestException as e:
            logging.error(f"Error generating PDF for {date_str}: {e}")
            results.append(False)
    return all(results)


# Test email sending
def test_email_sending(dates, server_url):
    results = []
    for date_str in dates:
        email_url = f"{server_url.rstrip('/')}/send_pdf_email"
        try:
            response = requests.post(email_url, json={"date": date_str}, timeout=5)
            response.raise_for_status()
            logging.info(f"Email sent for {date_str}: {response.status_code}")
            results.append(True)
        except requests.exceptions.RequestException as e:
            logging.error(f"Error sending email for {date_str}: {e}")
            results.append(False)
    return all(results)


# Trigger garbage collector
def trigger_garbage_collector(server_url):
    garbage_url = f"{server_url.rstrip('/')}/run_garbage_collector"
    try:
        response = requests.get(garbage_url, timeout=5)
        response.raise_for_status()
        logging.info(f"Garbage collector triggered: {response.status_code} - {response.json()}")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to trigger garbage collector: {e}")
        return False


# Check server availability
def check_server_availability(server_url):
    checkin_url = f"{server_url.rstrip('/')}/checkin"
    try:
        response = requests.post(checkin_url, json={"hostname": "test_availability"}, timeout=5)
        logging.info(f"Server is reachable at {checkin_url}: {response.status_code}")
        return True
    except requests.exceptions.RequestException as e:
        logging.warning(f"Server check failed at {checkin_url}: {e}. Proceeding with simulation.")
        return True


def main():
    parser = argparse.ArgumentParser(description="Simulate check-ins and test PDF creation and cleanup")
    parser.add_argument('--days', type=int, default=50, help='Number of days to simulate (default 50)')
    parser.add_argument('--percentage', type=float, default=0.8, help='Percentage of hosts to select (0.0-1.0)')
    parser.add_argument('--delay', type=float, default=1.0, help='Delay between daily simulations (seconds)')
    parser.add_argument('--server-url', type=str, default=DEFAULT_SERVER_URL, help='Server URL for check-ins')
    parser.add_argument('--clear-db', action='store_true', help='Clear the database before simulation')
    args = parser.parse_args()

    # Check server availability
    if not check_server_availability(args.server_url):
        logging.error("Server not reachable. Exiting.")
        return

    # Load hostnames
    all_hostnames = load_authorized_hosts()
    if not all_hostnames:
        logging.error("No hostnames loaded. Exiting.")
        return

    # Select hosts
    simulation_hosts = select_simulation_hosts(all_hostnames, percentage=args.percentage)
    if not simulation_hosts:
        logging.error("No hostnames selected for simulation. Exiting.")
        return

    # Clear database
    if args.clear_db:
        clear_database(CONFIG['database']['path'])

    # Check initial database state
    logging.info("Checking initial database state")
    initial_count, _, _ = check_db_state(CONFIG['database']['path'])

    # Simulate one day via server
    current_date = datetime.now(romania_tz).date()
    logging.info("Simulating check-ins for today via server")
    server_successful, server_skipped = simulate_checkins_for_date(simulation_hosts, current_date, args.server_url,
                                                                   CONFIG['database']['path'])
    logging.info(f"Server check-ins for {current_date}: {server_successful} successful, {server_skipped} skipped")

    # Directly insert check-ins for future dates
    total_successful_checkins = server_successful
    total_skipped_hosts = server_skipped
    days_processed = 1
    for day_offset in range(args.days - 1):
        target_date = current_date + timedelta(days=day_offset + 1)
        try:
            successful, skipped = insert_checkins_directly(simulation_hosts, target_date, CONFIG['database']['path'])
            total_successful_checkins += successful
            total_skipped_hosts += skipped
            days_processed += 1
            logging.info(f"Processed {days_processed}/{args.days} days")
            if successful == 0 and skipped == len(simulation_hosts):
                logging.info(f"All hosts already checked in for {target_date}. Continuing.")
            elif successful == 0:
                logging.warning(f"No successful direct inserts for {target_date}. Check database.")
                check_db_state(CONFIG['database']['path'])
        except Exception as e:
            logging.error(f"Error inserting check-ins for {target_date}: {e}")
            check_db_state(CONFIG['database']['path'])
            time.sleep(10)
        time.sleep(args.delay)

    logging.info(
        f"Total successful check-ins: {total_successful_checkins}, total skipped: {total_skipped_hosts}, days processed: {days_processed}")

    # Check database state after simulation
    logging.info("Checking database state after simulation")
    count, min_date, max_date = check_db_state(CONFIG['database']['path'])

    # Test PDF generation for sample dates
    test_dates = [
        current_date.isoformat(),
        (current_date + timedelta(days=1)).isoformat(),
        (current_date + timedelta(days=19)).isoformat(),
        (current_date + timedelta(days=args.days - 1)).isoformat()
    ]
    logging.info(f"Testing PDF generation for dates: {test_dates}")
    test_pdf_generation(test_dates, args.server_url)

    # Test email sending
    logging.info(f"Testing email sending for dates: {test_dates}")
    test_email_sending(test_dates, args.server_url)

    # Trigger garbage collector
    if count and count > 10500:
        logging.info("Row count exceeds 10500, triggering garbage collector")
        trigger_garbage_collector(args.server_url)
        logging.info("Checking database state after garbage collection")
        check_db_state(CONFIG['database']['path'])

    # Check PDF directory
    pdf_dir = CONFIG['pdf']['output_dir']
    try:
        pdf_files = [f for f in os.listdir(pdf_dir) if f.endswith('.pdf')]
        logging.info(f"PDF files remaining: {len(pdf_files)}")
    except Exception as e:
        logging.error(f"Error checking PDF directory: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Simulation stopped by user.")
        logging.info("Final database state")
        check_db_state(CONFIG['database']['path'])
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        logging.info("Final database state")
        check_db_state(CONFIG['database']['path'])