import os
import logging
import logging.handlers
import json
import sqlite3
import pandas as pd
from flask import Flask, request, jsonify, g
from flask_babel import Babel, gettext
from datetime import datetime, date, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import pytz
import atexit

# Time Zone Handling for Romania (Europe/Bucharest)
romania_tz = pytz.timezone('Europe/Bucharest')


# Load configuration from JSON file
def load_config(config_file='config.json'):
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
    except Exception as e:
        logging.error(f"Error loading config file {config_file}: {e}")
        raise

    # Override with environment variables if present
    config['server']['host'] = os.getenv('SERVER_HOST', config['server']['host'])
    config['server']['port'] = int(os.getenv('SERVER_PORT', config['server']['port']))
    config['server']['debug'] = os.getenv('SERVER_DEBUG', str(config['server']['debug'])).lower() == 'true'
    config['database']['path'] = os.getenv('DB_PATH', config['database']['path'])
    config['excel']['file_path'] = os.getenv('EXCEL_FILE_PATH', config['excel']['file_path'])
    config['pdf']['output_dir'] = os.getenv('PDF_OUTPUT_DIR', config['pdf']['output_dir'])
    config['logging']['file'] = os.getenv('LOG_FILE', config['logging']['file'])
    return config


CONFIG = load_config()

app = Flask(__name__)
app.config['BABEL_DEFAULT_LOCALE'] = 'ro'
babel = Babel(app)


def get_current_time_in_romania():
    return datetime.now(romania_tz)


def get_today_in_romania():
    return datetime.now(romania_tz).date()


def setup_logging():
    log_config = CONFIG['logging']
    log_file = log_config['file']
    max_log_size = log_config['max_size_mb'] * 1024 * 1024
    backup_count = log_config['backup_count']
    log_level = getattr(logging, log_config['level'].upper(), logging.INFO)

    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=max_log_size, backupCount=backup_count)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logging.getLogger().setLevel(log_level)
    logging.getLogger().addHandler(handler)
    logging.info("Logging initialized successfully.")


def get_db_connection():
    if not hasattr(g, 'db_connection'):
        db_path = os.path.abspath(CONFIG['database']['path'])
        logging.info(f"Creating new database connection for thread at: {db_path}")
        g.db_connection = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db_connection.row_factory = sqlite3.Row
    return g.db_connection


@app.teardown_appcontext
def close_db_connection(exception):
    if hasattr(g, 'db_connection'):
        g.db_connection.close()
        logging.info("Database connection closed for thread.")


def init_db():
    with app.app_context():
        with get_db_connection() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS checkins (
                hostname TEXT NOT NULL,
                checkin_time TEXT NOT NULL,
                date TEXT NOT NULL,
                PRIMARY KEY (hostname, date)
            )''')
            conn.commit()
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='checkins'")
            if cursor.fetchone():
                logging.info("Checkins table exists")
            else:
                logging.error("Failed to create checkins table")


def load_authorized_hosts():
    excel_config = CONFIG['excel']
    file_path = excel_config['file_path']
    if not os.path.exists(file_path):
        logging.error(f"Excel file not found: {file_path}")
        return {}
    try:
        df = pd.read_excel(file_path, usecols=['Hostname', 'Nume ', 'Norma'])
        return {row['Hostname']: {'name': row['Nume '].strip(), 'work_hours': row['Norma']} for _, row in df.iterrows()}
    except Exception as e:
        logging.error(f"Error loading Excel file: {e}")
        return {}


def load_daily_checkins():
    try:
        with get_db_connection() as conn:
            rows = conn.execute('SELECT hostname, checkin_time, date FROM checkins').fetchall()
        return {row['date']: {row['hostname']: row['checkin_time']} for row in rows}
    except sqlite3.Error as e:
        logging.error(f"Error loading daily checkins from database: {e}")
        return {}


def save_checkin(hostname, checkin_time, today):
    try:
        datetime.strptime(today, '%Y-%m-%d')
    except ValueError:
        logging.error(f"Invalid date format for {today}")
        raise ValueError("Date must be in YYYY-MM-DD format")

    try:
        with get_db_connection() as conn:
            conn.execute('INSERT OR IGNORE INTO checkins (hostname, checkin_time, date) VALUES (?, ?, ?)',
                         (hostname, checkin_time, today))
            conn.commit()
            if conn.total_changes > 0:
                logging.info(f"Successfully saved checkin for {hostname}")
                return True
            else:
                logging.info(f"{hostname} already checked in today.")
                return False
    except sqlite3.Error as e:
        logging.error(f"Database error while saving checkin for {hostname}: {e}")
        raise


def generate_pdf():
    with app.app_context():
        yesterday = get_today_in_romania() - timedelta(days=1)
        try:
            with get_db_connection() as conn:
                checked_in = conn.execute(
                    'SELECT hostname, checkin_time FROM checkins WHERE date = ? ORDER BY checkin_time ASC',
                    (yesterday.isoformat(),)).fetchall()
        except sqlite3.Error as e:
            logging.error(f"Database error while generating PDF: {e}")
            return

        absent_hosts = set(AUTHORIZED_HOSTS.keys()) - {row['hostname'] for row in checked_in}
        pdf_dir = CONFIG['pdf']['output_dir']
        os.makedirs(pdf_dir, exist_ok=True)
        pdf_filename = f"{pdf_dir}/{yesterday.isoformat()}.pdf"
        try:
            c = canvas.Canvas(pdf_filename, pagesize=letter)
            width, height = letter
            c.setFont("Helvetica-Bold", 14)
            c.drawString(100, height - 40,
                         gettext("Check-in Report for {date} ({tz})").format(date=yesterday, tz=romania_tz.zone))

            c.setFont("Helvetica-Bold", 12)
            c.drawString(100, height - 80, gettext("Checked in"))
            c.setFont("Helvetica", 10)
            y_position = height - 100

            for row in checked_in:
                if y_position < 50:
                    c.showPage()
                    y_position = height - 50
                    c.setFont("Helvetica", 10)

                checkin_dt = datetime.fromisoformat(row['checkin_time'])
                time_only = checkin_dt.strftime('%H:%M:%S')

                c.drawString(100, y_position,
                             f"{AUTHORIZED_HOSTS.get(row['hostname'], {}).get('name', 'Unknown')} {time_only}")
                y_position -= 15

            c.setFont("Helvetica-Bold", 12)
            if y_position < 50:
                c.showPage()
                y_position = height - 50
            c.drawString(100, y_position - 20, gettext("Absents"))
            c.setFont("Helvetica", 10)
            y_position -= 30

            for hostname in absent_hosts:
                if y_position < 50:
                    c.showPage()
                    y_position = height - 50
                    c.setFont("Helvetica", 10)
                c.drawString(100, y_position, AUTHORIZED_HOSTS.get(hostname, {}).get('name', 'Unknown'))
                y_position -= 15

            c.save()
            logging.info(f"PDF generated: {pdf_filename}")
        except Exception as e:
            logging.error(f"Error generating PDF: {e}")
            return


AUTHORIZED_HOSTS = None

scheduler = BackgroundScheduler(timezone=romania_tz)


def shutdown_scheduler():
    if scheduler.running:
        scheduler.shutdown()
        logging.info("Scheduler shut down successfully.")


atexit.register(shutdown_scheduler)


def initialize_app():
    global AUTHORIZED_HOSTS
    setup_logging()
    with app.app_context():
        init_db()
        AUTHORIZED_HOSTS = load_authorized_hosts()
    scheduler.add_job(generate_pdf, 'cron', hour=1)
    scheduler.start()
    logging.info("Application initialized successfully.")


@app.route('/checkin', methods=['POST'])
def checkin():
    hostname = request.json.get('hostname')
    if not hostname:
        logging.error("No hostname provided in request")
        return jsonify({'status': 'error', 'message': 'Hostname required'}), 400

    current_time = get_current_time_in_romania().isoformat()
    today = get_today_in_romania().isoformat()
    logging.info(f"Processing checkin for {hostname} at {current_time}")

    if hostname not in AUTHORIZED_HOSTS:
        logging.warning(f"Unauthorized hostname: {hostname}")
        return jsonify({'status': 'error', 'message': gettext('Unauthorized hostname')}), 403

    try:
        if save_checkin(hostname, current_time, today):
            logging.info(f"Checkin recorded for {hostname}")
            return jsonify(
                {'status': 'success', 'message': gettext('Check-in recorded'), 'timestamp': current_time}), 200
        else:
            return jsonify({'status': 'info', 'message': gettext('Already checked in today')}), 208
    except sqlite3.Error as e:
        logging.error(f"Database error processing checkin for {hostname}: {e}")
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    except Exception as e:
        logging.error(f"Unexpected error processing checkin for {hostname}: {e}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500


@app.route('/status', methods=['GET'])
def status():
    today = get_today_in_romania().isoformat()
    fresh_checkins = load_daily_checkins()
    return jsonify({'checkins': fresh_checkins.get(today, {})})


if __name__ == '__main__':
    initialize_app()
    server_config = CONFIG['server']
    logging.info(f"Starting server on {server_config['host']}:{server_config['port']}...")
    app.run(host=server_config['host'], port=server_config['port'], debug=server_config['debug'])