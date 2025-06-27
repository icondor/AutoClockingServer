import os
import logging
import logging.handlers
import json
import sqlite3
import pandas as pd
from flask import Flask, request, jsonify, g, send_file
from flask_babel import Babel, gettext
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import pytz
import atexit
from time import time
import traceback
import random
import base64
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

# Set locale environment variables for UTF-8 (important for Linux containers)
import locale

try:
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
except locale.Error:
    logging.warning("Could not set locale to en_US.UTF-8. Special characters may not display correctly.")

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

    config['server']['host'] = os.getenv('SERVER_HOST', config['server']['host'])
    config['server']['port'] = int(os.getenv('SERVER_PORT', config['server']['port']))
    config['server']['debug'] = os.getenv('SERVER_DEBUG', str(config['server']['debug'])).lower() == 'true'
    config['database']['path'] = os.getenv('DB_PATH', config['database']['path'])
    config['excel']['file_path'] = os.getenv('EXCEL_FILE_PATH', config['excel']['file_path'])
    config['pdf']['output_dir'] = os.getenv('PDF_OUTPUT_DIR', config['pdf']['output_dir'])
    config['logging']['file'] = os.getenv('LOG_FILE', config['logging']['file'])
    if 'scheduler' not in config:
        config['scheduler'] = {}
    config['scheduler']['hour'] = int(os.getenv('SCHEDULER_HOUR', config['scheduler'].get('hour', 3)))
    config['scheduler']['minute'] = int(os.getenv('SCHEDULER_MINUTE', config['scheduler'].get('minute', 0)))
    if 'email_scheduler' not in config:
        config['email_scheduler'] = {}
    config['email_scheduler']['day_of_week'] = os.getenv('EMAIL_SCHEDULER_DAYS',
                                                         config['email_scheduler'].get('day_of_week', '2,3,4,5,6')).lower()
    days = config['email_scheduler']['day_of_week'].split(',')
    for day in days:
        try:
            day_num = int(day.strip())
            if not (0 <= day_num <= 6):
                raise ValueError(f"Invalid day_of_week value: {day_num}. Must be between 0 (Sunday) and 6 (Saturday).")
        except ValueError as e:
            logging.error(f"Error in email_scheduler.day_of_week: {str(e)}")
            raise
    config['email_scheduler']['hour'] = int(os.getenv('EMAIL_SCHEDULER_HOUR', config['email_scheduler'].get('hour', 9)))
    config['email_scheduler']['minute'] = int(os.getenv('EMAIL_SCHEDULER_MINUTE', config['email_scheduler'].get('minute', 0)))
    config['email_recipients'] = os.getenv('EMAIL_RECIPIENTS', config.get('email_recipients', ''))
    config['sendgrid_api_key'] = os.getenv('SENDGRID_API_KEY', config.get('sendgrid_api_key', ''))
    config['from_email'] = os.getenv('FROM_EMAIL', config.get('from_email', 'reports@em1391.cloud.trados.com'))
    if 'fonts' not in config:
        config['fonts'] = {}
    config['fonts']['directory'] = os.getenv('FONTS_DIRECTORY', config['fonts'].get('directory', '/app/fonts'))
    config['fonts']['name'] = config['fonts'].get('name', 'ArialUnicode')
    config['fonts']['file'] = config['fonts'].get('file', 'ArialUnicode.ttf')

    return config

CONFIG = load_config()

PDF_COLUMNS = {
    'name': 100,
    'checkin': 260,
    'checkout': 360
}

app = Flask(__name__)
app.config['BABEL_DEFAULT_LOCALE'] = 'ro'
babel = Babel(app)

logging.info("Starting application initialization.")

# Register a font that supports Romanian characters
try:
    logging.info("Attempting to load DejaVuSans font.")
    dejavu_path_docker = '/usr/share/fonts/dejavu/DejaVuSans.ttf'
    dejavu_path_macos = '/Users/icondor/Library/Fonts/DejaVuSans.ttf'

    if os.path.exists(dejavu_path_docker):
        pdfmetrics.registerFont(TTFont('DejaVuSans', dejavu_path_docker))
        PDF_FONT = 'DejaVuSans'
        logging.info(f"Successfully loaded DejaVuSans font from {dejavu_path_docker}.")
    elif os.path.exists(dejavu_path_macos):
        pdfmetrics.registerFont(TTFont('DejaVuSans', dejavu_path_macos))
        PDF_FONT = 'DejaVuSans'
        logging.info(f"Successfully loaded DejaVuSans font from {dejavu_path_macos}.")
    else:
        logging.critical("No suitable font found for Romanian characters.")
        raise RuntimeError("Failed to load DejaVuSans font.")
except Exception as e:
    logging.critical(f"Failed to load DejaVuSans font: {str(e)}")
    raise RuntimeError(f"Font loading failed: {str(e)}")

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
        logging.info(f"Creating new database connection at: {db_path}")
        g.db_connection = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db_connection.row_factory = sqlite3.Row
    return g.db_connection

def get_scheduler_db_connection():
    db_path = os.path.abspath(CONFIG['database']['path'])
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

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
                checkout_time TEXT,
                PRIMARY KEY (hostname, date)
            )''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_checkins_date ON checkins(date)')
            conn.commit()
            logging.info("Checkins table initialized.")

def load_authorized_hosts():
    excel_config = CONFIG['excel']
    file_path = excel_config['file_path']
    if not os.path.exists(file_path):
        logging.error(f"Excel file not found: {file_path}")
        return {}
    try:
        df = pd.read_excel(file_path, usecols=['Hostname', 'Nume ', 'Norma'])
        df['Nume '] = df['Nume '].astype(str).str.strip()
        for name in df['Nume ']:
            logging.debug(f"Raw name from Excel: {name} (hex: {name.encode('utf-8').hex()})")
        return {row['Hostname']: {'name': row['Nume '], 'work_hours': row['Norma']} for _, row in df.iterrows()}
    except Exception as e:
        logging.error(f"Error loading Excel file: {e}")
        return {}

def load_daily_checkins(date=None, conn=None):
    try:
        if conn is None:
            conn = get_db_connection()
        if date:
            rows = conn.execute('SELECT hostname, checkin_time, date, checkout_time FROM checkins WHERE date = ?',
                                (date,)).fetchall()
        else:
            rows = conn.execute('SELECT hostname, checkin_time, date, checkout_time FROM checkins').fetchall()
        daily_checkins = {}
        for row in rows:
            date = row['date']
            hostname = row['hostname']
            checkin_time = row['checkin_time']
            checkout_time = row['checkout_time']
            if date not in daily_checkins:
                daily_checkins[date] = {}
            daily_checkins[date][hostname] = {'checkin_time': checkin_time, 'checkout_time': checkout_time}
        logging.debug(f"Loaded daily checkins for date={date}: {daily_checkins.get(date, {})}")
        return daily_checkins
    except sqlite3.Error as e:
        logging.error(f"Error loading daily checkins: {e}")
        return {}

def save_checkin(hostname, checkin_time):
    today = get_today_in_romania().isoformat()
    try:
        datetime.strptime(today, '%Y-%m-%d')
    except ValueError:
        logging.error(f"Invalid date format for {today}")
        raise ValueError("Date must be in YYYY-MM-DD format")

    try:
        checkin_dt = datetime.fromisoformat(checkin_time)
        if checkin_dt.tzinfo is None:
            checkin_dt = romania_tz.localize(checkin_dt)
        else:
            checkin_dt = checkin_dt.astimezone(romania_tz)
        norma = AUTHORIZED_HOSTS.get(hostname, {}).get('work_hours', 8)
        random_minutes = random.randint(-20, 40)
        total_minutes = (norma * 60) + random_minutes
        hours_to_add = total_minutes // 60
        minutes_to_add = total_minutes % 60
        checkout_dt = checkin_dt + timedelta(hours=hours_to_add, minutes=minutes_to_add)
        checkout_time = checkout_dt.isoformat()

        with get_db_connection() as conn:
            conn.execute(
                'INSERT OR IGNORE INTO checkins (hostname, checkin_time, date, checkout_time) VALUES (?, ?, ?, ?)',
                (hostname, checkin_time, today, checkout_time))
            conn.commit()
            if conn.total_changes > 0:
                logging.info(f"Saved checkin for {hostname} with checkout_time={checkout_time}")
                return True
            else:
                logging.info(f"{hostname} already checked in today.")
                return False
    except sqlite3.Error as e:
        logging.error(f"Database error saving checkin for {hostname}: {e}")
        raise

def truncate_text(text, font, font_size, max_width):
    ellipsis = "..."
    ellipsis_width = pdfmetrics.stringWidth(ellipsis, font, font_size)
    text_width = pdfmetrics.stringWidth(text, font, font_size)

    if text_width <= max_width:
        return text

    truncated_text = ""
    for i in range(len(text)):
        test_text = text[:i + 1] + ellipsis
        if pdfmetrics.stringWidth(test_text, font, font_size) > max_width:
            break
        truncated_text = test_text

    return truncated_text

def generate_pdf_for_date(report_date=None):
    start_time = time()
    if report_date is None:
        report_date = get_today_in_romania() - timedelta(days=1)
    report_date_str = report_date.isoformat()
    logging.info(f"Starting PDF generation for date: {report_date_str}")

    try:
        with get_scheduler_db_connection() as conn:
            checked_in = load_daily_checkins(date=report_date_str, conn=conn).get(report_date_str, {})
            logging.info(f"Retrieved {len(checked_in)} check-ins for date: {report_date_str}")

            absent_hosts = set(AUTHORIZED_HOSTS.keys()) - set(checked_in.keys())
            logging.info(f"Calculated {len(absent_hosts)} absent hosts for date: {report_date_str}")

            pdf_dir = CONFIG['pdf']['output_dir']
            os.makedirs(pdf_dir, exist_ok=True)
            pdf_filename = f"{pdf_dir}/{report_date_str}.pdf"
            logging.info(f"Generating PDF: {pdf_filename}")

            c = canvas.Canvas(pdf_filename, pagesize=letter)
            width, height = letter

            c.setFont(PDF_FONT, 14)
            title = gettext("Check-in Report for {date} ({tz})").format(date=report_date, tz=romania_tz.zone)
            c.drawString(100, height - 40, title)

            y_position = height - 80
            if checked_in:
                c.setFont(PDF_FONT, 12)
                c.drawString(100, y_position, gettext("Checked in"))
                y_position -= 20

                c.setFont(PDF_FONT, 10)
                c.drawString(PDF_COLUMNS['name'], y_position, gettext("Name"))
                c.drawString(PDF_COLUMNS['checkin'], y_position, gettext("Check-in"))
                c.drawString(PDF_COLUMNS['checkout'], y_position, gettext("Check-out"))
                y_position -= 15

                c.setFont(PDF_FONT, 10)
                for hostname, times in checked_in.items():
                    if y_position < 50:
                        c.showPage()
                        y_position = height - 40
                        c.setFont(PDF_FONT, 14)
                        c.drawString(100, y_position, title)
                        y_position -= 40
                        c.setFont(PDF_FONT, 12)
                        c.drawString(100, y_position, gettext("Checked in"))
                        y_position -= 20
                        c.setFont(PDF_FONT, 10)
                        c.drawString(PDF_COLUMNS['name'], y_position, gettext("Name"))
                        c.drawString(PDF_COLUMNS['checkin'], y_position, gettext("Check-in"))
                        c.drawString(PDF_COLUMNS['checkout'], y_position, gettext("Check-out"))
                        y_position -= 15
                        c.setFont(PDF_FONT, 10)

                    checkin_dt = datetime.fromisoformat(times['checkin_time'])
                    checkin_time_only = checkin_dt.strftime('%H:%M:%S')
                    checkout_time_only = None
                    if times['checkout_time']:
                        checkout_dt = datetime.fromisoformat(times['checkout_time'])
                        checkout_time_only = checkout_dt.strftime('%H:%M:%S')
                    name = AUTHORIZED_HOSTS.get(hostname, {}).get('name', 'Unknown')
                    max_name_width = PDF_COLUMNS['checkin'] - PDF_COLUMNS['name'] - 10
                    name_for_pdf = truncate_text(name, PDF_FONT, 10, max_name_width)

                    c.drawString(PDF_COLUMNS['name'], y_position, name_for_pdf)
                    c.drawString(PDF_COLUMNS['checkin'] + 10, y_position, checkin_time_only)
                    c.drawString(PDF_COLUMNS['checkout'] + 10, y_position, checkout_time_only or 'N/A')
                    y_position -= 15

                if absent_hosts:
                    y_position -= 20

            if absent_hosts:
                if y_position < 100:
                    c.showPage()
                    y_position = height - 40
                    c.setFont(PDF_FONT, 14)
                    c.drawString(100, y_position, title)
                    y_position -= 40

                c.setFont(PDF_FONT, 12)
                c.drawString(100, y_position, gettext("Absents"))
                y_position -= 20

                c.setFont(PDF_FONT, 10)
                c.drawString(PDF_COLUMNS['name'], y_position, gettext("Name"))
                y_position -= 15

                c.setFont(PDF_FONT, 10)
                for hostname in absent_hosts:
                    if y_position < 50:
                        c.showPage()
                        y_position = height - 40
                        c.setFont(PDF_FONT, 14)
                        c.drawString(100, y_position, title)
                        y_position -= 40
                        c.setFont(PDF_FONT, 12)
                        c.drawString(100, y_position, gettext("Absents"))
                        y_position -= 20
                        c.setFont(PDF_FONT, 10)
                        c.drawString(PDF_COLUMNS['name'], y_position, gettext("Name"))
                        y_position -= 15
                        c.setFont(PDF_FONT, 10)

                    name = AUTHORIZED_HOSTS.get(hostname, {}).get('name', 'Unknown')
                    name_for_pdf = name
                    c.drawString(PDF_COLUMNS['name'], y_position, name_for_pdf)
                    y_position -= 15

            c.save()
            end_time = time()
            logging.info(f"PDF generated: {pdf_filename} (took {end_time - start_time:.2f} seconds)")
            return pdf_filename
    except Exception as e:
        logging.error(f"Error generating PDF for date {report_date_str}: {str(e)}")
        return None

def generate_pdf():
    generate_pdf_for_date()

def send_pdf_email(report_date=None):
    if report_date is None:
        report_date = get_today_in_romania() - timedelta(days=1)
    report_date_str = report_date.isoformat()
    pdf_filename = f"{CONFIG['pdf']['output_dir']}/{report_date_str}.pdf"
    logging.info(f"Attempting to send email with PDF for date: {report_date_str}")

    if not os.path.exists(pdf_filename):
        logging.error(f"PDF file not found: {pdf_filename}")
        return False

    recipients = [email.strip() for email in CONFIG['email_recipients'].split(',') if email.strip()]
    if not recipients:
        logging.error("No email recipients configured.")
        return False
    logging.info(f"Sending email to recipients: {recipients}")

    try:
        message = Mail(
            from_email=CONFIG['from_email'],
            to_emails=recipients,
            subject=f'Check-in Report for {report_date_str}',
            html_content=f'<p>Please find attached the check-in report for {report_date_str}.</p>'
        )

        with open(pdf_filename, 'rb') as f:
            pdf_data = f.read()
        encoded_file = base64.b64encode(pdf_data).decode()
        attachment = Attachment(
            FileContent(encoded_file),
            FileName(f'report_{report_date_str}.pdf'),
            FileType('application/pdf'),
            Disposition('attachment')
        )
        message.attachment = attachment

        sendgrid_client = SendGridAPIClient(CONFIG['sendgrid_api_key'])
        response = sendgrid_client.send(message)
        logging.info(f"Email sent successfully: {response.status_code}")
        return True
    except Exception as e:
        logging.error(f"Failed to send email for date {report_date_str}: {str(e)}")
        return False

def send_pdf_email_task():
    send_pdf_email()

def garbage_collector():
    #MAX_ROWS = 10500  # 350 hosts * 30 days
    MAX_ROWS = 6000  # 350 hosts * 30 days
    try:
        with get_db_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM checkins")
            count = cursor.fetchone()[0]
            logging.info(f"Garbage collector: Current row count: {count}")
            if count <= MAX_ROWS:
                logging.info(f"Garbage collector: Row count {count} is below MAX_ROWS {MAX_ROWS}. No deletion needed.")
                return

            # Calculate rows to delete
            rows_to_delete = count - MAX_ROWS
            logging.info(f"Garbage collector: Need to delete approximately {rows_to_delete} rows to reach MAX_ROWS {MAX_ROWS}")

            # Get dates sorted by ascending order
            cursor = conn.execute("SELECT DISTINCT date FROM checkins ORDER BY date ASC")
            dates = [row[0] for row in cursor.fetchall()]
            rows_deleted = 0
            dates_to_delete = []

            # Determine dates to delete
            for date in dates:
                cursor = conn.execute("SELECT COUNT(*) FROM checkins WHERE date = ?", (date,))
                date_rows = cursor.fetchone()[0]
                if rows_deleted + date_rows <= rows_to_delete:
                    dates_to_delete.append(date)
                    rows_deleted += date_rows
                else:
                    # Include one more date if needed to exceed rows_to_delete
                    dates_to_delete.append(date)
                    rows_deleted += date_rows
                    break

            if not dates_to_delete:
                logging.info("Garbage collector: No dates to delete.")
                return

            # Delete all selected dates in one transaction
            logging.info(f"Garbage collector: Deleting {rows_deleted} rows from dates {dates_to_delete}")
            for date in dates_to_delete:
                conn.execute("DELETE FROM checkins WHERE date = ?", (date,))
                pdf_path = os.path.join(CONFIG['pdf']['output_dir'], f"checkins_{date}.pdf")
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
                    logging.info(f"Garbage collector: Deleted PDF: {pdf_path}")
                else:
                    logging.info(f"Garbage collector: No PDF file found for {date} to delete.")

            conn.commit()
            logging.info(f"Garbage collector: Deleted {rows_deleted} rows from {len(dates_to_delete)} dates")

            # Verify final count
            cursor = conn.execute("SELECT COUNT(*) FROM checkins")
            final_count = cursor.fetchone()[0]
            logging.info(f"Garbage collector: Final row count: {final_count}")
    except Exception as e:
        logging.error(f"Garbage collector: Error during execution: {e}")
        raise

AUTHORIZED_HOSTS = None
scheduler = BackgroundScheduler(timezone=romania_tz)
email_scheduler = BackgroundScheduler(timezone=romania_tz)
garbage_scheduler = BackgroundScheduler(timezone=romania_tz)

def shutdown_schedulers():
    if scheduler.running:
        scheduler.shutdown()
        logging.info("PDF scheduler shut down successfully.")
    if email_scheduler.running:
        email_scheduler.shutdown()
        logging.info("Email scheduler shut down successfully.")
    if garbage_scheduler.running:
        garbage_scheduler.shutdown()
        logging.info("Garbage collector scheduler shut down successfully.")

atexit.register(shutdown_schedulers)

def initialize_app():
    global AUTHORIZED_HOSTS
    setup_logging()
    with app.app_context():
        init_db()
        AUTHORIZED_HOSTS = load_authorized_hosts()
        if not AUTHORIZED_HOSTS:
            logging.error("No authorized hosts loaded.")
            raise RuntimeError("Failed to load authorized hosts.")

    scheduler_hour = CONFIG['scheduler']['hour']
    scheduler_minute = CONFIG['scheduler']['minute']
    logging.info(f"Scheduling generate_pdf job at {scheduler_hour:02d}:{scheduler_minute:02d}")
    scheduler.add_job(generate_pdf, 'cron', hour=scheduler_hour, minute=scheduler_minute)
    scheduler.start()
    logging.info(f"PDF scheduler started with timezone {scheduler.timezone}")

    email_scheduler_days = CONFIG['email_scheduler']['day_of_week'].split(',')
    logging.info(f"Scheduling send_pdf_email_task on days: {email_scheduler_days}")
    email_scheduler.add_job(
        send_pdf_email_task,
        'cron',
        day_of_week=','.join(email_scheduler_days),
        hour=CONFIG['email_scheduler']['hour'],
        minute=CONFIG['email_scheduler']['minute']
    )
    email_scheduler.start()
    logging.info(f"Email scheduler started with timezone {email_scheduler.timezone}")

    garbage_hour = 2  # Run at 2 AM
    garbage_minute = 0
    logging.info(f"Scheduling garbage_collector job at {garbage_hour:02d}:{garbage_minute:02d}")
    garbage_scheduler.add_job(garbage_collector, 'cron', hour=garbage_hour, minute=garbage_minute)
    garbage_scheduler.start()
    logging.info(f"Garbage collector scheduler started with timezone {garbage_scheduler.timezone}")

    logging.info("Application initialized successfully.")

@app.route('/checkin', methods=['POST'])
def checkin():
    hostname = request.json.get('hostname')
    if not hostname:
        logging.error("No hostname provided in request")
        return jsonify({'status': 'error', 'message': 'Hostname required'}), 400

    current_time = get_current_time_in_romania().isoformat()
    logging.info(f"Processing checkin for {hostname} at {current_time}")

    if hostname not in AUTHORIZED_HOSTS:
        logging.warning(f"Unauthorized hostname: {hostname}")
        return jsonify({'status': 'error', 'message': gettext('Unauthorized hostname')}), 403

    try:
        if save_checkin(hostname, current_time):
            logging.info(f"Checkin recorded for {hostname}")
            return jsonify({'status': 'success', 'message': gettext('Check-in recorded'), 'timestamp': current_time}), 200
        else:
            return jsonify({'status': 'info', 'message': gettext('Already checked in today')}), 208
    except sqlite3.Error as e:
        logging.error(f"Database error processing checkin for {hostname}: {e}")
        return jsonify({'status': 'error', 'message': 'Database error'}), 500

@app.route('/status', methods=['GET'])
def status():
    today = get_today_in_romania().isoformat()
    fresh_checkins = load_daily_checkins(date=today)
    return jsonify({'checkins': fresh_checkins.get(today, {})})

@app.route('/generate_pdf', methods=['GET'])
def generate_pdf_endpoint():
    date_str = request.args.get('date')
    if date_str:
        try:
            report_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            report_date_str = report_date.isoformat()
        except ValueError:
            logging.error(f"Invalid date format: {date_str}")
            return jsonify({'status': 'error', 'message': 'Invalid date format. Use YYYY-MM-DD'}), 400
    else:
        report_date = get_today_in_romania() - timedelta(days=1)
        report_date_str = report_date.isoformat()

    pdf_filename = generate_pdf_for_date(report_date)
    if pdf_filename:
        return send_file(pdf_filename, as_attachment=True, download_name=f"report_{report_date_str}.pdf")
    else:
        return jsonify({'status': 'error', 'message': 'Failed to generate PDF'}), 500

@app.route('/send_pdf_email', methods=['POST'])
def send_pdf_email_endpoint():
    date_str = request.json.get('date') if request.json else None
    if date_str:
        try:
            report_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            logging.error(f"Invalid date format: {date_str}")
            return jsonify({'status': 'error', 'message': 'Invalid date format. Use YYYY-MM-DD'}), 400
    else:
        report_date = None

    if send_pdf_email(report_date):
        return jsonify({'status': 'success', 'message': 'Email sent successfully'}), 200
    else:
        return jsonify({'status': 'error', 'message': 'Failed to send email'}), 500

from waitress import serve


@app.route('/run_garbage_collector', methods=['GET'])
def run_garbage_collector():
    try:
        garbage_collector()
        return jsonify({'status': 'success', 'message': 'Garbage collector executed'}), 200
    except Exception as e:
        logging.error(f"Error running garbage collector: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Failed to run garbage collector'}), 500

if __name__ == '__main__':
    logging.info(f"Starting server initialization at {datetime.now().isoformat()}")
    initialize_app()
    server_config = CONFIG['server']
    logging.info(f"Binding to host={server_config['host']}, port={server_config['port']}")
    serve(app, host=server_config['host'], port=server_config['port'])