import os
import sqlite3
import secrets
import datetime
import uuid
from flask import g
from werkzeug.security import generate_password_hash

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

def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

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
        is_developer INTEGER NOT NULL DEFAULT 0,
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
        default_password = secrets.token_urlsafe(12)
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
        print(f"✅ Default admin created! Username: '{default_username}'  Password: '{default_password}'")
        print("⚠️  Log in and change this password immediately -- it will not be shown again.")
    
    conn.commit()
    conn.close()

if not os.path.exists(DATABASE):
    init_db()