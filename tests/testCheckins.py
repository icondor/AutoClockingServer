import pandas as pd
import requests
import random
import time
import json
import os
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('checkin_simulator.log'),
        logging.StreamHandler()
    ]
)

# Define the default server URL as a constant
DEFAULT_SERVER_URL = "http://localhost:3001/checkin"

# Load configuration (same as your Flask app)
def load_config(config_file='../config.json'):
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
    except Exception as e:
        logging.error(f"Error loading config file {config_file}: {e}")
        raise
    return config

CONFIG = load_config()

# Load authorized hostnames from the Excel file (same as your Flask app)
def load_authorized_hosts():
    excel_config = CONFIG['excel']
    file_path = excel_config['file_path']
    if not os.path.exists(file_path):
        logging.error(f"Excel file not found: {file_path}")
        return []
    try:
        df = pd.read_excel(file_path, usecols=['Hostname'])
        hostnames = df['Hostname'].tolist()
        return hostnames
    except Exception as e:
        logging.error(f"Error loading Excel file: {e}")
        return []

# Select 80% of the hostnames randomly
def select_simulation_hosts(hostnames, percentage=0.8):
    num_hosts = len(hostnames)
    num_to_select = int(num_hosts * percentage)
    selected_hosts = random.sample(hostnames, num_to_select)
    logging.info(f"Selected {num_to_select} out of {num_hosts} hostnames for simulation (80%)")
    return selected_hosts

# Simulate a check-in by sending a POST request to the Flask app
def simulate_checkin(hostname, server_url=None):
    # Use the provided server_url, or fall back to DEFAULT_SERVER_URL
    target_url = server_url if server_url is not None else DEFAULT_SERVER_URL
    try:
        payload = {"hostname": hostname}
        response = requests.post(target_url, json=payload)
        response.raise_for_status()  # Raise an error for bad status codes
        logging.info(f"Check-in for {hostname} to {target_url}: {response.status_code} - {response.json()}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to check in for {hostname} to {target_url}: {e}")

def main():
    # Load all hostnames from the Excel file
    all_hostnames = load_authorized_hosts()
    if not all_hostnames:
        logging.error("No hostnames loaded. Exiting.")
        return

    # Select 80% of the hostnames for simulation
    simulation_hosts = select_simulation_hosts(all_hostnames, percentage=0.9)
    if not simulation_hosts:
        logging.error("No hostnames selected for simulation. Exiting.")
        return

    # Optionally set a custom server URL here
    custom_server_url = "http://clj-devmantools01.global.sdl.corp:3001/checkin"  # Replace with your server URL

    logging.info(f"Starting check-in simulation with {len(simulation_hosts)} hostnames...")
    try:
        while True:
            # Randomly pick a hostname from the selected subset
            hostname = random.choice(simulation_hosts)
            # Simulate a check-in using the custom URL (or default if custom is None)
            simulate_checkin(hostname, server_url=custom_server_url)
            # Wait for 1 second before the next check-in
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Simulation stopped by user.")

if __name__ == "__main__":
    main()