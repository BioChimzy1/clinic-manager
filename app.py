import os
import sqlite3
import datetime
import uuid
import math
from functools import wraps
from flask import Flask, request, session, jsonify, send_from_directory, g
from werkzeug.security import check_password_hash, generate_password_hash
from roles_permissions import has_permission

app = Flask(__name__)

# THIS IS REQUIRED FOR LOGIN SESSIONS TO WORK
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-fallback-do-not-use-in-prod')

app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'clinic.db')

# ------------------------------------------------------------------
# DATABASE CONNECTION CONTEXT
# ------------------------------------------------------------------
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.execute("PRAGMA foreign_keys = ON;")
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

# ------------------------------------------------------------------
# PERMISSION DECORATOR (API version)
# ------------------------------------------------------------------
def require_permission(permission):
    """Gate a route behind a permission string.
    Returns JSON error on denial instead of redirect."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            user_role = session.get('role', '')
            if not has_permission(user_role, permission):
                return jsonify({'success': False, 'error': 'Permission denied.'}), 403
            return view_func(*args, **kwargs)
        return wrapped
    return decorator

# ------------------------------------------------------------------
# HELPER FUNCTIONS
# ------------------------------------------------------------------

def get_clinic_currency(clinic_id):
    """Get the currency for a specific clinic with request-scoped caching"""
    
    # Check if already cached in this request
    cache_key = f'currency_{clinic_id}'
    if hasattr(g, cache_key):
        return getattr(g, cache_key)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.currency_id, cur.code, cur.symbol, cur.subunit_name, cur.subunit_ratio
        FROM clinics c
        JOIN currencies cur ON c.currency_id = cur.id
        WHERE c.id = ?
    ''', (clinic_id,))
    row = cursor.fetchone()
    
    if row:
        result = {
            'id': row[0],
            'code': row[1],
            'symbol': row[2],
            'subunit_name': row[3],
            'subunit_ratio': row[4]
        }
    else:
        # Fallback to MWK
        result = {'code': 'MWK', 'symbol': 'MK', 'subunit_name': 'Tambala', 'subunit_ratio': 100}
    
    # Cache it for this request
    setattr(g, cache_key, result)
    
    return result

def format_amount(amount, currency=None):
    """Format an amount in the given currency.

    Amounts are stored in subunits (e.g. tambala, cents) — divide by
    subunit_ratio to get the main-unit display value.
    """
    if currency is None:
        clinic_id = get_current_clinic_id()
        if clinic_id:
            currency = get_clinic_currency(clinic_id)
        else:
            currency = {'code': 'MWK', 'symbol': 'MK', 'subunit_ratio': 100}

    amount = amount or 0
    main_amount = amount / currency['subunit_ratio']
    formatted = f"{currency['symbol']} {main_amount:,.2f}"
    return formatted

def get_all_currencies():
    """Get all active currencies"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, code, name, symbol, subunit_ratio, is_default
        FROM currencies
        WHERE is_active = 1
        ORDER BY is_default DESC, name ASC
    ''')
    return cursor.fetchall()

def get_current_clinic_id():
    cached = session.get('clinic_id')
    if cached:
        return cached
    staff_id = session.get('staff_id')
    if not staff_id:
        return None
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT clinic_id FROM staff_clinics WHERE staff_id = ? LIMIT 1', (staff_id,))
    row = cursor.fetchone()
    clinic_id = row[0] if row else None
    if clinic_id:
        session['clinic_id'] = clinic_id
    return clinic_id

def get_price_list_data(clinic_id):
    cursor = get_db().cursor()
    today_str = datetime.date.today().isoformat()

    cursor.execute('''
        SELECT price_list.id, price_list.item_type, price_list.item_name,
               price_list.price, price_list.quantity,
               COALESCE(stock.usable_qty, 0) AS usable_qty,
               COALESCE(stock.expired_qty, 0) AS expired_qty,
               CASE WHEN price_list.inventory_id IS NULL THEN 1 ELSE 0 END AS no_stock_concept
        FROM price_list
        LEFT JOIN inventory AS linked_item
            ON price_list.inventory_id = linked_item.id
        LEFT JOIN (
            SELECT LOWER(TRIM(item_name)) AS name_key, category,
                   SUM(CASE WHEN expiry_date >= ? THEN quantity ELSE 0 END) AS usable_qty,
                   SUM(CASE WHEN expiry_date <  ? THEN quantity ELSE 0 END) AS expired_qty
            FROM inventory
            WHERE is_active = 1 AND clinic_id = ?
            GROUP BY name_key, category
        ) AS stock
            ON stock.name_key = LOWER(TRIM(linked_item.item_name))
            AND stock.category = linked_item.category
        WHERE price_list.is_active = 1
          AND price_list.clinic_id = ?
        ORDER BY
            CASE
                WHEN price_list.inventory_id IS NULL THEN 0
                WHEN COALESCE(stock.usable_qty, 0) > 0 THEN 0
                WHEN COALESCE(stock.expired_qty, 0) > 0 THEN 1
                ELSE 2
            END,
            price_list.item_name
    ''', (today_str, today_str, clinic_id, clinic_id))
    rows = cursor.fetchall()
    return rows

def get_inventory_data(clinic_id):
    cursor = get_db().cursor()
    cursor.execute("""
        SELECT id, category, item_name, quantity, min_alert_level, expiry_date
        FROM inventory
        WHERE clinic_id = ? AND is_active = 1
        ORDER BY expiry_date ASC
    """, (clinic_id,))
    rows = cursor.fetchall()
    return rows

def get_queue_data(clinic_id):
    cursor = get_db().cursor()
    cursor.execute('''
        SELECT patients.id, patients.name, patients.sex, patients.phone,
               appointments.appointment_type, appointments.status
        FROM patients
        JOIN appointments ON patients.id = appointments.patient_id
        WHERE appointments.status IN ('Waiting', 'Pending', 'Returned to Doctor')
          AND appointments.clinic_id = ?
        ORDER BY
            CASE WHEN appointments.status = 'Returned to Doctor' THEN 0 ELSE 1 END,
            appointments.created_at ASC
    ''', (clinic_id,))
    queue_list = cursor.fetchall()
    return queue_list

def get_loans_data(clinic_id):
    cursor = get_db().cursor()
    cursor.execute('''
        SELECT
            visits.id,
            visits.total_fee,
            visits.amount_paid,
            visits.loan_witness,
            visits.loan_due_date,
            visits.created_at,
            visits.is_retail,
            patients.name AS patient_name
        FROM visits
        LEFT JOIN patients ON visits.patient_id = patients.id
        WHERE visits.clinic_id = ?
          AND visits.status = 'Loan Active'
        ORDER BY visits.loan_due_date IS NULL, visits.loan_due_date ASC, visits.created_at ASC
    ''', (clinic_id,))
    loan_list = cursor.fetchall()
    return loan_list

def get_dashboard_data(clinic_id):
    cursor = get_db().cursor()

    cursor.execute("SELECT COUNT(*) FROM appointments WHERE clinic_id = ? AND status = 'Waiting'", (clinic_id,))
    waiting_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM appointments WHERE clinic_id = ? AND status = 'Pending'", (clinic_id,))
    pending_count = cursor.fetchone()[0]

    total_queue_count = waiting_count + pending_count

    today = datetime.date.today()
    today_start = today.isoformat() + 'T00:00:00'
    today_end = today.isoformat() + 'T23:59:59'

    cursor.execute('''
        SELECT COUNT(DISTINCT unique_id) FROM (
            SELECT id AS unique_id
            FROM visits
            WHERE clinic_id = ? 
            AND visit_date >= ? AND visit_date <= ?
            AND (is_retail IS NULL OR is_retail = 0)
            
            UNION
            
            SELECT id AS unique_id
            FROM visits
            WHERE clinic_id = ? 
            AND visit_date >= ? AND visit_date <= ?
            AND is_retail = 1
            
            UNION
            
            SELECT loan_payments.visit_id AS unique_id
            FROM loan_payments
            JOIN visits ON loan_payments.visit_id = visits.id
            WHERE visits.clinic_id = ? 
            AND loan_payments.payment_date >= ? AND loan_payments.payment_date <= ?
            AND visits.visit_date < ?
        )
    ''', (clinic_id, today_start, today_end, 
          clinic_id, today_start, today_end, 
          clinic_id, today_start, today_end, today_start))

    seen_today_count = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM inventory WHERE clinic_id = ? AND is_active = 1", (clinic_id,))
    total_items_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM inventory WHERE clinic_id = ? AND is_active = 1 AND quantity <= min_alert_level", (clinic_id,))
    low_stock_count = cursor.fetchone()[0]

    cutoff = (today + datetime.timedelta(days=14)).isoformat()
    cursor.execute(
        "SELECT COUNT(*) FROM inventory WHERE clinic_id = ? AND is_active = 1 AND expiry_date IS NOT NULL AND expiry_date <= ? AND expiry_date >= ?",
        (clinic_id, cutoff, today.isoformat())
    )
    expiring_soon_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM price_list WHERE clinic_id = ? AND is_active = 1", (clinic_id,))
    priced_items_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM visits WHERE clinic_id = ? AND status = 'Ready for Cashier'", (clinic_id,))
    cashier_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM visits WHERE clinic_id = ? AND status = 'Loan Active'", (clinic_id,))
    loans_count = cursor.fetchone()[0]

    cursor.execute('''
        SELECT SUM(amount_paid) FROM visits 
        WHERE clinic_id = ?
        AND status = 'Paid' 
        AND id NOT IN (SELECT DISTINCT visit_id FROM loan_payments)
        AND updated_at >= ? AND updated_at <= ?
    ''', (clinic_id, today_start, today_end))
    today_cash_direct = cursor.fetchone()[0] or 0

    cursor.execute('''
        SELECT SUM(loan_payments.amount) FROM loan_payments
        JOIN visits ON loan_payments.visit_id = visits.id
        WHERE visits.clinic_id = ?
        AND loan_payments.payment_date >= ? AND loan_payments.payment_date <= ?
    ''', (clinic_id, today_start, today_end))
    today_cash_from_loans = cursor.fetchone()[0] or 0

    today_cash_collected = today_cash_direct + today_cash_from_loans

    cursor.execute("SELECT COUNT(*) FROM appointments WHERE clinic_id = ? AND status IN ('Pending', 'Scheduled')", (clinic_id,))
    appointments_count = cursor.fetchone()[0]

    return {
        'waiting_count': waiting_count,
        'pending_count': pending_count,
        'total_queue_count': total_queue_count,
        'seen_today_count': seen_today_count,
        'total_items_count': total_items_count,
        'low_stock_count': low_stock_count,
        'expiring_soon_count': expiring_soon_count,
        'priced_items_count': priced_items_count,
        'cashier_count': cashier_count,
        'loans_count': loans_count,
        'today_cash_collected': today_cash_collected,
        'appointments_count': appointments_count,
    }

def get_period_dates(period):
    today = datetime.date.today()
    if period == 'today':
        start_date = today.isoformat() + 'T00:00:00'
        end_date = today.isoformat() + 'T23:59:59'
    elif period == 'week':
        start_date = (today - datetime.timedelta(days=7)).isoformat() + 'T00:00:00'
        end_date = today.isoformat() + 'T23:59:59'
    elif period == 'month':
        start_date = (today - datetime.timedelta(days=30)).isoformat() + 'T00:00:00'
        end_date = today.isoformat() + 'T23:59:59'
    else:
        start_date = (today - datetime.timedelta(days=7)).isoformat() + 'T00:00:00'
        end_date = today.isoformat() + 'T23:59:59'
        period = 'week'
    return start_date, end_date, period

def build_grouped_transactions(cursor, clinic_id, start_date, end_date, today_str, offset=0, limit=20):
    cursor.execute('''
        SELECT loan_payments.visit_id, DATE(loan_payments.payment_date) AS pay_date,
               SUM(loan_payments.amount) AS amount_sum,
               MAX(loan_payments.payment_date) AS pay_ts
        FROM loan_payments
        JOIN visits ON loan_payments.visit_id = visits.id
        WHERE loan_payments.payment_date >= ? AND loan_payments.payment_date <= ?
          AND visits.clinic_id = ?
        GROUP BY loan_payments.visit_id, DATE(loan_payments.payment_date)
    ''', (start_date, end_date, clinic_id))
    loan_grouped = cursor.fetchall()

    cursor.execute('''
        SELECT id AS visit_id, DATE(updated_at) AS pay_date, amount_paid AS amount_sum,
               updated_at AS pay_ts
        FROM visits
        WHERE status = 'Paid'
          AND clinic_id = ?
          AND id NOT IN (SELECT DISTINCT visit_id FROM loan_payments)
          AND updated_at >= ? AND updated_at <= ?
    ''', (clinic_id, start_date, end_date))
    direct_grouped = cursor.fetchall()

    from collections import defaultdict
    installments_map = defaultdict(list)

    for visit_id, pay_date, amount_sum, pay_ts in loan_grouped:
        if pay_date:
            installments_map[visit_id].append({'date': pay_date, 'amount': amount_sum, 'ts': pay_ts})
    for visit_id, pay_date, amount_sum, pay_ts in direct_grouped:
        if pay_date:
            installments_map[visit_id].append({'date': pay_date, 'amount': amount_sum, 'ts': pay_ts})

    grouped_transactions = []
    has_more = False
    if installments_map:
        visit_latest = []
        for vid, insts in installments_map.items():
            insts_sorted = sorted(insts, key=lambda x: x['ts'], reverse=True)
            installments_map[vid] = insts_sorted
            latest_date = insts_sorted[0]['date']
            latest_amount = insts_sorted[0]['amount']
            latest_ts = insts_sorted[0]['ts']
            today_total = sum(inst['amount'] for inst in insts_sorted if inst['date'] == today_str)
            visit_latest.append((vid, latest_date, latest_amount, today_total, latest_ts))

        visit_latest.sort(key=lambda x: x[4], reverse=True)

        total_count = len(visit_latest)
        page = visit_latest[offset:offset + limit]
        has_more = (offset + limit) < total_count
        top_visit_ids = [v[0] for v in page]

        if top_visit_ids:
            qmarks = ','.join(['?'] * len(top_visit_ids))
            cursor.execute(f'''
                SELECT visits.id, visits.total_fee, visits.amount_paid, visits.discount_amount, visits.status, visits.is_retail, patients.name
                FROM visits
                LEFT JOIN patients ON visits.patient_id = patients.id
                WHERE visits.id IN ({qmarks})
            ''', top_visit_ids)
            visit_rows = cursor.fetchall()
            visits_by_id = {r[0]: r for r in visit_rows}

            cursor.execute(f'''
                SELECT visit_id, item_name, item_type, quantity
                FROM visit_items
                WHERE visit_id IN ({qmarks})
            ''', top_visit_ids)
            items_by_visit = defaultdict(list)
            for v_id, item_name, item_type, quantity in cursor.fetchall():
                items_by_visit[v_id].append({
                    'name': item_name,
                    'category': item_type,
                    'quantity': quantity
                })

            for vid, latest_date, latest_amount, today_total, latest_ts in page:
                vrow = visits_by_id.get(vid)
                if vrow:
                    if today_total and today_total > 0:
                        summary_date = today_str
                        summary_amount = today_total
                    else:
                        summary_date = latest_date
                        summary_amount = latest_amount

                    total_fee = vrow[1] or 0
                    amount_paid = vrow[2] or 0
                    discount_amount = vrow[3] or 0
                    outstanding = max(0, total_fee - discount_amount - amount_paid)

                    grouped_transactions.append({
                        'visit_id': vrow[0],
                        'patient': vrow[6] or '🏪 Retail Sale',
                        'summary_date': summary_date,
                        'summary_amount': summary_amount,
                        'latest_date': latest_date,
                        'latest_amount': latest_amount,
                        'today_total': today_total,
                        'total_fee': total_fee,
                        'amount_paid': amount_paid,
                        'discount_amount': discount_amount,
                        'outstanding': outstanding,
                        'status': vrow[4] or '',
                        'is_retail': vrow[5] or 0,
                        'items_sold': items_by_visit.get(vid, []),
                        'installments': installments_map.get(vid, [])
                    })

    return grouped_transactions, has_more

# ------------------------------------------------------------------
# AUDIT LOGGING HELPER
# ------------------------------------------------------------------
def log_audit(action, table_name, record_id, old_value=None, new_value=None):
    staff_id = session.get('staff_id')
    if not staff_id:
        return
    
    clinic_id = get_current_clinic_id()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO audit_log (staff_id, clinic_id, action, table_name, record_id, old_value, new_value, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (staff_id, clinic_id, action, table_name, record_id, old_value, new_value, datetime.datetime.now().isoformat()))
    conn.commit()

# ------------------------------------------------------------------
# DATABASE INITIALIZATION
# ------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    # 1. clinics
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS clinics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT UNIQUE,
        clinic_name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        address TEXT,
        currency_id INTEGER DEFAULT 1,
        is_active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT,
        is_synced INTEGER DEFAULT 0
    )
''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_clinics_uuid ON clinics(uuid);')

    
    # 2. staff
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS staff (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT UNIQUE,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT,
        is_synced INTEGER DEFAULT 0
    )
''')

    # 2.5 staff_clinics
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS staff_clinics (
        staff_id INTEGER NOT NULL,
        clinic_id INTEGER NOT NULL,
        role TEXT NOT NULL DEFAULT 'Doctor',
        FOREIGN KEY (staff_id) REFERENCES staff (id),
        FOREIGN KEY (clinic_id) REFERENCES clinics (id),
        PRIMARY KEY (staff_id, clinic_id)
    )
''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_staff_uuid ON staff(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_staff_clinics_staff ON staff_clinics(staff_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_staff_clinics_clinic ON staff_clinics(clinic_id);')
    
    # 3. patients
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE,
            clinic_id INTEGER,
            name TEXT NOT NULL,
            date_of_birth TEXT,
            sex TEXT,
            phone TEXT,
            location TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            is_synced INTEGER DEFAULT 0,
            FOREIGN KEY (clinic_id) REFERENCES clinics (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_patients_uuid ON patients(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_patients_clinic ON patients(clinic_id);')
    
    # 4. appointments
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE,
            clinic_id INTEGER,
            patient_id INTEGER,
            doctor_id INTEGER,
            appointment_date TEXT NOT NULL,
            appointment_type TEXT DEFAULT 'Walk-In',
            reason TEXT,
            status TEXT DEFAULT 'Scheduled',
            check_in_time TEXT,
            cancelled_reason TEXT,
            created_at TEXT,
            updated_at TEXT,
            is_synced INTEGER DEFAULT 0,
            FOREIGN KEY (clinic_id) REFERENCES clinics (id),
            FOREIGN KEY (patient_id) REFERENCES patients (id),
            FOREIGN KEY (doctor_id) REFERENCES staff (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_appointments_uuid ON appointments(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_appointments_clinic ON appointments(clinic_id);')
    
    # 5. visits
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE,
            clinic_id INTEGER,
            patient_id INTEGER,
            doctor_id INTEGER,
            appointment_id INTEGER,
            visit_date TEXT NOT NULL,
            diagnosis TEXT NOT NULL,
            referral TEXT DEFAULT 'None',
            total_fee INTEGER DEFAULT 0,
            amount_paid INTEGER DEFAULT 0,
            loan_witness TEXT,
            discount_amount INTEGER DEFAULT 0,
            discount_reason TEXT,
            loan_due_date TEXT,
            status TEXT DEFAULT 'Open',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            is_synced INTEGER DEFAULT 0,
            payment_channel TEXT DEFAULT 'Cash',
            payment_reference TEXT,
            medical_aid_company TEXT,
            is_retail INTEGER DEFAULT 0,
            return_reason TEXT,
            FOREIGN KEY (clinic_id) REFERENCES clinics (id),
            FOREIGN KEY (patient_id) REFERENCES patients (id),
            FOREIGN KEY (doctor_id) REFERENCES staff (id),
            FOREIGN KEY (appointment_id) REFERENCES appointments (id)
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_visits_uuid ON visits(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_visits_clinic ON visits(clinic_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_visits_appointment ON visits(appointment_id);')
    
    # 6. visit_items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS visit_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE,
            visit_id INTEGER,
            inventory_id INTEGER,
            price_list_id INTEGER,
            item_type TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER DEFAULT 1,
            price_per_unit INTEGER DEFAULT 0,
            total_line_price INTEGER DEFAULT 0,
            created_at TEXT,
            is_synced INTEGER DEFAULT 0,
            FOREIGN KEY (visit_id) REFERENCES visits (id),
            FOREIGN KEY (price_list_id) REFERENCES price_list (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_visit_items_uuid ON visit_items(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_visit_items_visit ON visit_items(visit_id);')
    
    # 7. loan_payments
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS loan_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE,
            visit_id INTEGER,
            payment_date TEXT NOT NULL,
            amount INTEGER NOT NULL,
            created_at TEXT,
            is_synced INTEGER DEFAULT 0,
            FOREIGN KEY (visit_id) REFERENCES visits (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_loan_payments_uuid ON loan_payments(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_loan_payments_visit ON loan_payments(visit_id);')
    
    # 8. inventory
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE,
            clinic_id INTEGER,
            category TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER DEFAULT 0,
            min_alert_level INTEGER DEFAULT 10,
            expiry_date TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            is_synced INTEGER DEFAULT 0,
            FOREIGN KEY (clinic_id) REFERENCES clinics (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_inventory_uuid ON inventory(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_inventory_clinic ON inventory(clinic_id);')
    
    # 9. price_list
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE,
            clinic_id INTEGER,
            inventory_id INTEGER,
            item_type TEXT NOT NULL,
            item_name TEXT NOT NULL,
            price INTEGER NOT NULL,
            quantity INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            is_synced INTEGER DEFAULT 0,
            FOREIGN KEY (clinic_id) REFERENCES clinics (id),
            FOREIGN KEY (inventory_id) REFERENCES inventory (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_price_list_uuid ON price_list(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_price_list_clinic ON price_list(clinic_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_price_list_inventory ON price_list(inventory_id);')
    
    # 10. expenses
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE,
            clinic_id INTEGER,
            expense_date TEXT NOT NULL,
            category TEXT DEFAULT 'Other',
            description TEXT NOT NULL,
            amount INTEGER NOT NULL,
            created_at TEXT,
            is_synced INTEGER DEFAULT 0,
            FOREIGN KEY (clinic_id) REFERENCES clinics (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_expenses_uuid ON expenses(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_expenses_clinic ON expenses(clinic_id);')
    
    # 10.5 PRICE HISTORY
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            price_list_id INTEGER NOT NULL,
            item_type TEXT NOT NULL,
            item_name TEXT NOT NULL,
            old_price INTEGER NOT NULL,
            new_price INTEGER NOT NULL,
            old_quantity INTEGER DEFAULT 0,
            new_quantity INTEGER DEFAULT 0,
            changed_at TEXT,
            changed_by_staff_id INTEGER,
            FOREIGN KEY (price_list_id) REFERENCES price_list (id),
            FOREIGN KEY (changed_by_staff_id) REFERENCES staff (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_price_history_list ON price_history(price_list_id);')
    
    # 11. audit_log
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER,
            clinic_id INTEGER,
            action TEXT NOT NULL,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            old_value TEXT,
            new_value TEXT,
            timestamp TEXT,
            FOREIGN KEY (staff_id) REFERENCES staff (id),
            FOREIGN KEY (clinic_id) REFERENCES clinics (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_log_staff ON audit_log(staff_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_log_clinic ON audit_log(clinic_id);')
    
    # 12. currencies
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS currencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            subunit_name TEXT NOT NULL,
            subunit_ratio INTEGER DEFAULT 100,
            is_active INTEGER DEFAULT 1,
            is_default INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
    ''')
    
    # Insert default currencies
    now = datetime.datetime.now().isoformat()
    cursor.execute('''
        INSERT OR IGNORE INTO currencies (code, name, symbol, subunit_name, subunit_ratio, is_default, created_at)
        VALUES 
        ('MWK', 'Malawian Kwacha', 'MK', 'Tambala', 100, 1, ?),
        ('USD', 'US Dollar', '$', 'Cent', 100, 0, ?),
        ('EUR', 'Euro', '€', 'Cent', 100, 0, ?),
        ('GBP', 'British Pound', '£', 'Pence', 100, 0, ?),
        ('ZAR', 'South African Rand', 'R', 'Cent', 100, 0, ?)
    ''', (now, now, now, now, now))
    
    # AUTO-CREATE DEFAULT ADMIN
    cursor.execute("SELECT COUNT(*) FROM staff")
    staff_count = cursor.fetchone()[0]

    if staff_count == 0:
        default_username = "admin"
        default_password = "admin123"
        hashed_pw = generate_password_hash(default_password)

        cursor.execute('''
            INSERT INTO staff (uuid, full_name, role, username, password_hash, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(uuid.uuid4()),
            "System Administrator",
            "Admin",
            default_username,
            hashed_pw,
            1,
            datetime.datetime.now().isoformat()
        ))
        print(f"✅ Default admin created! Username: '{default_username}', Password: '{default_password}'")
    
    conn.commit()
    conn.close()

if not os.path.exists(DATABASE):
    init_db()

# ------------------------------------------------------------------
# API ROUTES - AUTH
# ------------------------------------------------------------------
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, role, password_hash FROM staff WHERE username = ? AND is_active = 1", (username,))
    user = cursor.fetchone()
    
    if user and check_password_hash(user[2], password):
        session['staff_id'] = user[0]
        session['role'] = user[1]
        
        cursor.execute('''
            SELECT clinics.id, clinics.clinic_name, staff_clinics.role
            FROM clinics
            JOIN staff_clinics ON clinics.id = staff_clinics.clinic_id
            WHERE staff_clinics.staff_id = ?
        ''', (session['staff_id'],))
        clinics = cursor.fetchall()
        
        session['user_clinics'] = [tuple(c) for c in clinics]
        
        if len(clinics) == 0:
            return jsonify({'success': True, 'needs_clinic': True})
        
        if len(clinics) == 1:
            session['clinic_id'] = clinics[0][0]
            session['clinic_name'] = clinics[0][1]
            session['role'] = clinics[0][2]
            return jsonify({
                'success': True,
                'staff_id': session['staff_id'],
                'role': session['role'],
                'clinic_id': session['clinic_id'],
                'clinic_name': session['clinic_name'],
                'clinics': [{'id': c[0], 'name': c[1], 'role': c[2]} for c in clinics]
            })
        
        return jsonify({
            'success': True,
            'needs_clinic_selection': True,
            'clinics': [{'id': c[0], 'name': c[1], 'role': c[2]} for c in clinics]
        })
    
    return jsonify({'success': False, 'error': 'Invalid username or password'}), 401

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/verify', methods=['GET'])
def api_verify():
    if 'staff_id' not in session:
        return jsonify({'success': False}), 401
    return jsonify({
        'success': True,
        'staff_id': session['staff_id'],
        'role': session['role'],
        'clinic_id': session.get('clinic_id'),
        'clinic_name': session.get('clinic_name')
    })

# ------------------------------------------------------------------
# API ROUTES - CLINIC
# ------------------------------------------------------------------
@app.route('/api/clinics', methods=['GET'])
@require_permission('clinic.view')
def api_clinics():
    return jsonify({
        'clinics': [{'id': c[0], 'name': c[1], 'role': c[2]} for c in session.get('user_clinics', [])]
    })

@app.route('/api/clinics/select', methods=['POST'])
def api_select_clinic():
    data = request.get_json()
    clinic_id = data.get('clinic_id')
    
    clinics = session.get('user_clinics', [])
    match = next((c for c in clinics if c[0] == int(clinic_id)), None)
    if match:
        session['clinic_id'] = match[0]
        session['clinic_name'] = match[1]
        session['role'] = match[2]
        return jsonify({
            'success': True,
            'clinic_id': match[0],
            'clinic_name': match[1],
            'role': match[2]
        })
    return jsonify({'success': False, 'error': 'Invalid clinic'}), 400

@app.route('/api/clinics/setup', methods=['POST'])
@require_permission('clinic.setup')
def api_setup_clinic():
    if 'staff_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    
    data = request.get_json()
    clinic_name = data.get('clinic_name', '').strip()
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip()
    address = data.get('address', '').strip()
    
    if not clinic_name:
        return jsonify({'success': False, 'error': 'Clinic name is required'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO clinics (uuid, clinic_name, phone, email, address, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_name, phone, email, address, datetime.datetime.now().isoformat()))
    clinic_id = cursor.lastrowid
    cursor.execute('''
        INSERT INTO staff_clinics (staff_id, clinic_id, role)
        VALUES (?, ?, ?)
    ''', (session['staff_id'], clinic_id, 'Admin'))
    conn.commit()
    
    session['clinic_id'] = clinic_id
    session['clinic_name'] = clinic_name
    session['role'] = 'Admin'
    session['user_clinics'] = session.get('user_clinics', []) + [(clinic_id, clinic_name, 'Admin')]
    
    return jsonify({
        'success': True,
        'clinic_id': clinic_id,
        'clinic_name': clinic_name,
        'role': 'Admin'
    })

@app.route('/api/clinics/create', methods=['POST'])
@require_permission('clinic.create')
def api_create_clinic():
    if 'staff_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    
    data = request.get_json()
    clinic_name = data.get('clinic_name', '').strip()
    phone = data.get('phone', '').strip()
    
    if not clinic_name:
        return jsonify({'success': False, 'error': 'Clinic name is required'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO clinics (uuid, clinic_name, phone, created_at)
        VALUES (?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_name, phone, datetime.datetime.now().isoformat()))
    new_clinic_id = cursor.lastrowid
    cursor.execute('''
        INSERT INTO staff_clinics (staff_id, clinic_id, role)
        VALUES (?, ?, ?)
    ''', (session['staff_id'], new_clinic_id, 'Admin'))
    conn.commit()
    
    current_clinics = session.get('user_clinics', [])
    session['user_clinics'] = current_clinics + [(new_clinic_id, clinic_name, 'Admin')]
    
    return jsonify({
        'success': True,
        'clinic_id': new_clinic_id,
        'clinic_name': clinic_name
    })
    
    
#API ROUTES CURRENCIES 
    
@app.route('/api/currencies', methods=['GET'])
@require_permission('clinic.view')
def api_currencies():
    """Get all available currencies"""
    currencies = get_all_currencies()
    return jsonify({
        'currencies': [{
            'id': c[0],
            'code': c[1],
            'name': c[2],
            'symbol': c[3],
            'subunit_ratio': c[4],
            'is_default': c[5]
        } for c in currencies]
    })

@app.route('/api/clinic/currency', methods=['GET'])
@require_permission('clinic.view')
def api_clinic_currency():
    """Get current clinic's currency"""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    currency = get_clinic_currency(clinic_id)
    return jsonify(currency)

@app.route('/api/clinic/currency', methods=['POST'])
@require_permission('clinic.setup')
def api_set_clinic_currency():
    """Change a clinic's currency"""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    data = request.get_json(silent=True) or {}
    currency_id = data.get('currency_id')

    if not currency_id:
        return jsonify({'error': 'currency_id required'}), 400

    conn = get_db()
    cursor = conn.cursor()

    # Confirm the currency actually exists and is active before switching to it,
    # otherwise the clinic silently ends up pointing at a non-existent currency_id
    # and every future format_amount()/get_clinic_currency() call falls back to MWK
    # with no indication anything went wrong.
    cursor.execute('SELECT id FROM currencies WHERE id = ? AND is_active = 1', (currency_id,))
    if not cursor.fetchone():
        return jsonify({'error': 'invalid_currency'}), 400

    cursor.execute('UPDATE clinics SET currency_id = ? WHERE id = ?', (currency_id, clinic_id))
    conn.commit()

    return jsonify({'success': True, 'currency': get_clinic_currency(clinic_id)})

# ------------------------------------------------------------------
# API ROUTES - DASHBOARD
# ------------------------------------------------------------------
@app.route('/api/dashboard', methods=['GET'])
@require_permission('dashboard.view')
def api_dashboard():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    stats = get_dashboard_data(clinic_id)
    stats['fetched_at'] = datetime.datetime.now().isoformat()
    stats['currency'] = get_clinic_currency(clinic_id)
    return jsonify(stats)
    
    
@app.route('/api/patients', methods=['GET'])
@require_permission('patient.view')
def api_patients():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, phone FROM patients WHERE clinic_id = ? AND is_active = 1 ORDER BY name", (clinic_id,))
    patients = cursor.fetchall()
    return jsonify({'patients': [{'id': r[0], 'name': r[1], 'phone': r[2]} for r in patients]})    

# ------------------------------------------------------------------
# API ROUTES - QUEUE
# ------------------------------------------------------------------
@app.route('/api/queue', methods=['GET'])
@require_permission('queue.view')
def api_queue():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    queue_list = get_queue_data(clinic_id)
    patients = [
        {
            'id': row[0],
            'name': row[1],
            'sex': row[2],
            'phone': row[3],
            'appointment_type': row[4],
            'status': row[5]
        }
        for row in queue_list
    ]
    return jsonify({
        'patients': patients,
        'total_in_queue': len(patients),
        'fetched_at': datetime.datetime.now().isoformat()
    })

@app.route('/api/queue/register', methods=['POST'])
@require_permission('queue.register_patient')
def api_queue_register():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'error': 'invalid_json'}), 400

    client_uuid = data.get('client_uuid')
    name = data.get('name')
    if not client_uuid or not name:
        return jsonify({'error': 'missing_fields'}), 400

    date_of_birth = data.get('date_of_birth')
    sex = data.get('sex')
    phone = data.get('phone')
    location = data.get('location')
    appointment_type = data.get('appointment_type', 'Walk-In')

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('SELECT id FROM patients WHERE uuid = ?', (client_uuid,))
    existing = cursor.fetchone()
    if existing:
        patient_id = existing[0]
        cursor.execute('''
            SELECT status FROM appointments WHERE patient_id = ? ORDER BY id DESC LIMIT 1
        ''', (patient_id,))
        row = cursor.fetchone()
        status = row[0] if row else 'Waiting'
        return jsonify({
            'status': 'already_processed',
            'patient_id': patient_id,
            'queue_status': status
        }), 200

    now = datetime.datetime.now().isoformat()

    cursor.execute('''
        INSERT INTO patients (uuid, clinic_id, name, date_of_birth, sex, phone, location, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (client_uuid, clinic_id, name, date_of_birth, sex, phone, location, now))
    patient_id = cursor.lastrowid

    queue_status = 'Pending' if appointment_type == 'Appointment' else 'Waiting'

    cursor.execute('''
        INSERT INTO appointments (uuid, clinic_id, patient_id, doctor_id, appointment_date, appointment_type, reason, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        str(uuid.uuid4()), clinic_id, patient_id, session.get('staff_id'),
        now, appointment_type, 'Consultation', queue_status, now
    ))

    conn.commit()
    log_audit('REGISTER_PATIENT', 'patients', patient_id,
              old_value=None, new_value=f"Name: {name}, Sex: {sex} (offline-sync)")

    return jsonify({
        'status': 'processed',
        'patient_id': patient_id,
        'queue_status': queue_status
    }), 201

# ------------------------------------------------------------------
# API ROUTES - INVENTORY
# ------------------------------------------------------------------
@app.route('/api/inventory', methods=['GET'])
@require_permission('inventory.view')
def api_inventory():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    
    rows = get_inventory_data(clinic_id)
    today_str = datetime.date.today().isoformat()
    expiry_cutoff_str = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    
    items = [
        {
            'id': row[0],
            'category': row[1],
            'name': row[2],
            'qty': row[3],
            'min_alert': row[4],
            'expiry': row[5]
        }
        for row in rows
    ]
    
    return jsonify({
        'items': items,
        'fetched_at': datetime.datetime.now().isoformat(),
        'today': today_str,
        'expiry_cutoff': expiry_cutoff_str
    })

@app.route('/api/inventory/add', methods=['POST'])
@require_permission('inventory.add')
def api_inventory_add():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    category = data.get('category')
    item_name = data.get('item_name', '').strip()
    quantity = int(data.get('quantity', 0))
    min_alert = int(data.get('min_alert_level', 10))
    expiry = data.get('expiry_date')

    if quantity < 0:
        return jsonify({'success': False, 'error': 'Quantity cannot be negative'}), 400

    item_name = ' '.join(item_name.split())
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id FROM inventory 
        WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND category = ? AND expiry_date = ? AND clinic_id = ? AND is_active = 1
    """, (item_name, category, expiry, clinic_id))
    existing = cursor.fetchone()

    if existing:
        return jsonify({'success': False, 'error': f'"{item_name}" with this exact expiry date already exists in stock'}), 400

    cursor.execute('''
        INSERT INTO inventory (uuid, clinic_id, category, item_name, quantity, min_alert_level, expiry_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_id, category, item_name, quantity, min_alert, expiry, datetime.datetime.now().isoformat()))

    conn.commit()
    log_audit('ADD_INVENTORY', 'inventory', cursor.lastrowid, 
              old_value=None, new_value=f"{item_name}, Qty: {quantity}")
    return jsonify({'success': True})

@app.route('/api/inventory/edit/<int:inventory_id>', methods=['POST'])
@require_permission('inventory.edit')
def api_inventory_edit(inventory_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    category = data.get('category')
    item_name = data.get('item_name', '').strip()
    quantity = int(data.get('quantity', 0))
    min_alert = int(data.get('min_alert_level', 10))
    expiry = data.get('expiry_date')

    if quantity < 0:
        return jsonify({'success': False, 'error': 'Quantity cannot be negative'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT item_name, category FROM inventory WHERE id = ? AND clinic_id = ? AND is_active = 1",
        (inventory_id, clinic_id)
    )
    original = cursor.fetchone()

    if original is None:
        return jsonify({'success': False, 'error': 'Item not found'}), 404

    original_name, original_category = original
    item_name = ' '.join(item_name.split())
    now = datetime.datetime.now().isoformat()

    cursor.execute('''
        UPDATE inventory 
        SET category = ?, item_name = ?, quantity = quantity + ?, min_alert_level = ?, expiry_date = ?, updated_at = ?
        WHERE id = ? AND clinic_id = ? AND is_active = 1
    ''', (category, item_name, quantity, min_alert, expiry, now, inventory_id, clinic_id))

    cursor.execute('''
        UPDATE inventory
        SET item_name = ?, category = ?, min_alert_level = ?, updated_at = ?
        WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?))
          AND category = ?
          AND clinic_id = ?
          AND is_active = 1
          AND id != ?
    ''', (item_name, category, min_alert, now,
          original_name, original_category, clinic_id, inventory_id))

    cursor.execute('''
        UPDATE price_list 
        SET item_name = ?, updated_at = ? 
        WHERE inventory_id IN (
            SELECT id FROM inventory
            WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND category = ? AND clinic_id = ? AND is_active = 1
        )
    ''', (item_name, now, item_name, category, clinic_id))

    cursor.execute("""
        SELECT id, quantity FROM inventory 
        WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND category = ? AND expiry_date = ? AND clinic_id = ? AND is_active = 1
        ORDER BY id ASC
    """, (item_name, category, expiry, clinic_id))
    rows = cursor.fetchall()

    if len(rows) > 1:
        first_id = rows[0][0]
        total_qty = sum(row[1] for row in rows)
        cursor.execute("UPDATE inventory SET quantity = ? WHERE id = ?", (total_qty, first_id))
        for dup_id in [row[0] for row in rows[1:]]:
            cursor.execute(
                "UPDATE price_list SET inventory_id = ?, updated_at = ? WHERE inventory_id = ?",
                (first_id, now, dup_id)
            )
            cursor.execute("DELETE FROM inventory WHERE id = ?", (dup_id,))

    conn.commit()
    log_audit('EDIT_INVENTORY', 'inventory', inventory_id, 
              old_value=f"Original: {original_name}", 
              new_value=f"New: {item_name}, Qty +{quantity}")
    return jsonify({'success': True})

@app.route('/api/inventory/reduce/<int:item_id>', methods=['POST'])
@require_permission('inventory.reduce')
def api_inventory_reduce(item_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    amount_to_remove = data.get('amount')

    try:
        amount_to_remove = int(amount_to_remove)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid amount.'}), 400

    if amount_to_remove <= 0:
        return jsonify({'success': False, 'error': 'Amount must be greater than 0.'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT quantity, item_name FROM inventory WHERE id = ? AND clinic_id = ? AND is_active = 1", (item_id, clinic_id))
    row = cursor.fetchone()

    if row is None:
        return jsonify({'success': False, 'error': 'Item not found.'}), 404

    current_qty, item_name = row

    if amount_to_remove > current_qty:
        return jsonify({'success': False, 'error': f'Cannot remove {amount_to_remove} -- only {current_qty} in stock.'}), 400

    new_qty = current_qty - amount_to_remove

    cursor.execute(
        "UPDATE inventory SET quantity = ?, updated_at = ? WHERE id = ?",
        (new_qty, datetime.datetime.now().isoformat(), item_id)
    )
    conn.commit()

    log_audit('REDUCE_INVENTORY', 'inventory', item_id,
              old_value=f"{item_name}, Qty: {current_qty}",
              new_value=f"{item_name}, Qty: {new_qty} (-{amount_to_remove})")

    return jsonify({'success': True, 'new_quantity': new_qty})

# ------------------------------------------------------------------
# API ROUTES - PRICE LIST
# ------------------------------------------------------------------
@app.route('/api/price_list', methods=['GET'])
@require_permission('price_list.view')
def api_price_list():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    rows = get_price_list_data(clinic_id)
    items = [
        {
            'id': row[0],
            'item_type': row[1],
            'item_name': row[2],
            'price': row[3],
            'quantity': row[4],
            'usable_qty': row[5],
            'expired_qty': row[6],
            'no_stock_concept': row[7]
        }
        for row in rows
    ]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT item_name, category FROM inventory WHERE clinic_id = ? AND is_active = 1", (clinic_id,))
    inventory_items = [{'name': r[0], 'category': r[1]} for r in cursor.fetchall()]

    return jsonify({
        'items': items,
        'inventory_items': inventory_items,
        'fetched_at': datetime.datetime.now().isoformat(),
        'currency': get_clinic_currency(clinic_id)
    })

@app.route('/api/price_list/add', methods=['POST'])
@require_permission('price_list.create')
def api_price_list_add():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    item_type = data.get('item_type')
    item_name = data.get('item_name')
    
    # Get clinic currency to determine subunit ratio
    currency = get_clinic_currency(clinic_id)
    subunit_ratio = currency['subunit_ratio']
    
    # Convert price to subunit (e.g., 100 for MWK tambala, 100 for USD cents, etc.)
    price = int(float(data.get('price', 0)) * subunit_ratio)
    quantity = int(data.get('quantity', 1))

    if price < 0 or quantity < 0:
        return jsonify({'success': False, 'error': 'Price and quantity cannot be negative.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM inventory WHERE item_name = ? AND category = ? AND clinic_id = ? AND is_active = 1",
        (item_name, item_type, clinic_id)
    )
    inv_row = cursor.fetchone()
    inventory_id = inv_row[0] if inv_row else None
    
    cursor.execute('''
        INSERT INTO price_list (uuid, clinic_id, inventory_id, item_type, item_name, price, quantity, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_id, inventory_id, item_type, item_name, price, quantity, datetime.datetime.now().isoformat()))
    conn.commit()
    return jsonify({'success': True})

@app.route('/api/price_list/update/<int:item_id>', methods=['POST'])
@require_permission('price_list.edit')
def api_price_list_update(item_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400
    
    data = request.get_json()
    new_price = data.get('price')
    new_qty = data.get('quantity')

    try:
        if new_price is None or float(new_price) < 0:
            return jsonify({'success': False, 'error': 'Price cannot be negative.'}), 400
        if new_qty is None or int(new_qty) < 0:
            return jsonify({'success': False, 'error': 'Quantity cannot be negative.'}), 400
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid price or quantity.'}), 400

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT price, quantity, item_type, item_name FROM price_list WHERE id = ? AND clinic_id = ?", (item_id, clinic_id))
        row = cursor.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Item not found.'}), 404

        old_price, old_qty, item_type, item_name = row
        
        # Get clinic currency
        currency = get_clinic_currency(clinic_id)
        subunit_ratio = currency['subunit_ratio']
        
        # Convert new_price to subunit
        new_price_subunit = int(float(new_price) * subunit_ratio)

        cursor.execute('''
            UPDATE price_list 
            SET price = ?, quantity = ?, updated_at = ? 
            WHERE id = ? AND clinic_id = ?
        ''', (new_price_subunit, new_qty, datetime.datetime.now().isoformat(), item_id, clinic_id))

        if old_price != new_price_subunit or old_qty != new_qty:
            cursor.execute('''
                INSERT INTO price_history (price_list_id, item_type, item_name, old_price, new_price, old_quantity, new_quantity, changed_by_staff_id, changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (item_id, item_type, item_name, old_price, new_price_subunit, old_qty, new_qty, session.get('staff_id'), datetime.datetime.now().isoformat()))

        conn.commit()
        log_audit('UPDATE_PRICE', 'price_list', item_id, 
                  old_value=f"Price: {old_price}, Qty: {old_qty}", 
                  new_value=f"Price: {new_price_subunit}, Qty: {new_qty}")
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/price_list/delete/<int:item_id>', methods=['DELETE'])
@require_permission('price_list.delete')
def api_price_list_delete(item_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE price_list 
        SET is_active = 0, updated_at = ? 
        WHERE id = ? AND clinic_id = ?
    ''', (datetime.datetime.now().isoformat(), item_id, clinic_id))
    conn.commit()
    log_audit('DELETE_PRICE', 'price_list', item_id, 
              old_value='Active', new_value='Deactivated (Soft Delete)')
    return jsonify({'success': True})

@app.route('/api/price_list/history/<int:item_id>', methods=['GET'])
@require_permission('price_list.view_history')
def api_price_list_history(item_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'No clinic selected'}), 403

    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT item_type, item_name, old_price, new_price, old_quantity, new_quantity, changed_at, staff.full_name
        FROM price_history
        LEFT JOIN staff ON price_history.changed_by_staff_id = staff.id
        WHERE price_list_id = ?
        ORDER BY changed_at DESC
    ''', (item_id,))
    rows = cursor.fetchall()
    
    return jsonify({
        'history': [{
            'type': r[0],
            'name': r[1],
            'old_price': r[2],
            'new_price': r[3],
            'old_qty': r[4],
            'new_qty': r[5],
            'changed_at': r[6],
            'changed_by': r[7] or 'System'
        } for r in rows],
        'currency': get_clinic_currency(clinic_id)
    })

# ------------------------------------------------------------------
# API ROUTES - CASHIER
# ------------------------------------------------------------------
@app.route('/api/cashier/list', methods=['GET'])
@require_permission('cashier.view')
def api_cashier_list():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            visits.id,
            visits.uuid,
            visits.visit_date,
            visits.diagnosis,
            visits.total_fee,
            visits.amount_paid,
            visits.loan_witness,
            visits.status,
            visits.discount_amount,
            visits.discount_reason,
            visits.loan_due_date,
            patients.name AS patient_name,
            patients.id AS patient_id
        FROM visits
        JOIN patients ON visits.patient_id = patients.id
        WHERE visits.clinic_id = ?
          AND visits.status = 'Ready for Cashier'
          AND (visits.is_retail IS NULL OR visits.is_retail = 0)
        ORDER BY visits.created_at ASC
    ''', (clinic_id,))
    cashier_list = cursor.fetchall()

    visit_ids = [r[0] for r in cashier_list]
    items_by_visit = {}
    if visit_ids:
        qmarks = ','.join(['?'] * len(visit_ids))
        cursor.execute(f'''
            SELECT visit_id, item_name, item_type, quantity
            FROM visit_items
            WHERE visit_id IN ({qmarks})
        ''', visit_ids)
        from collections import defaultdict
        items_by_visit = defaultdict(list)
        for v_id, item_name, item_type, quantity in cursor.fetchall():
            items_by_visit[v_id].append({
                'name': item_name,
                'category': item_type,
                'quantity': quantity
            })

    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'cashier_list': [{
            'id': r[0],
            'uuid': r[1],
            'visit_date': r[2],
            'diagnosis': r[3],
            'total_fee': r[4],
            'amount_paid': r[5],
            'loan_witness': r[6],
            'status': r[7],
            'discount_amount': r[8],
            'discount_reason': r[9],
            'loan_due_date': r[10],
            'patient_name': r[11],
            'patient_id': r[12],
            'items_sold': items_by_visit.get(r[0], [])
        } for r in cashier_list],
        'currency': currency
    })

@app.route('/api/cashier/view/<int:visit_id>', methods=['GET'])
@require_permission('cashier.view')
def api_cashier_view(visit_id):
    conn = get_db()
    cursor = conn.cursor()
    
    clinic_id = get_current_clinic_id()
    cursor.execute('''
        SELECT visits.total_fee, visits.amount_paid, visits.diagnosis, visits.status,
               patients.name AS patient_name
        FROM visits
        LEFT JOIN patients ON visits.patient_id = patients.id
        WHERE visits.id = ? AND visits.clinic_id = ?
    ''', (visit_id, clinic_id))
    visit = cursor.fetchone()
    
    if not visit:
        return jsonify({'error': 'Visit not found'}), 404

    cursor.execute('''
        SELECT item_type, item_name, quantity, price_per_unit, total_line_price
        FROM visit_items
        WHERE visit_id = ?
    ''', (visit_id,))
    items = cursor.fetchall()
    
    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'patient': visit[4] or '🏪 Retail Sale',
        'diagnosis': visit[2],
        'total': visit[0],
        'paid': visit[1],
        'status': visit[3],
        'items': [{'type': i[0], 'name': i[1], 'qty': i[2], 'unit_price': i[3], 'total': i[4]} for i in items],
        'currency': currency
    })

@app.route('/api/cashier/process/<int:visit_id>', methods=['POST'])
@require_permission('payment.process_full')
def api_cashier_process(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    
    data = request.get_json()
    payment_mode = data.get('payment_mode')
    
    if payment_mode not in ['full', 'loan']:
        return jsonify({'success': False, 'error': 'Invalid payment mode.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT total_fee, amount_paid, status, patient_id, appointment_id, loan_witness
            FROM visits WHERE id = ? AND clinic_id = ?
        ''', (visit_id, clinic_id))
        visit_row = cursor.fetchone()
        
        if not visit_row:
            return jsonify({'success': False, 'error': 'Visit not found.'}), 404
        
        total_fee, current_paid, status, patient_id, appointment_id, existing_witness = visit_row
        
        if status not in ['Ready for Cashier', 'Loan Active']:
            return jsonify({'success': False, 'error': 'Visit is not in a payable state.'}), 400
        
        now = datetime.datetime.now().isoformat()

        cursor.execute('''
            UPDATE visits SET status = 'Processing', updated_at = ?
            WHERE id = ? AND status IN ('Ready for Cashier', 'Loan Active')
        ''', (now, visit_id))

        if cursor.rowcount == 0:
            return jsonify({'success': False, 'error': 'This payment was already processed.'}), 400

        rounded_total = data.get('rounded_total')
        round_to = data.get('round_to') or 0

        # Get clinic currency for rounding
        currency = get_clinic_currency(clinic_id)
        subunit_ratio = currency['subunit_ratio']

        if rounded_total is not None and round_to:
            try:
                rounded_total = int(rounded_total)
                round_to_subunit = int(round_to) * subunit_ratio
            except (TypeError, ValueError):
                conn.rollback()
                return jsonify({'success': False, 'error': 'Invalid rounding value.'}), 400

            if round_to_subunit <= 0 or round_to_subunit % (subunit_ratio * 100) != 0:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Invalid rounding step.'}), 400

            expected_rounded = math.floor(total_fee / round_to_subunit + 0.5) * round_to_subunit
            if rounded_total != expected_rounded:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Rounded total does not match the selected rounding step.'}), 400

            cursor.execute('''
                UPDATE visits SET total_fee = ?, updated_at = ?
                WHERE id = ?
            ''', (rounded_total, now, visit_id))
            total_fee = rounded_total
        
        payment_channel = data.get('payment_channel', 'Cash')
        payment_reference = data.get('payment_reference')
        medical_aid_company = data.get('medical_aid_company')
        
        cursor.execute('''
            UPDATE visits
            SET payment_channel = ?, payment_reference = ?, medical_aid_company = ?
            WHERE id = ?
        ''', (payment_channel, payment_reference, medical_aid_company, visit_id))
        
        if payment_mode == 'full':
            discount_amount = int(data.get('discount_amount') or 0)
            discount_reason = (data.get('discount_reason') or '').strip()

            if discount_amount < 0:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Invalid discount amount.'}), 400

            if discount_amount > total_fee:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Discount cannot exceed the total fee.'}), 400

            if discount_amount > 0 and not discount_reason:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Discount reason is required.'}), 400

            amount_due = total_fee - discount_amount

            if current_paid > amount_due:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Amount already paid exceeds the discounted total. Adjust the discount first.'}), 400

            collected_now = amount_due - current_paid

            cursor.execute('''
                UPDATE visits
                SET amount_paid = ?,
                    discount_amount = ?,
                    discount_reason = ?,
                    status = 'Paid',
                    updated_at = ?
                WHERE id = ?
            ''', (amount_due, discount_amount, discount_reason or None, now, visit_id))

            if current_paid > 0 and collected_now > 0:
                cursor.execute('''
                    INSERT INTO loan_payments (uuid, visit_id, payment_date, amount, created_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (str(uuid.uuid4()), visit_id, now, collected_now, now))
            
        elif payment_mode == 'loan':
            amount_paid_now = int(data.get('amount_paid') or 0)
            witness_id = data.get('witness_id')
            loan_due_date = data.get('loan_due_date')
            discount_amount = int(data.get('discount_amount') or 0)
            discount_reason = (data.get('discount_reason') or '').strip()

            if discount_amount < 0:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Invalid discount amount.'}), 400

            if discount_amount > total_fee:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Discount cannot exceed the total fee.'}), 400

            if discount_amount > 0 and not discount_reason:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Discount reason is required.'}), 400

            effective_total = total_fee - discount_amount

            if amount_paid_now is None or amount_paid_now < 0:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Invalid payment amount.'}), 400

            if current_paid > effective_total:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Amount already paid exceeds the discounted total. Adjust the discount first.'}), 400

            new_total_paid = current_paid + amount_paid_now

            if new_total_paid >= effective_total:
                conn.rollback()
                return jsonify({'success': False, 'error': 'This payment would cover the full remaining balance. Use Full Payment instead.'}), 400
            
            if not witness_id:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Witness is required for loans.'}), 400
            
            if loan_due_date and loan_due_date.strip():
                try:
                    datetime.datetime.strptime(loan_due_date, '%Y-%m-%d')
                except ValueError:
                    conn.rollback()
                    return jsonify({'success': False, 'error': 'Invalid due date format. Use YYYY-MM-DD.'}), 400
            else:
                loan_due_date = None

            cursor.execute("SELECT full_name FROM staff WHERE id = ? AND is_active = 1", (witness_id,))
            witness_row = cursor.fetchone()
            witness_name = witness_row[0] if witness_row else 'Unknown Staff'
            
            cursor.execute('''
                UPDATE visits
                SET amount_paid = ?,
                    loan_witness = ?,
                    loan_due_date = ?,
                    discount_amount = ?,
                    discount_reason = ?,
                    status = 'Loan Active',
                    updated_at = ?
                WHERE id = ?
            ''', (new_total_paid, witness_name, loan_due_date, discount_amount, discount_reason or None, now, visit_id))
            
            cursor.execute('''
                INSERT INTO loan_payments (uuid, visit_id, payment_date, amount, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (str(uuid.uuid4()), visit_id, now, amount_paid_now, now))
        
        # INVENTORY DEDUCTION: FEFO (First-Expired, First-Out)
        # Fixed: uses inventory_id instead of text matching
        cursor.execute('''
            SELECT inventory_id, quantity
            FROM visit_items
            WHERE visit_items.visit_id = ?
              AND visit_items.inventory_id IS NOT NULL
        ''', (visit_id,))
        items_to_deduct = cursor.fetchall()
        
        for inventory_id, qty_to_deduct in items_to_deduct:
            # price_list.inventory_id points at one specific batch row, but
            # a given item_name+category can have several batches (different
            # expiry dates) in this clinic. Look up the item's identity first,
            # then pull ALL of that clinic's batches for it -- this matches
            # the availability check in api_visit_create, which also sums
            # quantity across every batch sharing this item_name+category.
            cursor.execute('''
                SELECT item_name, category
                FROM inventory
                WHERE id = ? AND clinic_id = ? AND is_active = 1
            ''', (inventory_id, clinic_id))
            linked_item = cursor.fetchone()

            if linked_item is None:
                print(f"WARNING: inventory_id {inventory_id} not found in clinic {clinic_id}; skipped deduction")
                continue

            linked_name, linked_category = linked_item

            cursor.execute('''
                SELECT id, quantity, expiry_date
                FROM inventory
                WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?))
                  AND category = ?
                  AND clinic_id = ?
                  AND is_active = 1
                  AND quantity > 0
                ORDER BY expiry_date ASC
            ''', (linked_name, linked_category, clinic_id))
            batches = cursor.fetchall()
            
            remaining = qty_to_deduct
            
            for batch_id, batch_qty, expiry in batches:
                if remaining <= 0:
                    break
                
                if batch_qty > 0:
                    deduct = min(remaining, batch_qty)
                    new_qty = batch_qty - deduct
                    cursor.execute('''
                        UPDATE inventory
                        SET quantity = ?, updated_at = ?
                        WHERE id = ?
                    ''', (new_qty, now, batch_id))
                    remaining -= deduct
            
            if remaining > 0:
                print(f"WARNING: Could not fully deduct inventory_id {inventory_id} ({linked_name}), still {remaining} units short across all batches")
        
        conn.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cashier/send_back/<int:visit_id>', methods=['POST'])
@require_permission('cashier.send_back')
def api_cashier_send_back(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    reason = (data.get('reason') or '').strip() or None

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT visits.status, visits.appointment_id, visits.is_retail, patients.name
        FROM visits
        LEFT JOIN patients ON visits.patient_id = patients.id
        WHERE visits.id = ? AND visits.clinic_id = ?
    ''', (visit_id, clinic_id))
    row = cursor.fetchone()

    if row is None:
        return jsonify({'success': False, 'error': 'Visit not found.'}), 404

    status, appointment_id, is_retail, patient_name = row

    if status != 'Ready for Cashier':
        return jsonify({'success': False, 'error': f'This visit is "{status}" and can no longer be sent back (already paid or on a loan).'}), 400

    cursor.execute('''
        SELECT price_list_id, item_type, item_name, quantity, price_per_unit
        FROM visit_items
        WHERE visit_id = ?
    ''', (visit_id,))
    items = [
        {
            'price_list_id': r[0],
            'type': r[1],
            'name': r[2],
            'qty': r[3],
            'price_per_unit': r[4]
        }
        for r in cursor.fetchall()
    ]

    now = datetime.datetime.now().isoformat()

    if is_retail:
        cursor.execute('''
            UPDATE visits SET status = 'Cancelled', return_reason = ?, updated_at = ?
            WHERE id = ? AND status = 'Ready for Cashier'
        ''', (reason, now, visit_id))

        if cursor.rowcount == 0:
            return jsonify({'success': False, 'error': 'Could not send this sale back -- it may have just been paid.'}), 400

        conn.commit()
        log_audit('SEND_BACK_RETAIL', 'visits', visit_id,
                  old_value='Ready for Cashier',
                  new_value=f"Sent back to Retail cart. Reason: {reason or '(none given)'}")
        return jsonify({'success': True, 'returned_to': 'retail', 'items': items})

    if appointment_id is None:
        return jsonify({'success': False, 'error': 'This visit has no linked appointment to send back to.'}), 400

    cursor.execute('''
        UPDATE visits SET status = 'Returned to Doctor', return_reason = ?, updated_at = ? WHERE id = ?
    ''', (reason, now, visit_id))

    cursor.execute('''
        UPDATE appointments SET status = 'Returned to Doctor', updated_at = ? WHERE id = ?
    ''', (now, appointment_id))

    conn.commit()
    log_audit('SEND_BACK_TO_DOCTOR', 'visits', visit_id,
              old_value='Ready for Cashier',
              new_value=f"Returned to Doctor. Patient: {patient_name}. Reason: {reason or '(none given)'}")
    return jsonify({'success': True, 'returned_to': 'doctor', 'items': items})

# ------------------------------------------------------------------
# API ROUTES - LOANS
# ------------------------------------------------------------------
@app.route('/api/loans', methods=['GET'])
@require_permission('loan.view_list')
def api_loans():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    loan_list = get_loans_data(clinic_id)
    today_str = datetime.date.today().isoformat()

    loans = []
    for row in loan_list:
        due_date = row[4]
        loans.append({
            'id': row[0],
            'total_fee': row[1],
            'amount_paid': row[2],
            'balance': row[1] - row[2],
            'witness': row[3],
            'due_date': due_date,
            'is_overdue': bool(due_date and due_date < today_str),
            'is_retail': row[6] == 1,
            'patient_name': row[7],
        })

    currency = get_clinic_currency(clinic_id)

    return jsonify({
        'loans': loans,
        'fetched_at': datetime.datetime.now().isoformat(),
        'currency': currency
    })

@app.route('/api/loans/details/<int:visit_id>', methods=['GET'])
@require_permission('loan.view')
def api_loan_details(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            visits.id,
            visits.total_fee,
            visits.amount_paid,
            visits.loan_witness,
            visits.loan_due_date,
            visits.status,
            patients.name AS patient_name,
            patients.id AS patient_id
        FROM visits
        LEFT JOIN patients ON visits.patient_id = patients.id
        WHERE visits.id = ? AND visits.clinic_id = ?
    ''', (visit_id, clinic_id))
    visit = cursor.fetchone()
    
    if not visit:
        return jsonify({'error': 'Visit not found'}), 404
    
    cursor.execute('''
        SELECT payment_date, amount
        FROM loan_payments
        WHERE visit_id = ?
        ORDER BY payment_date ASC
    ''', (visit_id,))
    payments = cursor.fetchall()
    
    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'visit': {
            'id': visit[0],
            'total_fee': visit[1],
            'amount_paid': visit[2],
            'loan_witness': visit[3],
            'loan_due_date': visit[4],
            'status': visit[5],
            'patient_name': visit[6],
            'patient_id': visit[7]
        },
        'payments': [{'date': p[0], 'amount': p[1]} for p in payments],
        'currency': currency
    })

@app.route('/api/loans/pay/<int:visit_id>', methods=['POST'])
@require_permission('loan.record_payment')
def api_loan_pay(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    
    data = request.get_json()
    amount = data.get('amount')
    
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid payment amount.'}), 400
    
    if amount <= 0:
        return jsonify({'success': False, 'error': 'Amount must be greater than 0.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT total_fee, amount_paid, status, discount_amount
            FROM visits WHERE id = ? AND clinic_id = ?
        ''', (visit_id, clinic_id))
        visit = cursor.fetchone()
        
        if not visit:
            return jsonify({'success': False, 'error': 'Visit not found.'}), 404
        
        total_fee, current_paid, status, discount_amount = visit
        effective_total = total_fee - (discount_amount or 0)
        
        if status != 'Loan Active':
            return jsonify({'success': False, 'error': 'This visit is not an active loan.'}), 400
        
        new_total_paid = current_paid + amount
        
        if new_total_paid > effective_total:
            return jsonify({'success': False, 'error': 'Payment exceeds remaining loan balance.'}), 400
        
        now = datetime.datetime.now().isoformat()
        
        cursor.execute('''
            INSERT INTO loan_payments (uuid, visit_id, payment_date, amount, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), visit_id, now, amount, now))
        
        cursor.execute('''
            UPDATE visits
            SET amount_paid = ?,
                updated_at = ?
            WHERE id = ?
        ''', (new_total_paid, now, visit_id))
        
        if new_total_paid >= effective_total:
            cursor.execute('''
                UPDATE visits
                SET status = 'Paid',
                    loan_witness = NULL,
                    loan_due_date = NULL,
                    updated_at = ?
                WHERE id = ?
            ''', (now, visit_id))
        
        conn.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# ------------------------------------------------------------------
# API ROUTES - RETAIL
# ------------------------------------------------------------------
@app.route('/api/retail/items', methods=['GET'])
@require_permission('retail.view')
def api_retail_items():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    conn = get_db()
    cursor = conn.cursor()
    today_str = datetime.date.today().isoformat()
    cursor.execute('''
        SELECT price_list.id, price_list.item_name, price_list.item_type, price_list.price,
               price_list.quantity AS pack_quantity, price_list.inventory_id,
               COALESCE(stock.usable_qty, 0) AS usable_qty
        FROM price_list
        LEFT JOIN inventory AS linked_item ON price_list.inventory_id = linked_item.id
        LEFT JOIN (
            SELECT LOWER(TRIM(item_name)) AS name_key, category,
                   SUM(CASE WHEN expiry_date >= ? THEN quantity ELSE 0 END) AS usable_qty
            FROM inventory WHERE is_active = 1 AND clinic_id = ? GROUP BY name_key, category
        ) AS stock ON stock.name_key = LOWER(TRIM(linked_item.item_name))
                 AND stock.category = linked_item.category
        WHERE price_list.clinic_id = ? AND price_list.is_active = 1
        ORDER BY price_list.item_name
    ''', (today_str, clinic_id, clinic_id))
    items = cursor.fetchall()
    
    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'items': [{'id': r[0], 'name': r[1], 'type': r[2], 'price': r[3], 'packQty': r[4], 'inventory_id': r[5], 'usableQty': r[6]} for r in items],
        'currency': currency
    })

@app.route('/api/retail/create_draft', methods=['POST'])
@require_permission('retail.create_draft')
def api_retail_create_draft():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400
    
    data = request.get_json()
    cart = data.get('cart', [])
    
    if not cart:
        return jsonify({'success': False, 'error': 'Cart is empty.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        total_fee = 0
        now = datetime.datetime.now().isoformat()
        today_str = datetime.date.today().isoformat()
        visit_items = []

        for item in cart:
            cursor.execute('''
                SELECT price_list.id, price_list.item_name, price_list.item_type, price_list.price, 
                       price_list.quantity AS pack_quantity, price_list.inventory_id,
                       COALESCE(stock.usable_qty, 0) AS usable_qty
                FROM price_list
                LEFT JOIN inventory AS linked_item ON price_list.inventory_id = linked_item.id
                LEFT JOIN (
                    SELECT LOWER(TRIM(item_name)) AS name_key, category,
                           SUM(CASE WHEN expiry_date >= ? THEN quantity ELSE 0 END) AS usable_qty
                    FROM inventory WHERE is_active = 1 AND clinic_id = ? GROUP BY name_key, category
                ) AS stock ON stock.name_key = LOWER(TRIM(linked_item.item_name)) 
                         AND stock.category = linked_item.category
                WHERE price_list.id = ? AND price_list.clinic_id = ? AND price_list.is_active = 1
            ''', (today_str, clinic_id, item['price_list_id'], clinic_id))
            
            row = cursor.fetchone()
            if not row:
                return jsonify({'success': False, 'error': f'Item not found: {item["name"]}'}), 400
            
            pl_id, pl_name, pl_type, pl_price, pl_pack_qty, pl_inv_id, usable_qty = row
            qty_sold = item['qty']

            if pl_inv_id is not None and qty_sold > usable_qty:
                return jsonify({'success': False, 'error': f'Only {usable_qty} units of "{pl_name}" available.'}), 400
            
            pack_qty = pl_pack_qty if pl_pack_qty and pl_pack_qty > 0 else 1
            price_per_unit = pl_price / pack_qty
            line_total = round(price_per_unit * qty_sold)
            total_fee += line_total

            visit_items.append({
                'pl_id': pl_id,
                'pl_name': pl_name,
                'pl_type': pl_type,
                'pl_inv_id': pl_inv_id,
                'qty_sold': qty_sold,
                'price_per_unit': price_per_unit,
                'line_total': line_total
            })
            
        cursor.execute('''
            INSERT INTO visits (uuid, clinic_id, patient_id, doctor_id, appointment_id, visit_date, 
                                diagnosis, total_fee, amount_paid, status, created_at, updated_at, is_retail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), clinic_id, None, None, None, now, 'Retail Sale', total_fee, 0, 'Ready for Cashier', now, now, 1))
        
        visit_id = cursor.lastrowid
        
        for item in visit_items:
            cursor.execute('''
                INSERT INTO visit_items (uuid, visit_id, inventory_id, price_list_id, item_type, item_name,
                                         quantity, price_per_unit, total_line_price, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (str(uuid.uuid4()), visit_id, item['pl_inv_id'], item['pl_id'], item['pl_type'], item['pl_name'], 
                  item['qty_sold'], round(item['price_per_unit']), item['line_total'], now))
        
        conn.commit()
        return jsonify({'success': True, 'visit_id': visit_id})
        
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/retail/pending', methods=['GET'])
@require_permission('retail.view')
def api_retail_pending():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, total_fee, amount_paid, status, created_at
        FROM visits
        WHERE clinic_id = ?
          AND is_retail = 1
          AND status = 'Ready for Cashier'
        ORDER BY created_at DESC
    ''', (clinic_id,))
    rows = cursor.fetchall()

    visit_ids = [r[0] for r in rows]
    items_by_visit = {}
    if visit_ids:
        qmarks = ','.join(['?'] * len(visit_ids))
        cursor.execute(f'''
            SELECT visit_id, item_name, item_type, quantity
            FROM visit_items
            WHERE visit_id IN ({qmarks})
        ''', visit_ids)
        from collections import defaultdict
        items_by_visit = defaultdict(list)
        for v_id, item_name, item_type, quantity in cursor.fetchall():
            items_by_visit[v_id].append({
                'name': item_name,
                'category': item_type,
                'quantity': quantity
            })

    return jsonify({
        'success': True,
        'pending': [
            {
                'visit_id': r[0],
                'total_fee': r[1],
                'amount_paid': r[2],
                'status': r[3],
                'created_at': r[4],
                'items_sold': items_by_visit.get(r[0], [])
            }
            for r in rows
        ]
    })

@app.route('/api/retail/cancel/<int:visit_id>', methods=['POST'])
@require_permission('retail.cancel_draft')
def api_retail_cancel(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT status, amount_paid, is_retail
        FROM visits
        WHERE id = ? AND clinic_id = ?
    ''', (visit_id, clinic_id))
    row = cursor.fetchone()

    if not row:
        return jsonify({'success': False, 'error': 'Visit not found.'}), 404

    status, amount_paid, is_retail = row

    if not is_retail:
        return jsonify({'success': False, 'error': 'This is not a retail sale.'}), 400

    if amount_paid and amount_paid > 0:
        return jsonify({'success': False, 'error': 'This sale already has a payment recorded and cannot be cancelled. Resume and complete it instead.'}), 400

    now = datetime.datetime.now().isoformat()
    cursor.execute('''
        UPDATE visits
        SET status = 'Cancelled', updated_at = ?
        WHERE id = ? AND status = 'Ready for Cashier'
    ''', (now, visit_id))

    if cursor.rowcount == 0:
        return jsonify({'success': False, 'error': 'Only unpaid drafts can be cancelled.'}), 400

    conn.commit()
    return jsonify({'success': True})

# ------------------------------------------------------------------
# API ROUTES - FINANCE
# ------------------------------------------------------------------
@app.route('/api/finance/stats', methods=['GET'])
@require_permission('finance.view')
def api_finance_stats():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    
    period = request.args.get('period', 'today')
    start_date, end_date, period = get_period_dates(period)
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT SUM(amount_paid) FROM visits 
        WHERE status = 'Paid'
        AND clinic_id = ?
        AND id NOT IN (SELECT DISTINCT visit_id FROM loan_payments)
        AND updated_at >= ? AND updated_at <= ?
    ''', (clinic_id, start_date, end_date))
    total_cash_direct = cursor.fetchone()[0] or 0

    cursor.execute('''
        SELECT SUM(loan_payments.amount)
        FROM loan_payments
        JOIN visits ON loan_payments.visit_id = visits.id
        WHERE visits.clinic_id = ?
        AND loan_payments.payment_date >= ? AND loan_payments.payment_date <= ?
    ''', (clinic_id, start_date, end_date))
    total_cash_from_loans = cursor.fetchone()[0] or 0

    total_cash = total_cash_direct + total_cash_from_loans
    
    cursor.execute('''
        SELECT SUM(total_fee - COALESCE(discount_amount, 0) - amount_paid) FROM visits 
        WHERE status = 'Loan Active'
          AND clinic_id = ?
    ''', (clinic_id,))
    outstanding_loans = cursor.fetchone()[0] or 0
    
    cursor.execute('''
        SELECT SUM(discount_amount) FROM visits 
        WHERE discount_amount > 0
        AND clinic_id = ?
        AND updated_at >= ? AND updated_at <= ?
    ''', (clinic_id, start_date, end_date))
    total_discounts = cursor.fetchone()[0] or 0
    
    net_revenue = total_cash
    
    cursor.execute('''
        SELECT SUM(amount) FROM expenses 
        WHERE clinic_id = ?
        AND expense_date >= ? AND expense_date <= ?
    ''', (clinic_id, start_date[:10], end_date[:10]))
    total_expenses = cursor.fetchone()[0] or 0
    
    net_profit = net_revenue - total_expenses
    
    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'total_cash': total_cash,
        'outstanding_loans': outstanding_loans,
        'total_discounts': total_discounts,
        'net_revenue': net_revenue,
        'total_expenses': total_expenses,
        'net_profit': net_profit,
        'period': period,
        'currency': currency
    })

@app.route('/api/finance/transactions', methods=['GET'])
@require_permission('finance.view_transactions')
def api_finance_transactions():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    period = request.args.get('period', 'today')
    try:
        offset = int(request.args.get('offset', 0))
    except ValueError:
        offset = 0

    start_date, end_date, period = get_period_dates(period)
    today_str = datetime.date.today().isoformat()

    conn = get_db()
    cursor = conn.cursor()
    grouped_transactions, has_more = build_grouped_transactions(
        cursor, clinic_id, start_date, end_date, today_str, offset=offset, limit=20
    )

    currency = get_clinic_currency(clinic_id)

    return jsonify({
        'transactions': grouped_transactions,
        'has_more': has_more,
        'next_offset': offset + 20,
        'currency': currency
    })

@app.route('/api/finance/expenses', methods=['GET'])
@require_permission('finance.view')
def api_finance_expenses():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    
    period = request.args.get('period', 'today')
    start_date, end_date, period = get_period_dates(period)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, expense_date, category, description, amount
        FROM expenses
        WHERE clinic_id = ?
        AND expense_date >= ? AND expense_date <= ?
        ORDER BY expense_date DESC
        LIMIT 50
    ''', (clinic_id, start_date[:10], end_date[:10]))
    expenses = cursor.fetchall()
    
    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'expenses': [{'id': r[0], 'date': r[1], 'category': r[2], 'description': r[3], 'amount': r[4]} for r in expenses],
        'currency': currency
    })

@app.route('/api/finance/expenses/add', methods=['POST'])
@require_permission('finance.add_expense')
def api_finance_add_expense():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    
    data = request.get_json()
    expense_date = data.get('expense_date')
    category = data.get('category', 'Other')
    description = data.get('description', '').strip()
    try:
        amount = int(float(data.get('amount', 0)) * 100)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid expense amount.'}), 400
    
    if not expense_date or amount <= 0:
        return jsonify({'success': False, 'error': 'Please fill in all required fields correctly.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO expenses (uuid, clinic_id, expense_date, category, description, amount, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_id, expense_date, category, description, amount, datetime.datetime.now().isoformat()))
    conn.commit()
    return jsonify({'success': True})

@app.route('/api/finance/expenses/delete/<int:expense_id>', methods=['DELETE'])
@require_permission('finance.delete_expense')
def api_finance_delete_expense(expense_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM expenses WHERE id = ? AND clinic_id = ?", (expense_id, clinic_id))
    conn.commit()
    return jsonify({'success': True})

# ------------------------------------------------------------------
# API ROUTES - STAFF
# ------------------------------------------------------------------
@app.route('/api/staff/list', methods=['GET'])
@require_permission('staff.view')
def api_staff_list():
    """Return a JSON list of active staff members for the witness dropdown."""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'No clinic'}), 403
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT staff.id, staff.full_name
        FROM staff
        JOIN staff_clinics ON staff.id = staff_clinics.staff_id
        WHERE staff_clinics.clinic_id = ? AND staff.is_active = 1
        ORDER BY staff.full_name
    ''', (clinic_id,))
    staff = cursor.fetchall()
    return jsonify({'staff': [{'id': s[0], 'name': s[1]} for s in staff]})

@app.route('/api/staff', methods=['GET'])
@require_permission('staff.view')
def api_staff():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT staff.id, staff.full_name, staff_clinics.role, staff.username, staff.is_active
        FROM staff
        JOIN staff_clinics ON staff.id = staff_clinics.staff_id
        WHERE staff_clinics.clinic_id = ?
        ORDER BY staff_clinics.role, staff.full_name
    ''', (clinic_id,))
    staff_list = cursor.fetchall()
    
    return jsonify({
        'staff': [{'id': r[0], 'name': r[1], 'role': r[2], 'username': r[3], 'active': r[4]} for r in staff_list]
    })

@app.route('/api/staff/add', methods=['POST'])
@require_permission('staff.add')
def api_staff_add():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400

    data = request.get_json()
    full_name = data.get('full_name', '').strip()
    role = data.get('role', '').strip()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    if not full_name or not role or not username:
        return jsonify({'success': False, 'error': 'Name, role, and username are required.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT id FROM staff WHERE username = ?", (username,))
        existing = cursor.fetchone()
        
        if existing:
            new_staff_id = existing[0]
            cursor.execute('''
                SELECT 1 FROM staff_clinics WHERE staff_id = ? AND clinic_id = ?
            ''', (new_staff_id, clinic_id))
            if cursor.fetchone():
                return jsonify({'success': False, 'error': 'This staff member is already assigned to your clinic.'}), 400
            
            cursor.execute('''
                INSERT INTO staff_clinics (staff_id, clinic_id, role)
                VALUES (?, ?, ?)
            ''', (new_staff_id, clinic_id, role))
            
            conn.commit()
            log_audit('ADD_STAFF', 'staff', new_staff_id,
                      old_value=None, new_value=f"Linked existing staff to clinic. Role: {role}, Username: {username}")
            return jsonify({'success': True})
        
        if not password:
            return jsonify({'success': False, 'error': 'Password is required for a new staff account.'}), 400
        
        hashed_pw = generate_password_hash(password)
        
        cursor.execute('''
            INSERT INTO staff (uuid, full_name, role, username, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), full_name, role, username, hashed_pw, datetime.datetime.now().isoformat()))
        new_staff_id = cursor.lastrowid
        
        cursor.execute('''
            INSERT INTO staff_clinics (staff_id, clinic_id, role)
            VALUES (?, ?, ?)
        ''', (new_staff_id, clinic_id, role))
        
        conn.commit()
        log_audit('ADD_STAFF', 'staff', new_staff_id, 
                  old_value=None, new_value=f"Role: {role}, Username: {username}")
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'A staff member with this username already exists.'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/staff/edit', methods=['POST'])
@require_permission('staff.edit')
def api_staff_edit():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400

    data = request.get_json()
    staff_id = data.get('staff_id')
    full_name = data.get('full_name', '').strip()
    role = data.get('role', '').strip()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    if not staff_id or not full_name or not role or not username:
        return jsonify({'success': False, 'error': 'Name, role, and username are required.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 1 FROM staff_clinics WHERE staff_id = ? AND clinic_id = ?
    ''', (staff_id, clinic_id))
    if not cursor.fetchone():
        return jsonify({'success': False, 'error': 'This staff member is not part of your clinic.'}), 400
    
    try:
        if password:
            hashed_pw = generate_password_hash(password)
            cursor.execute('''
                UPDATE staff SET full_name = ?, username = ?, password_hash = ?, updated_at = ?
                WHERE id = ?
            ''', (full_name, username, hashed_pw, datetime.datetime.now().isoformat(), staff_id))
        else:
            cursor.execute('''
                UPDATE staff SET full_name = ?, username = ?, updated_at = ?
                WHERE id = ?
            ''', (full_name, username, datetime.datetime.now().isoformat(), staff_id))
        
        cursor.execute('''
            UPDATE staff_clinics SET role = ? WHERE staff_id = ? AND clinic_id = ?
        ''', (role, staff_id, clinic_id))
        
        conn.commit()
        log_audit('EDIT_STAFF', 'staff', staff_id, old_value="Updated details", new_value="Staff edited")
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'A staff member with this username already exists.'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/staff/deactivate/<int:staff_id>', methods=['POST'])
@require_permission('staff.deactivate')
def api_staff_deactivate(staff_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400

    if staff_id == session.get('staff_id'):
        return jsonify({'success': False, 'error': 'You cannot deactivate your own account.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 1 FROM staff_clinics WHERE staff_id = ? AND clinic_id = ?
    ''', (staff_id, clinic_id))
    if not cursor.fetchone():
        return jsonify({'success': False, 'error': 'This staff member is not part of your clinic.'}), 400
    
    cursor.execute('''
        UPDATE staff SET is_active = 0, updated_at = ?
        WHERE id = ?
    ''', (datetime.datetime.now().isoformat(), staff_id))
    conn.commit()
    log_audit('DEACTIVATE_STAFF', 'staff', staff_id, 
              old_value='Active', new_value='Deactivated')
    return jsonify({'success': True})

@app.route('/api/staff/reactivate/<int:staff_id>', methods=['POST'])
@require_permission('staff.reactivate')
def api_staff_reactivate(staff_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 1 FROM staff_clinics WHERE staff_id = ? AND clinic_id = ?
    ''', (staff_id, clinic_id))
    if not cursor.fetchone():
        return jsonify({'success': False, 'error': 'This staff member is not part of your clinic.'}), 400
    
    cursor.execute('''
        UPDATE staff SET is_active = 1, updated_at = ?
        WHERE id = ?
    ''', (datetime.datetime.now().isoformat(), staff_id))
    conn.commit()
    log_audit('REACTIVATE_STAFF', 'staff', staff_id, 
              old_value='Inactive', new_value='Reactivated')
    return jsonify({'success': True})

# ------------------------------------------------------------------
# API ROUTES - APPOINTMENTS
# ------------------------------------------------------------------
@app.route('/api/appointments', methods=['GET'])
@require_permission('appointment.view')
def api_appointments():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            appointments.id,
            appointments.appointment_date,
            appointments.appointment_type,
            appointments.status,
            appointments.reason,
            appointments.cancelled_reason,
            appointments.check_in_time,
            patients.id AS patient_id,
            patients.name AS patient_name,
            patients.phone,
            patients.sex
        FROM appointments
        JOIN patients ON appointments.patient_id = patients.id
        WHERE appointments.status NOT IN ('Waiting', 'Pending', 'In Progress', 'Completed', 'Returned to Doctor')
          AND appointments.clinic_id = ?
        ORDER BY appointments.appointment_date ASC
    ''', (clinic_id,))
    appointment_list = cursor.fetchall()

    return jsonify({
        'appointments': [{
            'id': r[0],
            'date': r[1],
            'type': r[2],
            'status': r[3],
            'reason': r[4],
            'cancelled_reason': r[5],
            'check_in_time': r[6],
            'patient_id': r[7],
            'patient_name': r[8],
            'phone': r[9],
            'sex': r[10]
        } for r in appointment_list]
    })

@app.route('/api/appointments/schedule', methods=['POST'])
@require_permission('appointment.schedule')
def api_appointment_schedule():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    
    data = request.get_json()
    patient_id = data.get('patient_id')
    appointment_date = data.get('appointment_date')
    reason = data.get('reason', 'Consultation')
    
    conn = get_db()
    cursor = conn.cursor()

    if not patient_id:
        name = data.get('new_name')
        dob = data.get('new_dob')
        sex = data.get('new_sex')
        phone = data.get('new_phone')
        location = data.get('new_location')
        
        if not name:
            return jsonify({'success': False, 'error': 'Name is required for new patient'}), 400
        
        cursor.execute('''
            INSERT INTO patients (uuid, clinic_id, name, date_of_birth, sex, phone, location, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), clinic_id, name, dob, sex, phone, location, datetime.datetime.now().isoformat()))
        patient_id = cursor.lastrowid
    
    cursor.execute('''
        INSERT INTO appointments (uuid, clinic_id, patient_id, doctor_id, appointment_date, appointment_type, reason, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_id, patient_id, session.get('staff_id'), appointment_date, 'Appointment', reason, 'Pending', datetime.datetime.now().isoformat()))
    
    conn.commit()
    return jsonify({'success': True})

@app.route('/api/appointments/update/<int:appt_id>', methods=['POST'])
@require_permission('appointment.confirm')
def api_appointment_update(appt_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    
    data = request.get_json()
    action = data.get('action')

    action_permission = {
        'confirm': 'appointment.confirm',
        'cancel': 'appointment.cancel',
        'reschedule': 'appointment.reschedule',
        'missed': 'appointment.mark_missed',
        'check_in': 'appointment.check_in',
    }
    permission = action_permission.get(action)
    if permission is None:
        return jsonify({'success': False, 'error': 'Invalid action'}), 400
    if not has_permission(session.get('role', ''), permission):
        return jsonify({'success': False, 'error': 'Permission denied.'}), 403

    now = datetime.datetime.now().isoformat()

    conn = get_db()
    cursor = conn.cursor()

    if action == 'confirm':
        cursor.execute('''
            UPDATE appointments SET status = 'Scheduled', updated_at = ? WHERE id = ?
        ''', (now, appt_id))

    elif action == 'cancel':
        reason = data.get('reason', 'No reason provided')
        cursor.execute('''
            UPDATE appointments SET status = 'Cancelled', cancelled_reason = ?, updated_at = ? WHERE id = ?
        ''', (reason, now, appt_id))

    elif action == 'reschedule':
        new_date = data.get('new_date')
        cursor.execute('''
            UPDATE appointments SET appointment_date = ?, status = 'Pending', updated_at = ? WHERE id = ?
        ''', (new_date, now, appt_id))

    elif action == 'missed':
        cursor.execute('''
            UPDATE appointments SET status = 'Missed', updated_at = ? WHERE id = ?
        ''', (now, appt_id))

    elif action == 'check_in':
        cursor.execute('''
            UPDATE appointments SET status = 'Waiting', check_in_time = ?, updated_at = ? WHERE id = ?
        ''', (now, now, appt_id))

    else:
        return jsonify({'success': False, 'error': 'Invalid action'}), 400

    conn.commit()
    return jsonify({'success': True})

@app.route('/api/appointments/review/<int:patient_id>', methods=['GET'])
@require_permission('appointment.review')
def api_appointment_review(patient_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT patients.id, patients.name, patients.sex, patients.date_of_birth, patients.phone,
               appointments.id, appointments.appointment_date, appointments.reason, appointments.status
        FROM patients
        JOIN appointments ON patients.id = appointments.patient_id
        WHERE patients.id = ? AND appointments.status = 'Pending'
          AND appointments.clinic_id = ?
        ORDER BY appointments.created_at DESC
        LIMIT 1
    ''', (patient_id, clinic_id))
    row = cursor.fetchone()

    if row is None:
        return jsonify({'error': 'This patient is not currently pending confirmation.'}), 404

    return jsonify({
        'patient_id': row[0],
        'patient_name': row[1],
        'patient_sex': row[2],
        'patient_dob': row[3],
        'patient_phone': row[4],
        'appointment_id': row[5],
        'appointment_date': row[6],
        'appointment_reason': row[7],
        'appointment_status': row[8]
    })

@app.route('/api/appointments/review/<int:patient_id>', methods=['POST'])
@require_permission('appointment.review')
def api_appointment_review_action(patient_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    action = data.get('action')
    now = datetime.datetime.now().isoformat()

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT appointments.id
        FROM patients
        JOIN appointments ON patients.id = appointments.patient_id
        WHERE patients.id = ? AND appointments.status = 'Pending'
          AND appointments.clinic_id = ?
        ORDER BY appointments.created_at DESC
        LIMIT 1
    ''', (patient_id, clinic_id))
    row = cursor.fetchone()

    if row is None:
        return jsonify({'success': False, 'error': 'This patient is not currently pending confirmation.'}), 400

    appt_id = row[0]

    if action == 'confirm':
        cursor.execute('''
            UPDATE appointments SET status = 'Scheduled', updated_at = ? WHERE id = ?
        ''', (now, appt_id))
    elif action == 'cancel':
        reason = data.get('reason', 'No reason provided')
        cursor.execute('''
            UPDATE appointments SET status = 'Cancelled', cancelled_reason = ?, updated_at = ? WHERE id = ?
        ''', (reason, now, appt_id))
    elif action == 'reschedule':
        new_date = data.get('new_date')
        if new_date:
            cursor.execute('''
                UPDATE appointments SET appointment_date = ?, status = 'Pending', updated_at = ? WHERE id = ?
            ''', (new_date, now, appt_id))
        else:
            return jsonify({'success': False, 'error': 'Please provide a new date for rescheduling.'}), 400
    else:
        return jsonify({'success': False, 'error': 'Invalid action.'}), 400

    conn.commit()
    return jsonify({'success': True})

# ------------------------------------------------------------------
# API ROUTES - VISITS
# ------------------------------------------------------------------
@app.route('/api/visit/create', methods=['POST'])
@require_permission('visit.create')
def api_visit_create():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    patient_id = data.get('patient_id')
    diagnosis = data.get('diagnosis', '').strip()
    selected_items = data.get('items', [])

    if not patient_id:
        return jsonify({'success': False, 'error': 'Patient ID required'}), 400
    if not diagnosis:
        return jsonify({'success': False, 'error': 'Diagnosis is required'}), 400
    if not selected_items:
        return jsonify({'success': False, 'error': 'Select at least one item'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT patients.id, patients.name, appointments.id, appointments.status
        FROM patients
        JOIN appointments ON patients.id = appointments.patient_id
        WHERE patients.id = ? AND appointments.status IN ('Waiting', 'Pending', 'Returned to Doctor') 
        ORDER BY appointments.created_at DESC
        LIMIT 1
    ''', (patient_id,))
    row = cursor.fetchone()

    if row is None:
        return jsonify({'success': False, 'error': 'Patient not in active queue'}), 400

    p_id, p_name, appointment_id, appointment_status = row

    cursor.execute(
        "SELECT id FROM visits WHERE appointment_id = ? AND status != 'Returned to Doctor'",
        (appointment_id,)
    )
    existing_visit = cursor.fetchone()
    if existing_visit:
        return jsonify({'success': False, 'error': f'A visit was already saved for {p_name}'}), 400

    today_str = datetime.date.today().isoformat()
    line_items = []
    total_fee = 0

    for item in selected_items:
        price_list_id = item.get('price_list_id')
        qty = item.get('qty', 0)

        if qty <= 0:
            return jsonify({'success': False, 'error': 'Quantity must be at least 1'}), 400

        cursor.execute('''
            SELECT price_list.id, price_list.item_name, price_list.item_type, price_list.price,
                   price_list.quantity AS pack_quantity, price_list.inventory_id,
                   COALESCE(stock.usable_qty, 0) AS usable_qty,
                   COALESCE(stock.expired_qty, 0) AS expired_qty
            FROM price_list
            LEFT JOIN inventory AS linked_item
                ON price_list.inventory_id = linked_item.id
            LEFT JOIN (
                SELECT LOWER(TRIM(item_name)) AS name_key, category,
                       SUM(CASE WHEN expiry_date >= ? THEN quantity ELSE 0 END) AS usable_qty,
                       SUM(CASE WHEN expiry_date <  ? THEN quantity ELSE 0 END) AS expired_qty
                FROM inventory
                WHERE is_active = 1 AND clinic_id = ?
                GROUP BY name_key, category
            ) AS stock
                ON stock.name_key = LOWER(TRIM(linked_item.item_name))
                AND stock.category = linked_item.category
            WHERE price_list.id = ? AND price_list.clinic_id = ? AND price_list.is_active = 1
        ''', (today_str, today_str, clinic_id, price_list_id, clinic_id))
        item_row = cursor.fetchone()

        if item_row is None:
            return jsonify({'success': False, 'error': 'Selected item no longer exists'}), 400

        (pl_id, pl_name, pl_type, pl_price, pl_pack_quantity, pl_inventory_id, usable_qty, expired_qty) = item_row

        has_stock_concept = pl_inventory_id is not None
        if has_stock_concept and usable_qty <= 0:
            return jsonify({'success': False, 'error': f'"{pl_name}" has no usable stock available'}), 400

        if has_stock_concept and qty > usable_qty:
            return jsonify({'success': False, 'error': f'"{pl_name}": only {usable_qty} unit(s) available, but {qty} were prescribed'}), 400

        pack_quantity = pl_pack_quantity if pl_pack_quantity and pl_pack_quantity > 0 else 1
        price_per_unit = pl_price / pack_quantity
        line_total = round(price_per_unit * qty)

        total_fee += line_total
        line_items.append((pl_id, pl_name, pl_type, round(price_per_unit), pl_inventory_id, qty, line_total))

    now = datetime.datetime.now().isoformat()

    cursor.execute('''
        INSERT INTO visits (uuid, clinic_id, patient_id, doctor_id, appointment_id,
                             visit_date, diagnosis, total_fee, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_id, patient_id, session.get('staff_id'), appointment_id, now, diagnosis, total_fee, 'Ready for Cashier', now))
    visit_id = cursor.lastrowid

    for (pl_id, pl_name, pl_type, price_per_unit, pl_inventory_id, qty, line_total) in line_items:
        cursor.execute('''
            INSERT INTO visit_items (uuid, visit_id, inventory_id, price_list_id, item_type, item_name,
                                      quantity, price_per_unit, total_line_price, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), visit_id, pl_inventory_id, pl_id, pl_type, pl_name, qty, price_per_unit, line_total, now))

    cursor.execute("UPDATE appointments SET status = 'In Progress', updated_at = ? WHERE id = ?", (now, appointment_id))

    conn.commit()
    log_audit('CREATE_VISIT', 'visits', visit_id, 
              old_value=None, new_value=f"Patient: {p_name}, Total: {format_amount(total_fee)}")
    return jsonify({'success': True, 'visit_id': visit_id})

@app.route('/api/visit/prefill/<int:patient_id>', methods=['GET'])
@require_permission('visit.create')
def api_visit_prefill(patient_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT patients.id, patients.name, patients.sex, patients.date_of_birth,
               appointments.id, appointments.appointment_type, appointments.status
        FROM patients
        JOIN appointments ON patients.id = appointments.patient_id
        WHERE patients.id = ? AND appointments.status IN ('Waiting', 'Pending', 'Returned to Doctor') 
        ORDER BY appointments.created_at DESC
        LIMIT 1
    ''', (patient_id,))
    row = cursor.fetchone()

    if row is None:
        return jsonify({'error': 'Patient not in active queue'}), 404

    (p_id, p_name, p_sex, p_dob, appointment_id, appointment_type, appointment_status) = row

    today_str = datetime.date.today().isoformat()
    cursor.execute('''
        SELECT price_list.id, price_list.item_type, price_list.item_name,
               price_list.price, price_list.quantity AS default_quantity,
               COALESCE(stock.usable_qty, 0) AS usable_qty,
               COALESCE(stock.expired_qty, 0) AS expired_qty,
               CASE WHEN price_list.inventory_id IS NULL THEN 1 ELSE 0 END AS no_stock_concept
        FROM price_list
        LEFT JOIN inventory AS linked_item
            ON price_list.inventory_id = linked_item.id
        LEFT JOIN (
            SELECT LOWER(TRIM(item_name)) AS name_key, category,
                   SUM(CASE WHEN expiry_date >= ? THEN quantity ELSE 0 END) AS usable_qty,
                   SUM(CASE WHEN expiry_date <  ? THEN quantity ELSE 0 END) AS expired_qty
            FROM inventory
            WHERE is_active = 1 AND clinic_id = ?
            GROUP BY name_key, category
        ) AS stock
            ON stock.name_key = LOWER(TRIM(linked_item.item_name))
            AND stock.category = linked_item.category
        WHERE price_list.is_active = 1
          AND price_list.clinic_id = ?
        ORDER BY price_list.item_type, price_list.item_name
    ''', (today_str, today_str, clinic_id, clinic_id))
    all_priced_items = cursor.fetchall()

    prefill_diagnosis = None
    prefill_return_reason = None
    prefill_items = []

    if appointment_status == 'Returned to Doctor':
        cursor.execute('''
            SELECT id, diagnosis, return_reason
            FROM visits
            WHERE appointment_id = ? AND status = 'Returned to Doctor'
            ORDER BY created_at DESC
            LIMIT 1
        ''', (appointment_id,))
        prev_visit = cursor.fetchone()

        if prev_visit:
            prev_visit_id, prefill_diagnosis, prefill_return_reason = prev_visit
            cursor.execute('''
                SELECT price_list_id, quantity
                FROM visit_items
                WHERE visit_id = ? AND price_list_id IS NOT NULL
            ''', (prev_visit_id,))
            prefill_items = [{'id': r[0], 'qty': r[1]} for r in cursor.fetchall()]

    currency = get_clinic_currency(clinic_id)

    return jsonify({
        'patient_id': p_id,
        'patient_name': p_name,
        'patient_sex': p_sex,
        'patient_dob': p_dob,
        'appointment_type': appointment_type,
        'appointment_status': appointment_status,
        'priced_items': [{
            'id': r[0],
            'type': r[1],
            'name': r[2],
            'price': r[3],
            'defaultQuantity': r[4],
            'usableQty': r[5],
            'expiredQty': r[6],
            'noStockConcept': r[7]
        } for r in all_priced_items],
        'prefill_diagnosis': prefill_diagnosis,
        'prefill_return_reason': prefill_return_reason,
        'prefill_items': prefill_items,
        'currency': currency
    })

# ------------------------------------------------------------------
# API ROUTES - AUDIT
# ------------------------------------------------------------------
@app.route('/api/audit', methods=['GET'])
@require_permission('audit.view')
def api_audit():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    
    conn = get_db()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT audit_log.id, staff.full_name, audit_log.action, audit_log.table_name, 
               audit_log.record_id, audit_log.old_value, audit_log.new_value, audit_log.timestamp
        FROM audit_log
        LEFT JOIN staff ON audit_log.staff_id = staff.id
        WHERE audit_log.clinic_id = ?
        ORDER BY audit_log.timestamp DESC
        LIMIT 50
    ''', (clinic_id,))
    
    logs = [dict(row) for row in cursor.fetchall()]
    
    return jsonify({'logs': logs})
    
    
@app.route('/service-worker.js')
def service_worker():
    # Served from root (not /static/) on purpose. A service worker's
    # maximum control area defaults to the folder it's served from --
    # registering it at /static/service-worker.js would only ever let
    # it control pages under /static/, never /queue, /register, etc.
    # The Service-Worker-Allowed header makes the wider scope explicit.
    response = send_from_directory('static', 'service-worker.js')
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    return response    

# ------------------------------------------------------------------
# SERVE SPA SHELL
# ------------------------------------------------------------------
# In app.py, replace the serve_spa route with this:
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_static_page(path):
    if path.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    if path == '':
        path = 'home'
    # Prevent directory traversal
    safe_path = path.replace('..', '').strip('/')
    try:
        return send_from_directory('static/pages', f'{safe_path}.html')
    except FileNotFoundError:
        return send_from_directory('static/pages', '404.html') if os.path.exists('static/pages/404.html') else jsonify({'error': 'Not found'}), 404


@app.after_request
def add_cache_headers(response):
    # Allow service worker to cache static assets and pages
    if request.path.startswith('/static/') or request.path in [
        '/register', '/queue', '/price_list', '/inventory', 
        '/dashboard', '/login', '/home', '/about', '/contact',
        '/cashier', '/loans', '/retail', '/appointments', 
        '/finance', '/staff'
    ]:
        response.headers['Cache-Control'] = 'public, max-age=3600'
    return response

# ------------------------------------------------------------------
# RUN THE APP
# ------------------------------------------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)