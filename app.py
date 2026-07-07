import os
import sqlite3
import datetime
import uuid
import math
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session, flash, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from roles_permissions import has_permission

app = Flask(__name__)

# ------------------------------------------------------------------
# PERMISSION DECORATOR
# ------------------------------------------------------------------
def require_permission(permission, json_response=False):
    """Gate a route behind a permission string from roles_permissions_improved.py.

    json_response=False (default): behaves like the existing HTML routes —
        flashes a message and redirects to the dashboard on denial.
    json_response=True: behaves like the existing JSON API routes —
        returns {'success': False, 'error': ...} instead of redirecting,
        since those are called from fetch() and a redirect would just be
        swallowed by the JS.

    This is purely a drop-in replacement for the old
        allowed_roles = [...]
        if user_role not in allowed_roles: ...
    pattern — it doesn't change who is allowed to do what beyond what's
    defined in PERMISSIONS.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            user_role = session.get('role', '')
            if not has_permission(user_role, permission):
                if json_response:
                    return {'success': False, 'error': 'Permission denied.'}
                flash('You do not have permission to do that.', 'danger')
                return redirect(url_for('dashboard'))
            return view_func(*args, **kwargs)
        return wrapped
    return decorator

# THIS IS REQUIRED FOR LOGIN SESSIONS TO WORK
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-fallback-do-not-use-in-prod')

app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# ------------------------------------------------------------------
# HELPER FUNCTION
# ------------------------------------------------------------------
def get_current_clinic_id():
    cached = session.get('clinic_id')
    if cached:
        return cached
    staff_id = session.get('staff_id')
    if not staff_id:
        return None
    
    # Fallback: If session['clinic_id'] is missing, pick their first clinic
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT clinic_id FROM staff_clinics WHERE staff_id = ? LIMIT 1
    ''', (staff_id,))
    row = cursor.fetchone()
    conn.close()
    clinic_id = row[0] if row else None
    if clinic_id:
        session['clinic_id'] = clinic_id
    return clinic_id
    
    
def get_price_list_data(clinic_id):
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
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
            WHERE is_active = 1
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
    ''', (today_str, today_str, clinic_id))
    rows = cursor.fetchall()
    conn.close()
    return rows    
  
  
def get_inventory_data(clinic_id):
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, category, item_name, quantity, min_alert_level, expiry_date
        FROM inventory
        WHERE clinic_id = ? AND is_active = 1
        ORDER BY expiry_date ASC
    """, (clinic_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

@app.route('/api/inventory')
@require_permission('inventory.view', json_response=True)
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
    
# ------------------------------------------------------------------
# AUDIT LOGGING HELPER
# ------------------------------------------------------------------
def log_audit(action, table_name, record_id, old_value=None, new_value=None):
    """Log an action to the audit_log table."""
    staff_id = session.get('staff_id')
    if not staff_id:
        return  # Skip logging if no staff logged in (shouldn't happen)
    
    clinic_id = get_current_clinic_id()
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO audit_log (staff_id, clinic_id, action, table_name, record_id, old_value, new_value, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (staff_id, clinic_id, action, table_name, record_id, old_value, new_value, datetime.datetime.now().isoformat()))
    conn.commit()
    conn.close()    

# ------------------------------------------------------------------
# DATABASE INITIALIZATION (Your existing amazing schema)
# ------------------------------------------------------------------
# ------------------------------------------------------------------
# DATABASE INITIALIZATION (With NOT NULL constraints)
# ------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect('clinic.db')
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
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            is_synced INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_clinics_uuid ON clinics(uuid);')
    
    # 2. staff (Removed clinic_id, UNIQUE is now just username)
    # NOTE: 'role' here is the staff member's home/default role (used before a
    # clinic is selected, and as the initial value when they're first added).
    # The AUTHORITATIVE role for permission checks inside a clinic is
    # staff_clinics.role, since the same person can hold different roles
    # at different clinics.
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS staff (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT UNIQUE,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL,
        username TEXT UNIQUE NOT NULL,  -- Back to global unique (or keep per-clinic if you prefer)
        password_hash TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT,
        is_synced INTEGER DEFAULT 0
    )
''')

    # 2.5 staff_clinics (The new junction table, now with a per-clinic role)

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
    
    # 3. patients (UPDATED: name NOT NULL)
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
    
    # 4. appointments (UPDATED: appointment_date NOT NULL)
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
    
    # 5. visits (UPDATED: diagnosis NOT NULL)
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
            
            -- NEW COLUMNS START HERE --
            payment_channel TEXT DEFAULT 'Cash',
            payment_reference TEXT,
            medical_aid_company TEXT,
            is_retail INTEGER DEFAULT 0,
            -- Set when a cashier sends this visit back before payment
            -- (either to the doctor for a consultation, or back into
            -- the live retail cart for a retail sale). Kept on the OLD
            -- visit row for history/display -- the new visit created
            -- when the person redoes it does not carry this over.
            return_reason TEXT,
            -- NEW COLUMNS END HERE --

            FOREIGN KEY (clinic_id) REFERENCES clinics (id),
            FOREIGN KEY (patient_id) REFERENCES patients (id),
            FOREIGN KEY (doctor_id) REFERENCES staff (id),
            FOREIGN KEY (appointment_id) REFERENCES appointments (id)
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_visits_uuid ON visits(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_visits_clinic ON visits(clinic_id);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_visits_appointment ON visits(appointment_id);')
    
    # 6. visit_items (UPDATED: Added FOREIGN KEY for price_list_id)
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
    
    # 8. inventory (UPDATED: item_name NOT NULL)
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
    
    # --------------------------------------------------------------
    # AUTO-CREATE DEFAULT ADMIN IF NO STAFF EXISTS
    # --------------------------------------------------------------
    cursor.execute("SELECT COUNT(*) FROM staff")
    staff_count = cursor.fetchone()[0]

    if staff_count == 0:
        # Create a default admin account so the user never gets locked out
        default_username = "admin"
        default_password = "admin123"  # <--- You can change this to anything you want
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

# Create the database only if it doesn't exist
import os
if not os.path.exists('clinic.db'):
    init_db()

# ------------------------------------------------------------------
# LOGIN & SECURITY ROUTES
# ------------------------------------------------------------------

# This block forces every page to ask for login first
@app.before_request
def require_login():
    if request.path == '/' or request.path == '/login' or request.path.startswith('/static/') or request.path == '/about' or request.path == '/contact':
        return
    if 'staff_id' not in session:
        return redirect(url_for('login'))

@app.route('/')
def home():
    """Display the public Welcome / Landing page."""
    # If the user is already logged in, send them straight to the dashboard.
    if 'staff_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = sqlite3.connect('clinic.db')
        cursor = conn.cursor()
        
        # 1. Get the staff member (no clinic_id check)
        cursor.execute("SELECT id, role, password_hash FROM staff WHERE username = ? AND is_active = 1", (username,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user[2], password):
            session['staff_id'] = user[0]
            session['role'] = user[1]  # home/default role, may be overridden below
            
            # 2. Fetch all clinics this staff belongs to, WITH their role at each one
            conn = sqlite3.connect('clinic.db')
            cursor = conn.cursor()
            cursor.execute('''
                SELECT clinics.id, clinics.clinic_name, staff_clinics.role
                FROM clinics
                JOIN staff_clinics ON clinics.id = staff_clinics.clinic_id
                WHERE staff_clinics.staff_id = ?
            ''', (session['staff_id'],))
            clinics = cursor.fetchall()
            conn.close()
            
            session['user_clinics'] = clinics  # Store list of (id, name, role)
            
            # If they have 0 clinics, redirect to select_clinic with a message
            if len(clinics) == 0:
                flash('You are not assigned to any clinic. Please set up a new clinic or contact an admin.', 'warning')
                return redirect(url_for('select_clinic'))
            
            # If they have exactly 1 clinic, auto-select it (role comes from staff_clinics)
            if len(clinics) == 1:
                session['clinic_id'] = clinics[0][0]
                session['clinic_name'] = clinics[0][1]
                session['role'] = clinics[0][2]
                return redirect(url_for('dashboard'))
            
            # If they have multiple, redirect them to choose
            return redirect(url_for('select_clinic'))
            
        return render_template('login.html', error='Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/setup_clinic', methods=['GET', 'POST'])
def setup_clinic():
    """First-time setup: User must create their clinic before using the app."""
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT 1 FROM staff_clinics WHERE staff_id = ? LIMIT 1", (session['staff_id'],))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        flash('You already have a clinic assigned. Welcome back!', 'info')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        clinic_name = request.form['clinic_name'].strip()
        phone = request.form['phone'].strip()
        email = request.form['email'].strip()
        address = request.form['address'].strip()
        
        if not clinic_name:
            flash('Clinic Name is required.', 'danger')
            conn.close()
            return render_template('setup_clinic.html')
        
        cursor.execute('''
            INSERT INTO clinics (uuid, clinic_name, phone, email, address, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), clinic_name, phone, email, address, datetime.datetime.now().isoformat()))
        
        clinic_id = cursor.lastrowid
        
        # Whoever creates a clinic is always its Admin, regardless of their home role
        cursor.execute('''
            INSERT INTO staff_clinics (staff_id, clinic_id, role)
            VALUES (?, ?, ?)
        ''', (session['staff_id'], clinic_id, 'Admin'))
        
        conn.commit()
        conn.close()
        # Update the session for the newly created (and now active) clinic
        session['clinic_id'] = clinic_id
        session['clinic_name'] = clinic_name
        session['role'] = 'Admin'
        session['user_clinics'] = session.get('user_clinics', []) + [(clinic_id, clinic_name, 'Admin')]
        flash(f'Clinic "{clinic_name}" created successfully! You are now the Admin.', 'success')
        return redirect(url_for('dashboard'))
    
    conn.close()
    return render_template('setup_clinic.html')
    
@app.route('/create_clinic', methods=['POST'])
@require_permission('clinic.create')
def create_clinic():
    """Admin-only: Create a new clinic and assign the current admin to it."""
    clinic_name = request.form.get('clinic_name', '').strip()
    phone = request.form.get('phone', '').strip()
    
    if not clinic_name:
        flash('Clinic name is required.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO clinics (uuid, clinic_name, phone, created_at)
        VALUES (?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_name, phone, datetime.datetime.now().isoformat()))
    
    new_clinic_id = cursor.lastrowid
    
    # Assign the current admin as Admin of this new clinic too
    cursor.execute('''
        INSERT INTO staff_clinics (staff_id, clinic_id, role)
        VALUES (?, ?, ?)
    ''', (session['staff_id'], new_clinic_id, 'Admin'))
    
    conn.commit()
    conn.close()
    
    # Update session with the new clinic in the list
    current_clinics = session.get('user_clinics', [])
    session['user_clinics'] = current_clinics + [(new_clinic_id, clinic_name, 'Admin')]
    
    flash(f'Clinic "{clinic_name}" created successfully! Use the clinic switcher to switch to it.', 'success')
    return redirect(url_for('dashboard'))    
    
@app.route('/select_clinic', methods=['GET', 'POST'])
def select_clinic():
    """Allow a staff member to choose which clinic to operate in for this session."""
    if 'staff_id' not in session:
        return redirect(url_for('login'))
    
    # GET request: quick switch via ?clinic_id=123 from the navbar
    if request.method == 'GET':
        clinic_id = request.args.get('clinic_id')
        if clinic_id:
            # Verify they are actually allowed to work in this clinic
            clinics = session.get('user_clinics', [])
            match = next((c for c in clinics if c[0] == int(clinic_id)), None)
            if match:
                session['clinic_id'] = match[0]
                session['clinic_name'] = match[1]
                session['role'] = match[2]
                flash(f'Switched to clinic.', 'info')
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid clinic selection.', 'danger')
    
    clinics = session.get('user_clinics', [])
    
    # If they have 0 clinics, show a special message
    if not clinics:
        flash('You are not assigned to any clinic. Please set up a new clinic below.', 'warning')
        return render_template('select_clinic.html', clinics=clinics, no_clinics=True)
    
    if request.method == 'POST':
        clinic_id = request.form.get('clinic_id')
        # Verify they are actually allowed to work in this clinic
        match = next((c for c in clinics if c[0] == int(clinic_id)), None)
        if match:
            session['clinic_id'] = match[0]
            session['clinic_name'] = match[1]
            session['role'] = match[2]
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid clinic selection.', 'danger')
    
    return render_template('select_clinic.html', clinics=clinics, no_clinics=False)

# ------------------------------------------------------------------
# PATIENT REGISTRATION (Walk-In OR Appointment)
# ------------------------------------------------------------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    if request.method == 'POST':
        name = request.form['name']
        date_of_birth = request.form['date_of_birth']
        sex = request.form['sex']
        phone = request.form['phone']
        location = request.form['location']
        appointment_type = request.form['appointment_type']  # 'Walk-In' or 'Appointment'
        
        conn = sqlite3.connect('clinic.db')
        cursor = conn.cursor()
        
        # 1. Insert into patients
        cursor.execute('''
            INSERT INTO patients (uuid, clinic_id, name, date_of_birth, sex, phone, location, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), clinic_id, name, date_of_birth, sex, phone, location, datetime.datetime.now().isoformat()))
        
        patient_id = cursor.lastrowid
        
        # 2. Determine the status based on the appointment type
        if appointment_type == 'Appointment':
            status = 'Pending'   # Needs doctor to confirm date
        else:
            status = 'Waiting'   # Ready to be seen immediately

        # 3. Add to Queue (Appointments table)
        cursor.execute('''
            INSERT INTO appointments (uuid, clinic_id, patient_id, doctor_id, appointment_date, appointment_type, reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(uuid.uuid4()), clinic_id, patient_id, session.get('staff_id'), 
            datetime.datetime.now().isoformat(), 
            appointment_type,
            'Consultation',
            status,
            datetime.datetime.now().isoformat()
        ))
        
        conn.commit()
        log_audit('REGISTER_PATIENT', 'patients', patient_id, 
          old_value=None, new_value=f"Name: {name}, Sex: {sex}")
        conn.close()
        
        return redirect(url_for('queue'))
        
    return render_template('register.html')

@app.route('/service-worker.js')
def service_worker():
    # Served from root (not /static/) on purpose. A service worker's
    # maximum control area defaults to the folder it's served from —
    # registering it at /static/service-worker.js would only ever let
    # it control pages under /static/, never /queue, /register, etc.
    # The Service-Worker-Allowed header makes the wider scope explicit.
    response = send_from_directory('static', 'service-worker.js')
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    return response
    
    
# ------------------------------------------------------------------
# OFFLINE-FIRST PATIENT REGISTRATION (JSON API, idempotent by client UUID)
# ------------------------------------------------------------------

@app.route('/api/queue/register', methods=['POST'])
@require_permission('queue.register_patient', json_response=True)
def api_queue_register():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'error': 'no_clinic'}, 400

    data = request.get_json(silent=True)
    if not data:
        return {'error': 'invalid_json'}, 400

    client_uuid = data.get('client_uuid')
    name = data.get('name')
    if not client_uuid or not name:
        return {'error': 'missing_fields'}, 400

    date_of_birth = data.get('date_of_birth')
    sex = data.get('sex')
    phone = data.get('phone')
    location = data.get('location')
    appointment_type = data.get('appointment_type', 'Walk-In')

    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    # Idempotency check: has this exact registration already been processed?
    # client_uuid is generated once on the device and re-sent on every retry,
    # so a dropped connection + resend never creates a duplicate patient.
    cursor.execute('SELECT id FROM patients WHERE uuid = ?', (client_uuid,))
    existing = cursor.fetchone()
    if existing:
        patient_id = existing[0]
        cursor.execute('''
            SELECT status FROM appointments WHERE patient_id = ? ORDER BY id DESC LIMIT 1
        ''', (patient_id,))
        row = cursor.fetchone()
        status = row[0] if row else 'Waiting'
        conn.close()
        return {
            'status': 'already_processed',
            'patient_id': patient_id,
            'queue_status': status
        }, 200

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
    conn.close()

    return {
        'status': 'processed',
        'patient_id': patient_id,
        'queue_status': queue_status
    }, 201

# ------------------------------------------------------------------
# ACTIVE QUEUE — shared query used by both the HTML page and the
# offline read-cache JSON API, so the two can never drift apart.
# ------------------------------------------------------------------
def get_queue_data(clinic_id):
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    cursor.execute('''
        SELECT patients.id, patients.name, patients.sex, patients.phone,
               appointments.appointment_type, appointments.status
        FROM patients
        JOIN appointments ON patients.id = appointments.patient_id
        WHERE appointments.status IN ('Waiting', 'Pending', 'Returned to Doctor')
          AND appointments.clinic_id = ?
        ORDER BY
            -- Bumped-back patients go to the very front of the queue --
            -- they were already seen once and are just missing a fix,
            -- not a fresh arrival who should wait behind everyone else.
            CASE WHEN appointments.status = 'Returned to Doctor' THEN 0 ELSE 1 END,
            appointments.created_at ASC
    ''', (clinic_id,))
    queue_list = cursor.fetchall()
    conn.close()
    return queue_list

@app.route('/queue')
@require_permission('queue.view')
def queue():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    return render_template('queue.html')

# Read-only JSON snapshot of the queue, for the offline cache in
# queue.html. Same query as the HTML route above via get_queue_data(),
# so the two never disagree on what "the queue" is.
@app.route('/api/queue')
@require_permission('queue.view', json_response=True)
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
    
# ------------------------------------------------------------------
# APPOINTMENT MANAGEMENT
# ------------------------------------------------------------------
@app.route('/appointments')
def appointments():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    if not has_permission(session.get('role', ''), 'appointment.view'):
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))

    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    # Get appointments that are NOT in the active queue
    # Statuses: Pending (waiting for doctor), Scheduled (confirmed), Cancelled, Missed
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
    conn.close()

    return render_template('appointments.html', appointments=appointment_list, role=session.get('role'))


@app.route('/appointments/schedule', methods=['GET', 'POST'])
def schedule_appointment():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Schedule a new appointment for an existing or new patient"""
    if not has_permission(session.get('role', ''), 'appointment.schedule'):
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    if request.method == 'POST':
        # Get existing patient ID or register a new one on the fly
        patient_id = request.form.get('patient_id')
        if not patient_id or patient_id == '':
            # Register new patient right here
            name = request.form['new_name']
            dob = request.form['new_dob']
            sex = request.form['new_sex']
            phone = request.form['new_phone']
            location = request.form['new_location']
            
            cursor.execute('''
                INSERT INTO patients (uuid, clinic_id, name, date_of_birth, sex, phone, location, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (str(uuid.uuid4()), clinic_id, name, dob, sex, phone, location, datetime.datetime.now().isoformat()))
            patient_id = cursor.lastrowid
        
        appointment_date = request.form['appointment_date']
        reason = request.form.get('reason', 'Consultation')

        # STATUS IS "Pending" - waiting for doctor confirmation
        cursor.execute('''
            INSERT INTO appointments (uuid, clinic_id, patient_id, doctor_id, appointment_date, appointment_type, reason, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), clinic_id, patient_id, session.get('staff_id'), appointment_date, 'Appointment', reason, 'Pending', datetime.datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
        flash('Appointment scheduled successfully! Status: Pending (waiting for doctor confirmation).', 'success')
        return redirect(url_for('appointments'))
    
    # GET: Load existing patients for the dropdown
    cursor.execute("SELECT id, name, phone FROM patients WHERE clinic_id = ? AND is_active = 1 ORDER BY name", (clinic_id,))
    patients = cursor.fetchall()
    conn.close()

    return render_template('schedule_appointment.html', patients=patients)


@app.route('/appointments/update/<int:appt_id>', methods=['POST'])
def update_appointment(appt_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Update appointment status (Confirm, Cancel, Reschedule, Missed, Check In)"""
    data = request.get_json()
    action = data.get('action')

    # Each action maps to its own permission key in roles_permissions.py,
    # since 'confirm' and the rest historically weren't gated identically
    # in the module (they are now reconciled to match live behavior).
    action_permission = {
        'confirm': 'appointment.confirm',
        'cancel': 'appointment.cancel',
        'reschedule': 'appointment.reschedule',
        'missed': 'appointment.mark_missed',
        'check_in': 'appointment.check_in',
    }
    permission = action_permission.get(action)
    if permission is None:
        return {'success': False, 'error': 'Invalid action'}
    if not has_permission(session.get('role', ''), permission):
        return {'success': False, 'error': 'Permission denied.'}

    now = datetime.datetime.now().isoformat()

    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    if action == 'confirm':
        # Doctor confirms the appointment -> Scheduled
        cursor.execute('''
            UPDATE appointments SET status = 'Scheduled', updated_at = ? WHERE id = ?
        ''', (now, appt_id))
        message = "Appointment confirmed and scheduled."

    elif action == 'cancel':
        reason = data.get('reason', 'No reason provided')
        cursor.execute('''
            UPDATE appointments SET status = 'Cancelled', cancelled_reason = ?, updated_at = ? WHERE id = ?
        ''', (reason, now, appt_id))
        message = "Appointment cancelled."

    elif action == 'reschedule':
        new_date = data.get('new_date')
        cursor.execute('''
            UPDATE appointments SET appointment_date = ?, status = 'Pending', updated_at = ? WHERE id = ?
        ''', (new_date, now, appt_id))
        message = "Appointment rescheduled. Needs doctor confirmation again."

    elif action == 'missed':
        cursor.execute('''
            UPDATE appointments SET status = 'Missed', updated_at = ? WHERE id = ?
        ''', (now, appt_id))
        message = "Appointment marked as Missed."

    elif action == 'check_in':
        # Check in the patient -> Move to Active Queue (Waiting)
        cursor.execute('''
            UPDATE appointments SET status = 'Waiting', check_in_time = ?, updated_at = ? WHERE id = ?
        ''', (now, now, appt_id))
        message = "Patient checked in! Added to Active Queue."

    else:
        conn.close()
        return {'success': False, 'error': 'Invalid action'}

    conn.commit()
    conn.close()
    return {'success': True, 'message': message} 
    
    
@app.route('/appointments/review/<int:patient_id>', methods=['GET', 'POST'])
def review_appointment(patient_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Doctor reviews a pending appointment before confirming it"""
    if not has_permission(session.get('role', ''), 'appointment.review'):
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))

    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    # Fetch the pending patient details
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
        conn.close()
        flash('This patient is not currently pending confirmation.', 'warning')
        return redirect(url_for('queue'))

    (p_id, p_name, p_sex, p_dob, p_phone, appt_id, appt_date, appt_reason, appt_status) = row

    if request.method == 'POST':
        action = request.form.get('action')
        now = datetime.datetime.now().isoformat()

        if action == 'confirm':
            # Confirm -> Status becomes Scheduled (removes from Active Queue)
            cursor.execute('''
                UPDATE appointments SET status = 'Scheduled', updated_at = ? WHERE id = ?
            ''', (now, appt_id))
            conn.commit()
            conn.close()
            flash(f'Appointment confirmed for {p_name}. Patient moved to Appointments list.', 'success')
            return redirect(url_for('queue'))

        elif action == 'cancel':
            reason = request.form.get('reason', 'No reason provided')
            cursor.execute('''
                UPDATE appointments SET status = 'Cancelled', cancelled_reason = ?, updated_at = ? WHERE id = ?
            ''', (reason, now, appt_id))
            conn.commit()
            conn.close()
            flash(f'Appointment cancelled for {p_name}.', 'info')
            return redirect(url_for('queue'))

        elif action == 'reschedule':
            new_date = request.form.get('new_date')
            if new_date:
                cursor.execute('''
                    UPDATE appointments SET appointment_date = ?, status = 'Pending', updated_at = ? WHERE id = ?
                ''', (new_date, now, appt_id))
                conn.commit()
                conn.close()
                flash(f'Appointment rescheduled for {p_name}. Still pending confirmation.', 'info')
                return redirect(url_for('queue'))
            else:
                flash('Please provide a new date for rescheduling.', 'warning')

    conn.close()

    return render_template(
        'review_appointment.html',
        patient_id=p_id,
        patient_name=p_name,
        patient_sex=p_sex,
        patient_dob=p_dob,
        patient_phone=p_phone,
        appointment_date=appt_date,
        appointment_reason=appt_reason
    )    
    
# ------------------------------------------------------------------
# INVENTORY MANAGEMENT (Add & Edit)
# ------------------------------------------------------------------
@app.route('/inventory', methods=['GET', 'POST'])
@require_permission('inventory.view')
def inventory():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    if request.method == 'POST':
        # inventory.add and inventory.edit resolve to the same role set
        # today ({admin, pharmacist}), so one check covers both the
        # add-new-item and edit-existing-item branches below.
        if not has_permission(session.get('role', ''), 'inventory.add'):
            conn.close()
            flash('You do not have permission to modify inventory.', 'danger')
            return redirect(url_for('inventory'))

        inventory_id = request.form.get('inventory_id')
        category = request.form['category']
        item_name = request.form['item_name'].strip()
        quantity = int(request.form['quantity'])
        min_alert = int(request.form['min_alert_level'])
        expiry = request.form['expiry_date']

        # Guardrail: quantity here always means "amount to ADD" (Add mode)
        # or "amount to ADD to existing stock" (Edit mode) -- it should
        # never be negative. Removing/reducing stock has its own separate
        # action (Clear/Reduce Stock below), so a negative number here
        # would silently do the wrong thing rather than what's intended.
        if quantity < 0:
            flash('Quantity cannot be negative. Use "Clear/Reduce Stock" to remove units instead.', 'warning')
            conn.close()
            return redirect(url_for('inventory'))
        
        # SCENARIO A: EDIT — updates the picked row, and propagates the
        # rename/recategorize/alert-level change to every OTHER batch of
        # this same drug too (siblings = same original name+category,
        # different expiry). Quantity and expiry are per-batch and only
        # ever change on the row you actually opened. The only time this
        # merges into another row is if the new name+category+expiry on
        # the edited row now exactly matches some other existing row —
        # then those two specifically get merged together.
        if inventory_id and inventory_id != '':
            item_name = ' '.join(item_name.split())

            # Capture this row's ORIGINAL name + category before changing
            # anything, so we know which other rows are "siblings" (the
            # same drug, just a different batch/expiry).
            cursor.execute("SELECT item_name, category FROM inventory WHERE id = ? AND is_active = 1", (inventory_id,))
            original = cursor.fetchone()

            if original is None:
                conn.close()
                return redirect(url_for('inventory'))

            original_name, original_category = original

            # 1. Update the row being edited: name, category, alert level,
            #    and expiry all change here; quantity is ADDED to this
            #    row's existing stock (per-batch, as before).
            cursor.execute('''
                UPDATE inventory 
                SET category = ?, item_name = ?, quantity = quantity + ?, min_alert_level = ?, expiry_date = ?, updated_at = ?
                WHERE id = ? AND is_active = 1
            ''', (category, item_name, quantity, min_alert, expiry, datetime.datetime.now().isoformat(), inventory_id))

            # 2. Propagate name + category + alert level to every OTHER
            #    active batch of this same drug (matched on the ORIGINAL
            #    name+category, case/whitespace-insensitive). Their own
            #    quantity and expiry_date are left completely untouched.
            cursor.execute('''
                UPDATE inventory
                SET item_name = ?, category = ?, min_alert_level = ?, updated_at = ?
                WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?))
                  AND category = ?
                  AND clinic_id = ?
                  AND is_active = 1
                  AND id != ?
            ''', (item_name, category, min_alert, datetime.datetime.now().isoformat(),
                  original_name, original_category, clinic_id, inventory_id))

            # 3. Keep the Price List name in sync for the edited row AND
            #    every sibling batch, since they all share the drug's name.
            cursor.execute('''
                UPDATE price_list 
                SET item_name = ?, updated_at = ? 
                WHERE inventory_id IN (
                    SELECT id FROM inventory
                    WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND category = ? AND is_active = 1
                )
            ''', (item_name, datetime.datetime.now().isoformat(), item_name, category))

            # 4. Check if the edited row now matches another existing row
            #    on name + category + expiry (this happens if the new
            #    expiry you typed lines up with a batch that already
            #    exists). If so, merge those two: sum quantity into the
            #    older row and remove the duplicate.
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
                    # Re-point any price_list rows that referenced the duplicate
                    # being removed, so the price list doesn't lose its link.
                    cursor.execute(
                        "UPDATE price_list SET inventory_id = ?, updated_at = ? WHERE inventory_id = ?",
                        (first_id, datetime.datetime.now().isoformat(), dup_id)
                    )
                    cursor.execute("DELETE FROM inventory WHERE id = ?", (dup_id,))

            conn.commit()
            log_audit('EDIT_INVENTORY', 'inventory', inventory_id, 
          old_value=f"Original: {original_name}", 
          new_value=f"New: {item_name}, Qty +{quantity}")
            conn.close()
            return redirect(url_for('inventory'))

        # SCENARIO B: ADD — always creates a brand new row, never edits an
        # existing one. If a row with this exact name + category + expiry
        # already exists, that means this is actually the SAME batch as
        # something already in stock — block it and tell the user to use
        # Edit instead. Any other expiry (different, or no match at all)
        # is allowed and always inserts fresh.
        else:
            item_name = ' '.join(item_name.split())
            cursor.execute("""
                SELECT id FROM inventory 
                WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND category = ? AND expiry_date = ? AND clinic_id = ? AND is_active = 1
            """, (item_name, category, expiry, clinic_id))
            existing = cursor.fetchone()

            if existing:
                conn.close()
                flash(f'"{item_name}" with this exact expiry date already exists in stock. '
                      f'Use Edit Stock to add quantity to it, or choose a different expiry date.', 'warning')
                return redirect(url_for('inventory'))

            cursor.execute('''
                INSERT INTO inventory (uuid, clinic_id, category, item_name, quantity, min_alert_level, expiry_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (str(uuid.uuid4()), clinic_id, category, item_name, quantity, min_alert, expiry, datetime.datetime.now().isoformat()))

            conn.commit()
            log_audit('ADD_INVENTORY', 'inventory', cursor.lastrowid, 
          old_value=None, new_value=f"{item_name}, Qty: {quantity}")
            conn.close()
            return redirect(url_for('inventory'))

    # GET requests
    cursor.execute("SELECT id, category, item_name, quantity, min_alert_level, expiry_date FROM inventory WHERE clinic_id = ? AND is_active = 1 ORDER BY expiry_date ASC", (clinic_id,))
    items = cursor.fetchall()
    cursor.execute("SELECT item_name FROM inventory WHERE clinic_id = ? AND is_active = 1", (clinic_id,))
    existing_names = cursor.fetchall()
    conn.close()

    # Passed in as plain ISO date strings (YYYY-MM-DD) so the template can
    # compare them directly against expiry_date with simple string
    # comparison -- ISO dates sort correctly as strings, no date parsing
    # needed in Jinja. Same 14-day "expiring soon" window used on the
    # dashboard, kept consistent across both pages.
    today_str = datetime.date.today().isoformat()
    expiry_cutoff_str = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()

    return render_template(
        'inventory.html',
        items=items,
        existing_names=existing_names,
        today=today_str,
        expiry_cutoff=expiry_cutoff_str
    )


@app.route('/inventory/reduce/<int:item_id>', methods=['POST'])
@require_permission('inventory.reduce', json_response=True)
def reduce_inventory(item_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """
    Explicit, separate action for removing units from a specific batch --
    covers both clearing out expired stock entirely and smaller partial
    write-offs (damaged units, recount corrections, etc). This is
    deliberately NOT part of the Add/Edit form: that form's quantity
    field always means "add to stock", and overloading it to also mean
    "subtract" would be confusing and error-prone. This route only ever
    subtracts, and never lets the result go below 0 or remove more than
    what's actually there.
    """
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    data = request.get_json()
    amount_to_remove = data.get('amount')

    try:
        amount_to_remove = int(amount_to_remove)
    except (TypeError, ValueError):
        conn.close()
        return {'success': False, 'error': 'Invalid amount.'}

    if amount_to_remove <= 0:
        conn.close()
        return {'success': False, 'error': 'Amount must be greater than 0.'}

    cursor.execute("SELECT quantity, item_name FROM inventory WHERE id = ? AND clinic_id = ? AND is_active = 1", (item_id, clinic_id))
    row = cursor.fetchone()

    if row is None:
        conn.close()
        return {'success': False, 'error': 'Item not found.'}

    current_qty, item_name = row

    if amount_to_remove > current_qty:
        conn.close()
        return {'success': False, 'error': f'Cannot remove {amount_to_remove} -- only {current_qty} in stock.'}

    new_qty = current_qty - amount_to_remove

    cursor.execute(
        "UPDATE inventory SET quantity = ?, updated_at = ? WHERE id = ?",
        (new_qty, datetime.datetime.now().isoformat(), item_id)
    )
    conn.commit()

    log_audit('REDUCE_INVENTORY', 'inventory', item_id,
              old_value=f"{item_name}, Qty: {current_qty}",
              new_value=f"{item_name}, Qty: {new_qty} (-{amount_to_remove})")

    conn.close()

    return {'success': True, 'new_quantity': new_qty}




# ------------------------------------------------------------------
# PRICE LIST MANAGEMENT
# ------------------------------------------------------------------
@app.route('/price_list', methods=['GET', 'POST'])
@require_permission('price_list.view')
def price_list():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    if request.method == 'POST':
        # price_list.create and price_list.edit resolve to the same role
        # set today ({admin, pharmacist}); this route only ever creates new
        # price list entries (updates go through update_price_item()), so
        # price_list.create is the correct gate here.
        if not has_permission(session.get('role', ''), 'price_list.create'):
            conn.close()
            flash('You do not have permission to add price list items.', 'danger')
            return redirect(url_for('price_list'))

        item_type = request.form['item_type']
        item_name = request.form['item_name']
        price = int(float(request.form['price']) * 100)  # Convert to Tambala
        quantity = int(request.form['quantity'])

        if price < 0 or quantity < 0:
            flash('Price and quantity cannot be negative.', 'warning')
            conn.close()
            return redirect(url_for('price_list'))
        
        # Check if this item already exists in Inventory (to link it)
        cursor.execute("SELECT id FROM inventory WHERE item_name = ? AND category = ? AND is_active = 1", (item_name, item_type))
        inv_row = cursor.fetchone()
        inventory_id = inv_row[0] if inv_row else None
        
        cursor.execute('''
            INSERT INTO price_list (uuid, clinic_id, inventory_id, item_type, item_name, price, quantity, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), clinic_id, inventory_id, item_type, item_name, price, quantity, datetime.datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return redirect(url_for('price_list'))
    
    """
    # GET: Fetch price list and inventory items for autocomplete.
    #
    # Stock + expiry status is computed LIVE here, never stored. For each
    # priced item linked to inventory, we split its active batches into:
    #   - usable_qty   = quantity from batches that have NOT expired
    #   - expired_qty  = quantity from batches that HAVE expired
    #
    # That gives four possible states, and a drug can show as available
    # AND have expired stock flagged at the same time if it has both:
    #   - usable_qty > 0, expired_qty == 0  -> Available
    #   - usable_qty > 0, expired_qty  > 0  -> Available + some expired
    #   - usable_qty == 0, expired_qty > 0  -> Available but all expired
    #   - usable_qty == 0, expired_qty == 0 -> Out of Stock
    # Items with no inventory_id (Procedures, Consultation, etc. -- no
    # physical stock concept) are always "Available" with no expired flag.
    # Nothing here is written anywhere -- restocking or batches expiring
    # changes this automatically on the next page load.
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
            WHERE is_active = 1
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
    ''', (today_str, today_str, clinic_id))
    price_list = cursor.fetchall()
    
    cursor.execute("SELECT item_name, category FROM inventory WHERE clinic_id = ? AND is_active = 1", (clinic_id,))
    inventory_items = cursor.fetchall()
    """
    
    conn.close()
    
    return render_template('price_list.html')
    
# Read-only JSON snapshot of the price list + stock status, for the
# offline cache in price_list.html. Same query as the page's own data
# via get_price_list_data(), so the two never disagree.
@app.route('/api/price_list')
@require_permission('price_list.view', json_response=True)
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

    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute("SELECT item_name, category FROM inventory WHERE clinic_id = ? AND is_active = 1", (clinic_id,))
    inventory_items = [{'name': r[0], 'category': r[1]} for r in cursor.fetchall()]
    conn.close()

    return jsonify({
        'items': items,
        'inventory_items': inventory_items,
        'fetched_at': datetime.datetime.now().isoformat()
    })    

@app.route('/price_list/update/<int:item_id>', methods=['POST'])
@require_permission('price_list.edit', json_response=True)
def update_price_item(item_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    data = request.get_json()
    new_price = data.get('price')
    new_qty = data.get('quantity')

    try:
        if new_price is None or float(new_price) < 0:
            conn.close()
            return {'success': False, 'error': 'Price cannot be negative.'}
        if new_qty is None or int(new_qty) < 0:
            conn.close()
            return {'success': False, 'error': 'Quantity cannot be negative.'}
    except (TypeError, ValueError):
        conn.close()
        return {'success': False, 'error': 'Invalid price or quantity.'}

    try:
        # 1. Get the OLD data before updating
        cursor.execute("SELECT price, quantity, item_type, item_name FROM price_list WHERE id = ? AND clinic_id = ?", (item_id, clinic_id))
        row = cursor.fetchone()
        if row:
            old_price, old_qty, item_type, item_name = row
        else:
            conn.close()
            return {'success': False, 'error': 'Item not found.'}

        # 2. Update the price and quantity
        cursor.execute('''
            UPDATE price_list 
            SET price = ?, quantity = ?, updated_at = ? 
            WHERE id = ? AND clinic_id = ?
        ''', (new_price, new_qty, datetime.datetime.now().isoformat(), item_id, clinic_id))

        # 3. If anything actually changed, log it to price_history using your perfect table
        if old_price != new_price or old_qty != new_qty:
            cursor.execute('''
                INSERT INTO price_history (price_list_id, item_type, item_name, old_price, new_price, old_quantity, new_quantity, changed_by_staff_id, changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (item_id, item_type, item_name, old_price, new_price, old_qty, new_qty, session.get('staff_id'), datetime.datetime.now().isoformat()))

        conn.commit()
        log_audit('UPDATE_PRICE', 'price_list', item_id, 
          old_value=f"Price: {old_price}, Qty: {old_qty}", 
          new_value=f"Price: {new_price}, Qty: {new_qty}")
        conn.close()
        return {'success': True}
    except Exception as e:
        conn.rollback()
        conn.close()
        return {'success': False, 'error': str(e)}

@app.route('/price_list/delete/<int:item_id>')
@require_permission('price_list.delete')
def delete_price_item(item_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    # Soft delete: mark as inactive and update timestamp
    cursor.execute('''
        UPDATE price_list 
        SET is_active = 0, updated_at = ? 
        WHERE id = ? AND clinic_id = ?
    ''', (datetime.datetime.now().isoformat(), item_id, clinic_id))
    conn.commit()
    log_audit('DELETE_PRICE', 'price_list', item_id, 
          old_value='Active', new_value='Deactivated (Soft Delete)')
    conn.close()
    flash('Price item deactivated successfully. History preserved.', 'success')
    return redirect(url_for('price_list'))
    
@app.route('/price_list/history/<int:item_id>', methods=['GET'])
@require_permission('price_list.view_history', json_response=True)
def price_list_history(item_id):
    """Fetch the price history for a specific price_list item."""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'error': 'No clinic selected'}, 403

    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT item_type, item_name, old_price, new_price, old_quantity, new_quantity, changed_at, staff.full_name
        FROM price_history
        LEFT JOIN staff ON price_history.changed_by_staff_id = staff.id
        WHERE price_list_id = ?
        ORDER BY changed_at DESC
    ''', (item_id,))
    rows = cursor.fetchall()
    conn.close()
    
    return {
        'history': [{
            'type': r[0],
            'name': r[1],
            'old_price': r[2],
            'new_price': r[3],
            'old_qty': r[4],
            'new_qty': r[5],
            'changed_at': r[6],
            'changed_by': r[7] or 'System'
        } for r in rows]
    }  
    
    
@app.route('/api/staff/list')
def api_staff_list():
    """Return a JSON list of active staff members for the witness dropdown."""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'error': 'No clinic'}, 403
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT staff.id, staff.full_name
        FROM staff
        JOIN staff_clinics ON staff.id = staff_clinics.staff_id
        WHERE staff_clinics.clinic_id = ? AND staff.is_active = 1
        ORDER BY staff.full_name
    ''', (clinic_id,))
    staff = cursor.fetchall()
    conn.close()
    return {'staff': [{'id': s[0], 'name': s[1]} for s in staff]}    
    
    
@app.route('/visit/<int:patient_id>', methods=['GET', 'POST'])
@require_permission('visit.create')
def visit(patient_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    # Find the patient and their currently ACTIVE appointment (the one
    # that's actually sitting in the queue right now as Waiting or
    # Pending). A patient could in theory have older appointment rows
    # from past visits, but only the live one matters for opening a
    # new visit -- this mirrors exactly what the queue page itself
    # already filters on. 'Scheduled' is intentionally excluded: a
    # scheduled appointment hasn't been checked in yet and never
    # appears in the queue, so it has no business reaching the visit
    # form either. It belongs in /appointments until check-in moves
    # it to 'Waiting'.
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
        conn.close()
        flash('This patient is not in the active queue (status must be Waiting or Pending). Scheduled appointments stay in the Appointments tab until checked in.', 'warning')
        return redirect(url_for('queue'))

    (p_id, p_name, p_sex, p_dob, appointment_id, appointment_type, appointment_status) = row

    if request.method == 'POST':
        # Guard against double-submission (slow connection, accidental
        # double-tap on Save, browser back-and-resubmit, etc). Once a
        # visit is created for this appointment, the appointment moves
        # to 'In Progress' below -- so if a visit already exists for
        # this exact appointment_id, this POST is a duplicate of one
        # that already succeeded, not a legitimate second visit. Catch
        # it here, before doing any other work or touching inventory.
        #
        # 'Returned to Doctor' visits are excluded from this check on
        # purpose: that status means the cashier bounced a PRIOR visit
        # back before taking any payment, and the appointment itself
        # was reopened to 'Returned to Doctor' so the doctor could redo
        # it. The old visit row is kept only as a historical record --
        # it must never block the doctor from saving the real, current
        # visit for this same appointment_id.
        cursor.execute(
            "SELECT id FROM visits WHERE appointment_id = ? AND status != 'Returned to Doctor'",
            (appointment_id,)
        )
        existing_visit = cursor.fetchone()
        if existing_visit is not None:
            conn.close()
            flash(f'A visit was already saved for {p_name}. Sent to cashier.', 'info')
            return redirect(url_for('queue'))

        diagnosis = request.form.get('diagnosis', '').strip()

        if not diagnosis:
            flash('Diagnosis is required before saving a visit.', 'warning')
            conn.close()
            return redirect(url_for('visit', patient_id=patient_id))

        # Selected items arrive as parallel form fields:
        #   selected_items = list of price_list IDs that were checked
        #   qty_<id> = quantity for that specific item
        selected_ids = request.form.getlist('selected_items')

        if not selected_ids:
            flash('Select at least one item (consultation, drug, test, or procedure) before saving.', 'warning')
            conn.close()
            return redirect(url_for('visit', patient_id=patient_id))

        # Re-check stock status server-side for every selected item --
        # never trust what the client sent, since the page could be
        # stale (someone else cleared stock in the meantime) or the
        # request could be tampered with. Same logic as the price list
        # page: fully-expired-with-nothing-usable items are blocked
        # outright; partially-expired-but-still-stocked items are fine.
        today_str = datetime.date.today().isoformat()
        line_items = []
        total_fee = 0

        for price_list_id in selected_ids:
            qty_raw = request.form.get(f'qty_{price_list_id}', '1')
            try:
                qty = int(qty_raw)
            except ValueError:
                qty = 0

            if qty <= 0:
                flash('Quantity must be at least 1 for every selected item.', 'warning')
                conn.close()
                return redirect(url_for('visit', patient_id=patient_id))

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
                    WHERE is_active = 1
                    GROUP BY name_key, category
                ) AS stock
                    ON stock.name_key = LOWER(TRIM(linked_item.item_name))
                    AND stock.category = linked_item.category
                WHERE price_list.id = ? AND price_list.is_active = 1
            ''', (today_str, today_str, price_list_id))
            item_row = cursor.fetchone()

            if item_row is None:
                flash('One of the selected items no longer exists. Please review your selections.', 'warning')
                conn.close()
                return redirect(url_for('visit', patient_id=patient_id))

            (pl_id, pl_name, pl_type, pl_price, pl_pack_quantity, pl_inventory_id, usable_qty, expired_qty) = item_row

            # Block selection only when there's a stock concept AND
            # nothing usable remains (fully expired or genuinely out of
            # stock). Items with no inventory link at all (Procedures,
            # Consultation) have no stock concept and are always fine.
            has_stock_concept = pl_inventory_id is not None
            if has_stock_concept and usable_qty <= 0:
                flash(f'"{pl_name}" has no usable stock available (expired or out of stock) and cannot be added to this visit.', 'warning')
                conn.close()
                return redirect(url_for('visit', patient_id=patient_id))

            # Block when the prescribed quantity exceeds what's actually
            # usable. Being merely "in stock" (checked above) isn't
            # enough on its own -- 1 unit in stock doesn't justify
            # prescribing 60. This is a hard stop, not a clamp-down to
            # whatever's available: silently saving a smaller quantity
            # than what the doctor actually entered could understate a
            # prescription without anyone noticing, which is worse than
            # making them re-enter it.
            if has_stock_concept and qty > usable_qty:
                flash(f'"{pl_name}": only {usable_qty} unit(s) available, but {qty} were prescribed. Please adjust the quantity.', 'warning')
                conn.close()
                return redirect(url_for('visit', patient_id=patient_id))

            # price_list.price is the price for a whole PACK of
            # price_list.quantity units (e.g. MK 2500 for a pack of 30
            # capsules) -- it is NOT a per-unit price. If the doctor
            # prescribes a different quantity than the pack size, the
            # line total scales proportionally: (price / pack_quantity)
            # * quantity_prescribed. Guard against a pack_quantity of 0
            # or missing (shouldn't happen given price_list's own
            # min=1 constraint, but never trust stored data blindly).
            # Round to the nearest whole Tambala rather than truncating,
            # so fractional-cent rounding never silently loses money.
            pack_quantity = pl_pack_quantity if pl_pack_quantity and pl_pack_quantity > 0 else 1
            price_per_unit = pl_price / pack_quantity
            line_total = round(price_per_unit * qty)

            total_fee += line_total
            line_items.append((pl_id, pl_name, pl_type, price_per_unit, pl_inventory_id, qty, line_total))

        # All validation passed -- create the visit and its line items.
        visit_uuid = str(uuid.uuid4())
        now = datetime.datetime.now().isoformat()

        cursor.execute('''
            INSERT INTO visits (uuid, clinic_id, patient_id, doctor_id, appointment_id,
                                 visit_date, diagnosis, total_fee, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (visit_uuid, clinic_id, patient_id, session.get('staff_id'), appointment_id, now, diagnosis, total_fee, 'Ready for Cashier', now))
        visit_id = cursor.lastrowid

        for (pl_id, pl_name, pl_type, price_per_unit, pl_inventory_id, qty, line_total) in line_items:
            cursor.execute('''
                INSERT INTO visit_items (uuid, visit_id, inventory_id, price_list_id, item_type, item_name,
                                          quantity, price_per_unit, total_line_price, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (str(uuid.uuid4()), visit_id, pl_inventory_id, pl_id, pl_type, pl_name, qty, round(price_per_unit), line_total, now))

        # Move the patient out of the active queue -- their appointment
        # is now "In Progress" rather than Waiting/Pending, which is
        # what the queue page filters on.
        cursor.execute("UPDATE appointments SET status = 'In Progress', updated_at = ? WHERE id = ?", (now, appointment_id))

        conn.commit()
        log_audit('CREATE_VISIT', 'visits', visit_id, 
          old_value=None, new_value=f"Patient: {p_name}, Total: MK {total_fee/100}")
        conn.close()
        flash(f'Visit saved for {p_name}. Sent to cashier.', 'success')
        return redirect(url_for('queue'))

    # GET: show the form with all available price_list items, each
    # annotated with live stock status (same logic as the price list
    # page) so the template can grey out anything with no usable stock.
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
            WHERE is_active = 1
            GROUP BY name_key, category
        ) AS stock
            ON stock.name_key = LOWER(TRIM(linked_item.item_name))
            AND stock.category = linked_item.category
        WHERE price_list.is_active = 1
          AND price_list.clinic_id = ?
        ORDER BY price_list.item_type, price_list.item_name
    ''', (today_str, today_str, clinic_id))
    all_priced_items = cursor.fetchall()

    # If the cashier sent this visit back, pull the previous diagnosis,
    # reason, and item selections so the form opens pre-filled instead
    # of blank -- the doctor is fixing one thing, not redoing the whole
    # visit from memory. Only relevant when the appointment is actually
    # in the 'Returned to Doctor' state (checked above via
    # appointment_status); a completely fresh Waiting/Pending patient
    # has no such prior visit to look up.
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

    conn.close()

    return render_template(
        'visit.html',
        patient_id=p_id,
        patient_name=p_name,
        patient_sex=p_sex,
        patient_dob=p_dob,
        appointment_type=appointment_type,
        priced_items=all_priced_items,
        prefill_diagnosis=prefill_diagnosis,
        prefill_return_reason=prefill_return_reason,
        prefill_items=prefill_items
    )

# ------------------------------------------------------------------
# CASHIER PAGE
# ------------------------------------------------------------------
@app.route('/cashier')
def cashier():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Show brand-new consultation visits awaiting their first payment
    decision (status = 'Ready for Cashier' only). Active loans -- 
    consultation or retail -- live exclusively on /loans instead."""
    if not has_permission(session.get('role', ''), 'cashier.view'):
        flash('You do not have permission to access the Cashier page.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    # Fetch visits ready for cashier or with active loans -- consultation
    # visits only. Retail sales (is_retail = 1) are intentionally
    # excluded: they have their own "Pending Retail Sales" list on the
    # /retail page (see retail_pending() below) and their own
    # resume/cancel flow there, so a half-finished over-the-counter
    # sale never clutters the queue a doctor/cashier uses for patients
    # actually waiting to be charged after a consultation. Both flows
    # still finalize payment through the exact same /cashier/process
    # route below -- only the queue they're surfaced in differs.
    #
    # Plain JOIN is correct here (not LEFT JOIN): every visit left
    # after the is_retail filter is a real consultation visit, which
    # always has a patient attached.
    #
    # status = 'Ready for Cashier' ONLY -- 'Loan Active' visits are
    # deliberately excluded. Cashier is now purely "new visits that
    # need a first payment decision"; anyone who already has an active
    # loan (consultation OR retail) is handled exclusively through the
    # /loans page and loan_details.html's repayment form, which is the
    # correct, additive, witness-only-on-creation flow -- the cashier
    # modal's "Full Payment" button would otherwise charge the entire
    # original total instead of the real remaining balance.
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
    conn.close()
    
    return render_template('cashier.html', 
                          cashier_list=cashier_list,
                          role=session.get('role'))


# ------------------------------------------------------------------
# SEND VISIT BACK (before payment)
# ------------------------------------------------------------------
# One button, inside the Process Payment modal (so the cashier always
# sees the actual items before deciding), shared by both the Cashier
# page (consultation visits) and the Retail page (unpaid drafts) since
# they both use that same modal. What "send back" means differs by
# visit type:
#
#   - Consultation visit: goes back to 'Returned to Doctor' -- the
#     appointment reopens in the Queue (front of the line, distinct
#     badge) and the doctor's /visit/<patient_id> form pre-fills the
#     same diagnosis + items instead of starting blank, with a banner
#     showing the cashier's reason.
#   - Retail sale: reuses the exact same 'Cancelled' status the
#     existing /retail/cancel draft-cancel already uses (retail
#     already treats unpaid drafts as disposable/redoable) -- the only
#     difference is the item list is handed back in the response so
#     the Retail page can reload them straight into the live, editable
#     cart instead of the person having to re-search everything.
#
# In both cases no inventory is touched: creation/draft time never
# deducts stock (only /cashier/process does, on actual payment), so
# there is nothing to reverse -- this is purely a status change plus
# handing the item list back to the client.
@app.route('/cashier/send_back/<int:visit_id>', methods=['POST'])
@require_permission('cashier.send_back', json_response=True)
def send_back_visit(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    reason = (request.get_json(silent=True) or {}).get('reason', '').strip() or None

    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    cursor.execute('''
        SELECT visits.status, visits.appointment_id, visits.is_retail, patients.name
        FROM visits
        LEFT JOIN patients ON visits.patient_id = patients.id
        WHERE visits.id = ? AND visits.clinic_id = ?
    ''', (visit_id, clinic_id))
    row = cursor.fetchone()

    if row is None:
        conn.close()
        return jsonify({'success': False, 'error': 'Visit not found.'}), 404

    status, appointment_id, is_retail, patient_name = row

    if status != 'Ready for Cashier':
        conn.close()
        return jsonify({'success': False, 'error': f'This visit is "{status}" and can no longer be sent back (already paid or on a loan).'}), 400

    # Grab the item list BEFORE any status change, so it can be handed
    # back to the client to repopulate the cart/visit form with.
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
            conn.close()
            return jsonify({'success': False, 'error': 'Could not send this sale back -- it may have just been paid.'}), 400

        conn.commit()
        log_audit('SEND_BACK_RETAIL', 'visits', visit_id,
                  old_value='Ready for Cashier',
                  new_value=f"Sent back to Retail cart. Reason: {reason or '(none given)'}")
        conn.close()
        return jsonify({'success': True, 'returned_to': 'retail', 'items': items})

    # Consultation visit -> back to the doctor.
    if appointment_id is None:
        conn.close()
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
    conn.close()

    return jsonify({'success': True, 'returned_to': 'doctor', 'items': items})


@app.route('/cashier/view/<int:visit_id>', methods=['GET'])
def view_cashier_invoice(visit_id):
    """View-only route to show the items charged for a specific visit.
    Used by both the Cashier page (consultation visits) and the Retail
    page (resuming a pending retail draft) -- LEFT JOIN is required so
    retail visits, which have patient_id = NULL, don't 404 here."""
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    # Fetch Visit Details
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
        conn.close()
        return {'error': 'Visit not found'}, 404

    # Fetch the items for this visit
    cursor.execute('''
        SELECT item_type, item_name, quantity, price_per_unit, total_line_price
        FROM visit_items
        WHERE visit_id = ?
    ''', (visit_id,))
    items = cursor.fetchall()
    
    conn.close()
    
    return {
        'patient': visit[4] or '🏪 Retail Sale',
        'diagnosis': visit[2],
        'total': visit[0],
        'paid': visit[1],
        'status': visit[3],
        'items': [{'type': i[0], 'name': i[1], 'qty': i[2], 'unit_price': i[3], 'total': i[4]} for i in items]
    }                          

@app.route('/cashier/process/<int:visit_id>', methods=['POST'])
def process_payment(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Process payment for a visit (Full Payment, Discount, or Loan)"""
    # payment.process_full / process_loan / apply_discount all resolve to the
    # same role set today (admin, cashier, doctor), so one gate covers all
    # three payment modes handled below.
    if not has_permission(session.get('role', ''), 'payment.process_full'):
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    data = request.get_json()
    payment_mode = data.get('payment_mode')
    
    if payment_mode not in ['full', 'loan']:
        return {'success': False, 'error': 'Invalid payment mode.'}
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    try:
        # Get the visit details
        cursor.execute('''
            SELECT total_fee, amount_paid, status, patient_id, appointment_id, loan_witness
            FROM visits WHERE id = ? AND clinic_id = ?
        ''', (visit_id, clinic_id))
        visit_row = cursor.fetchone()
        
        if not visit_row:
            conn.close()
            return {'success': False, 'error': 'Visit not found.'}
        
        total_fee, current_paid, status, patient_id, appointment_id, existing_witness = visit_row
        
        if status not in ['Ready for Cashier', 'Loan Active']:
            conn.close()
            return {'success': False, 'error': 'Visit is not in a payable state.'}
        
        now = datetime.datetime.now().isoformat()

        # Atomically "claim" this visit
        cursor.execute('''
            UPDATE visits SET status = 'Processing', updated_at = ?
            WHERE id = ? AND status IN ('Ready for Cashier', 'Loan Active')
        ''', (now, visit_id))

        if cursor.rowcount == 0:
            conn.close()
            return {'success': False, 'error': 'This payment was already processed.'}

        # ROUNDING: the cashier may choose to round the invoice total to
        # the nearest MK 100-1000 to avoid decimals. If a rounded_total is
        # supplied, validate it against the cashier's chosen rounding step
        # and against the original total_fee (it can only round to the
        # nearest multiple -- never an arbitrary value) and then persist it
        # as the visit's real total_fee. This is a genuine adjustment to
        # what's billed, not a discount, so it happens before any discount
        # math and isn't recorded as one.
        rounded_total = data.get('rounded_total')
        round_to = data.get('round_to') or 0
        if rounded_total is not None and round_to:
            try:
                rounded_total = int(rounded_total)
                round_to_tambala = int(round_to) * 100
            except (TypeError, ValueError):
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Invalid rounding value.'}

            if round_to_tambala <= 0 or round_to_tambala % 10000 != 0:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Invalid rounding step.'}

            # Use floor(x + 0.5) for round-half-up, matching JavaScript's
            # Math.round() exactly. Python's built-in round() uses
            # banker's rounding (round-half-to-even), which disagrees
            # with the frontend at exact halfway points (e.g. 12.5) and
            # would cause this validation to wrongly reject a legitimate
            # rounding choice.
            expected_rounded = math.floor(total_fee / round_to_tambala + 0.5) * round_to_tambala
            if rounded_total != expected_rounded:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Rounded total does not match the selected rounding step.'}

            cursor.execute('''
                UPDATE visits SET total_fee = ?, updated_at = ?
                WHERE id = ?
            ''', (rounded_total, now, visit_id))
            total_fee = rounded_total
        
        # 1. SAVE PAYMENT CHANNEL INFO
        payment_channel = data.get('payment_channel', 'Cash')
        payment_reference = data.get('payment_reference')
        medical_aid_company = data.get('medical_aid_company')
        
        cursor.execute('''
            UPDATE visits
            SET payment_channel = ?, payment_reference = ?, medical_aid_company = ?
            WHERE id = ?
        ''', (payment_channel, payment_reference, medical_aid_company, visit_id))
        
        if payment_mode == 'full':
            # Full Payment, optionally with a discount applied on top.
            # current_paid may already be > 0 here -- this branch is also
            # reached when someone pays off the REMAINDER of an existing
            # loan in full (status was 'Loan Active'). amount_due is the
            # final total the visit should show as paid; collected_now is
            # what actually changed hands in this transaction, which is
            # the only figure that should ever hit loan_payments / a
            # day's cash total. Without this distinction, paying off an
            # old loan would silently re-count money collected on an
            # earlier day as if it arrived today.
            discount_amount = data.get('discount_amount') or 0
            discount_reason = (data.get('discount_reason') or '').strip()

            if discount_amount < 0:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Invalid discount amount.'}

            if discount_amount > total_fee:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Discount cannot exceed the total fee.'}

            if discount_amount > 0 and not discount_reason:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Discount reason is required.'}

            amount_due = total_fee - discount_amount

            if current_paid > amount_due:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Amount already paid exceeds the discounted total. Adjust the discount first.'}

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

            # If this visit had a prior loan payment (current_paid > 0),
            # record today's remainder as its own loan_payments entry --
            # same reasoning as add_loan_payment(): finance/dashboard cash
            # totals sum loan_payments by date, so the money collected
            # today for an old loan must be dated today, separately from
            # whatever was already recorded on the day(s) it was first paid.
            if current_paid > 0 and collected_now > 0:
                cursor.execute('''
                    INSERT INTO loan_payments (uuid, visit_id, payment_date, amount, created_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (str(uuid.uuid4()), visit_id, now, collected_now, now))
            
        elif payment_mode == 'loan':
            # Loan: partial payment, witness required. A discount may also
            # be applied, reducing the effective amount owed.
            #
            # amount_paid_now is exactly what the modal's "Amount Paid Now"
            # field means -- money changing hands in THIS transaction, not
            # a running total. current_paid (fetched earlier from the
            # visit row) may already be > 0 if this is an additional
            # payment on an existing loan, not the loan's first payment.
            # The new running total is current_paid + amount_paid_now, the
            # same arithmetic add_loan_payment() already uses correctly
            # for the dedicated "Record Payment" flow on loan_details.html
            # -- this route needed to match it instead of overwriting
            # amount_paid with just the new field value, which silently
            # erased whatever had already been paid earlier.
            amount_paid_now = data.get('amount_paid')
            witness_id = data.get('witness_id')
            loan_due_date = data.get('loan_due_date')
            discount_amount = data.get('discount_amount') or 0
            discount_reason = (data.get('discount_reason') or '').strip()

            if discount_amount < 0:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Invalid discount amount.'}

            if discount_amount > total_fee:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Discount cannot exceed the total fee.'}

            if discount_amount > 0 and not discount_reason:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Discount reason is required.'}

            effective_total = total_fee - discount_amount

            if amount_paid_now is None or amount_paid_now < 0:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Invalid payment amount.'}

            if current_paid > effective_total:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Amount already paid exceeds the discounted total. Adjust the discount first.'}

            new_total_paid = current_paid + amount_paid_now

            if new_total_paid >= effective_total:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'This payment would cover the full remaining balance. Use Full Payment instead.'}
            
            if not witness_id:
                conn.rollback()
                conn.close()
                return {'success': False, 'error': 'Witness is required for loans.'}
            
            # Validate due date format if provided
            if loan_due_date and loan_due_date.strip():
                try:
                    datetime.datetime.strptime(loan_due_date, '%Y-%m-%d')
                except ValueError:
                    conn.rollback()
                    conn.close()
                    return {'success': False, 'error': 'Invalid due date format. Use YYYY-MM-DD.'}
            else:
                loan_due_date = None

            # Fetch witness name to store in the text column for legacy support
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
            
            # Record this payment in loan_payments. amount_paid_now is
            # always the correct figure here regardless of whether this
            # is the loan's first payment or an additional one -- it's
            # exactly what was collected in this transaction.
            cursor.execute('''
                INSERT INTO loan_payments (uuid, visit_id, payment_date, amount, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (str(uuid.uuid4()), visit_id, now, amount_paid_now, now))
        
        # ------------------------------------------------------------------
        # INVENTORY DEDUCTION: FEFO (First-Expired, First-Out)
        # This logic remains completely identical to your original working code
        # ------------------------------------------------------------------
        cursor.execute('''
            SELECT visit_items.item_name, visit_items.quantity
            FROM visit_items
            WHERE visit_items.visit_id = ?
              AND visit_items.inventory_id IS NOT NULL
        ''', (visit_id,))
        items_to_deduct = cursor.fetchall()
        
        for item_name, qty_to_deduct in items_to_deduct:
            cursor.execute('''
                SELECT id, quantity, expiry_date
                FROM inventory
                WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND clinic_id = ? AND is_active = 1
                ORDER BY expiry_date ASC
            ''', (item_name, clinic_id))
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
                print(f"WARNING: Could not fully deduct {item_name}, still {remaining} units short")
        
        conn.commit()
        conn.close()
        return {'success': True}
        
    except Exception as e:
        conn.rollback()
        conn.close()
        return {'success': False, 'error': str(e)}


def get_loans_data(clinic_id):
    """Same query as before, now shared by both the HTML shell route
    and the JSON API route, so they can never disagree on what an
    'active loan' is."""
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
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
    conn.close()
    return loan_list


@app.route('/loans', methods=['GET'])
@require_permission('loan.view_list')
def loans():
    """Shell only now -- loans.html fetches /api/loans on load, same
    pattern as queue.html. All permission/clinic checks unchanged."""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    return render_template('loans.html')


@app.route('/api/loans', methods=['GET'])
@require_permission('loan.view_list', json_response=True)
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

    return jsonify({
        'loans': loans,
        'fetched_at': datetime.datetime.now().isoformat()
    })


@app.route('/cashier/loan/<int:visit_id>', methods=['GET'])
def loan_details(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """View loan payment history for a specific visit"""
    if not has_permission(session.get('role', ''), 'loan.view'):
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = sqlite3.connect('clinic.db')
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
        conn.close()
        return redirect(url_for('cashier'))
    
    cursor.execute('''
        SELECT payment_date, amount
        FROM loan_payments
        WHERE visit_id = ?
        ORDER BY payment_date ASC
    ''', (visit_id,))
    payments = cursor.fetchall()
    
    conn.close()
    
    return render_template('loan_details.html',
                          visit=visit,
                          payments=payments,
                          role=session.get('role'))

@app.route('/cashier/loan/pay/<int:visit_id>', methods=['POST'])
def add_loan_payment(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Add a new payment toward an outstanding loan"""
    if not has_permission(session.get('role', ''), 'loan.record_payment'):
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    data = request.get_json()
    amount = data.get('amount')
    
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return {'success': False, 'error': 'Invalid payment amount.'}
    
    if amount <= 0:
        return {'success': False, 'error': 'Amount must be greater than 0.'}
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT total_fee, amount_paid, status, discount_amount
            FROM visits WHERE id = ? AND clinic_id = ?
        ''', (visit_id, clinic_id))
        visit = cursor.fetchone()
        
        if not visit:
            conn.close()
            return {'success': False, 'error': 'Visit not found.'}
        
        total_fee, current_paid, status, discount_amount = visit
        effective_total = total_fee - (discount_amount or 0)
        
        if status != 'Loan Active':
            conn.close()
            return {'success': False, 'error': 'This visit is not an active loan.'}
        
        new_total_paid = current_paid + amount
        
        if new_total_paid > effective_total:
            conn.close()
            return {'success': False, 'error': 'Payment exceeds remaining loan balance.'}
        
        now = datetime.datetime.now().isoformat()
        
        # Record the payment
        cursor.execute('''
            INSERT INTO loan_payments (uuid, visit_id, payment_date, amount, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), visit_id, now, amount, now))
        
        # Update the visit's amount_paid
        cursor.execute('''
            UPDATE visits
            SET amount_paid = ?,
                updated_at = ?
            WHERE id = ?
        ''', (new_total_paid, now, visit_id))
        
        # If fully paid, mark as Paid
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
        conn.close()
        return {'success': True}
        
    except Exception as e:
        conn.rollback()
        conn.close()
        return {'success': False, 'error': str(e)}

# ------------------------------------------------------------------
# RETAIL / PHARMACY SALES (Non-clinical)
# ------------------------------------------------------------------
@app.route('/retail', methods=['GET'])
def retail():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    
    if not has_permission(session.get('role', ''), 'retail.view'):
        flash('You do not have permission to access Retail Sales.', 'danger')
        return redirect(url_for('dashboard'))

    # NOTE: there used to be a POST handler here that created an
    # already-Paid visit directly, with its own separate copy of the
    # pricing/discount/inventory-deduction logic. retail.html no longer
    # calls it -- the page now creates a draft visit via
    # /retail/create_draft and finalizes payment through
    # /cashier/process/<visit_id>, the exact same route the Cashier
    # page uses. That keeps a single source of truth for "what happens
    # when a visit gets paid" (status transitions, payment_channel,
    # discount/rounding validation, FEFO inventory deduction) instead
    # of two slightly-different implementations drifting apart, which
    # was the root cause of dashboard/finance/cashier numbers
    # disagreeing. Removed rather than left dead, so nothing can ever
    # get wired back to this shortcut by accident.

    # GET: Show the retail form
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    # Same usable-stock join used in /retail/create_draft, reused here
    # rather than reimplemented, so the number shown to the operator
    # before adding an item to the cart can never drift from the number
    # actually enforced when the sale is validated.
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
            FROM inventory WHERE is_active = 1 GROUP BY name_key, category
        ) AS stock ON stock.name_key = LOWER(TRIM(linked_item.item_name))
                 AND stock.category = linked_item.category
        WHERE price_list.clinic_id = ? AND price_list.is_active = 1
        ORDER BY price_list.item_name
    ''', (today_str, clinic_id))
    items = cursor.fetchall()
    conn.close()

    return render_template('retail.html', items=items, role=session.get('role'))

@app.route('/retail/create_draft', methods=['POST'])
@require_permission('retail.create_draft', json_response=True)
def retail_create_draft():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}
    
    data = request.get_json()
    cart = data.get('cart', [])
    
    if not cart:
        return {'success': False, 'error': 'Cart is empty.'}
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    try:
        total_fee = 0
        now = datetime.datetime.now().isoformat()
        today_str = datetime.date.today().isoformat()
        visit_items = []

        # Calculate and validate cart
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
                    FROM inventory WHERE is_active = 1 GROUP BY name_key, category
                ) AS stock ON stock.name_key = LOWER(TRIM(linked_item.item_name)) 
                         AND stock.category = linked_item.category
                WHERE price_list.id = ? AND price_list.clinic_id = ? AND price_list.is_active = 1
            ''', (today_str, item['price_list_id'], clinic_id))
            
            row = cursor.fetchone()
            if not row:
                conn.close()
                return {'success': False, 'error': f'Item not found: {item["name"]}'}
            
            pl_id, pl_name, pl_type, pl_price, pl_pack_qty, pl_inv_id, usable_qty = row
            qty_sold = item['qty']

            if pl_inv_id is not None and qty_sold > usable_qty:
                conn.close()
                return {'success': False, 'error': f'Only {usable_qty} units of "{pl_name}" available.'}
            
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
            
        # Create the draft visit (status = Ready for Cashier)
        cursor.execute('''
            INSERT INTO visits (uuid, clinic_id, patient_id, doctor_id, appointment_id, visit_date, 
                                diagnosis, total_fee, amount_paid, status, created_at, updated_at, is_retail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), clinic_id, None, None, None, now, 'Retail Sale', total_fee, 0, 'Ready for Cashier', now, now, 1))
        
        visit_id = cursor.lastrowid
        
        # Insert visit items
        for item in visit_items:
            cursor.execute('''
                INSERT INTO visit_items (uuid, visit_id, inventory_id, price_list_id, item_type, item_name,
                                         quantity, price_per_unit, total_line_price, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (str(uuid.uuid4()), visit_id, item['pl_inv_id'], item['pl_id'], item['pl_type'], item['pl_name'], 
                  item['qty_sold'], round(item['price_per_unit']), item['line_total'], now))
        
        conn.commit()
        conn.close()
        return {'success': True, 'visit_id': visit_id}
        
    except Exception as e:
        conn.rollback()
        conn.close()
        return {'success': False, 'error': str(e)}    


@app.route('/retail/pending', methods=['GET'])
@require_permission('retail.view', json_response=True)
def retail_pending():
    """List retail drafts awaiting their FIRST payment decision (created
    via create_draft but not yet finalized through /cashier/process).
    status = 'Ready for Cashier' ONLY -- once a retail sale takes its
    first partial payment and becomes 'Loan Active', it is no longer a
    "pending draft"; it's a loan, and graduates entirely to the /loans
    page (loan_details.html's repayment form), exactly like a
    consultation loan does. Including 'Loan Active' here would surface
    the same sale in two places with two different (and inconsistent)
    ways to pay it -- a "Resume" button reopening the original
    payment-creation modal here, vs the correct additive repayment
    form on /loans -- which is the bug this comment is preventing."""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}

    conn = sqlite3.connect('clinic.db')
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
    conn.close()

    return {
        'success': True,
        'pending': [
            {
                'visit_id': r[0],
                'total_fee': r[1],
                'amount_paid': r[2],
                'status': r[3],
                'created_at': r[4],
            }
            for r in rows
        ]
    }


@app.route('/retail/cancel/<int:visit_id>', methods=['POST'])
@require_permission('retail.cancel_draft', json_response=True)
def retail_cancel(visit_id):
    """Cancel an abandoned retail draft. Only allowed for retail visits
    that are still unpaid/undeducted -- create_draft never deducts
    inventory (that only happens inside process_payment), so cancelling
    here is a plain status change with nothing to restore. A visit
    that has already taken a loan payment (Loan Active with
    amount_paid > 0) is NOT cancellable from here -- real money has
    already changed hands for it, so it must be resumed and completed
    through the normal payment flow instead, same as a consultation
    loan would be."""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}

    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    cursor.execute('''
        SELECT status, amount_paid, is_retail
        FROM visits
        WHERE id = ? AND clinic_id = ?
    ''', (visit_id, clinic_id))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return {'success': False, 'error': 'Visit not found.'}

    status, amount_paid, is_retail = row

    if not is_retail:
        conn.close()
        return {'success': False, 'error': 'This is not a retail sale.'}

    if amount_paid and amount_paid > 0:
        conn.close()
        return {'success': False, 'error': 'This sale already has a payment recorded and cannot be cancelled. Resume and complete it instead.'}

    now = datetime.datetime.now().isoformat()
    cursor.execute('''
        UPDATE visits
        SET status = 'Cancelled', updated_at = ?
        WHERE id = ? AND status = 'Ready for Cashier'
    ''', (now, visit_id))

    if cursor.rowcount == 0:
        conn.close()
        return {'success': False, 'error': 'Only unpaid drafts can be cancelled.'}

    conn.commit()
    conn.close()
    return {'success': True}


# ------------------------------------------------------------------
# FINANCE & REPORTING DASHBOARD
# ------------------------------------------------------------------
def get_period_dates(period):
    """
    Translate a 'period' query param (today/week/month) into a
    (start_date, end_date, normalized_period) tuple of ISO datetime strings.
    Shared by finance() and finance_transactions() so the date-range logic
    only needs to be changed in one place.
    """
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
    """
    Build the list of per-visit grouped transactions (with installments)
    for the finance page, ordered newest-first by exact payment timestamp.
    Supports pagination via offset/limit for lazy-loading.
    Returns (grouped_transactions, has_more).
    """
    # 1) loan_payments grouped by visit_id and by DATE(payment_date)
    #    NOTE: we keep MAX(payment_date) as a full timestamp (pay_ts) purely
    #    for ordering -- pay_date stays a plain date for display/grouping.
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
    loan_grouped = cursor.fetchall()  # list of (visit_id, pay_date, amount_sum, pay_ts)

    # 2) direct visit payments (visits fully paid in the period and not in loan_payments)
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

    # Combine into per-visit installments map
    from collections import defaultdict
    installments_map = defaultdict(list)  # visit_id -> list of {date, amount, ts}

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
            # Sort installments by full timestamp desc (newest first)
            insts_sorted = sorted(insts, key=lambda x: x['ts'], reverse=True)
            installments_map[vid] = insts_sorted
            latest_date = insts_sorted[0]['date']
            latest_amount = insts_sorted[0]['amount']
            latest_ts = insts_sorted[0]['ts']
            # sum of installments with date == today
            today_total = sum(inst['amount'] for inst in insts_sorted if inst['date'] == today_str)
            visit_latest.append((vid, latest_date, latest_amount, today_total, latest_ts))

        # Order by full timestamp DESC (true newest-first, no day-level ties)
        visit_latest.sort(key=lambda x: x[4], reverse=True)

        # Pagination: take a page starting at offset, and detect if more remain
        total_count = len(visit_latest)
        page = visit_latest[offset:offset + limit]
        has_more = (offset + limit) < total_count
        top_visit_ids = [v[0] for v in page]

        # Fetch visit metadata including amount_paid so we can compute outstanding
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

            for vid, latest_date, latest_amount, today_total, latest_ts in page:
                vrow = visits_by_id.get(vid)
                if vrow:
                    # Choose summary: prefer today's total when present, otherwise latest
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
                        'installments': installments_map.get(vid, [])
                    })

    return grouped_transactions, has_more


@app.route('/finance/transactions')
def finance_transactions():
    """Lazy-load endpoint: returns the next batch of grouped transaction rows as HTML."""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'error': 'No clinic'}, 403

    if not has_permission(session.get('role', ''), 'finance.view_transactions'):
        return {'error': 'Permission denied'}, 403

    period = request.args.get('period', 'today')
    try:
        offset = int(request.args.get('offset', 0))
    except ValueError:
        offset = 0

    start_date, end_date, period = get_period_dates(period)
    today_str = datetime.date.today().isoformat()

    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    grouped_transactions, has_more = build_grouped_transactions(
        cursor, clinic_id, start_date, end_date, today_str, offset=offset, limit=20
    )
    conn.close()

    html = render_template(
        '_transaction_rows.html',
        grouped_transactions=grouped_transactions,
        today_str=today_str
    )
    return {'html': html, 'has_more': has_more, 'next_offset': offset + 20}


@app.route('/finance')
def finance():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Finance dashboard with revenue, loans, discounts, and expenses"""
    if not has_permission(session.get('role', ''), 'finance.view'):
        flash('You do not have permission to access the Finance page.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    # Get date range filter
    period = request.args.get('period', 'today')
    today = datetime.date.today()
    start_date, end_date, period = get_period_dates(period)
    
    # 1. Total Cash Collected (direct visits paid in full during range)
    cursor.execute('''
        SELECT SUM(amount_paid) FROM visits 
        WHERE status = 'Paid'
        AND clinic_id = ?
        AND id NOT IN (SELECT DISTINCT visit_id FROM loan_payments)
        AND updated_at >= ? AND updated_at <= ?
    ''', (clinic_id, start_date, end_date))
    total_cash_direct = cursor.fetchone()[0] or 0

    # 1b. Total cash from loan payments in range
    cursor.execute('''
        SELECT SUM(loan_payments.amount)
        FROM loan_payments
        JOIN visits ON loan_payments.visit_id = visits.id
        WHERE visits.clinic_id = ?
        AND loan_payments.payment_date >= ? AND loan_payments.payment_date <= ?
    ''', (clinic_id, start_date, end_date))
    total_cash_from_loans = cursor.fetchone()[0] or 0

    total_cash = total_cash_direct + total_cash_from_loans
    
    # 2. Outstanding Loans
    cursor.execute('''
        SELECT SUM(total_fee - COALESCE(discount_amount, 0) - amount_paid) FROM visits 
        WHERE status = 'Loan Active'
          AND clinic_id = ?
    ''', (clinic_id,))
    outstanding_loans = cursor.fetchone()[0] or 0
    
    # 3. Total Discounts Given (within period)
    cursor.execute('''
        SELECT SUM(discount_amount) FROM visits 
        WHERE discount_amount > 0
        AND clinic_id = ?
        AND updated_at >= ? AND updated_at <= ?
    ''', (clinic_id, start_date, end_date))
    total_discounts = cursor.fetchone()[0] or 0
    
    # 4. Net Revenue
    net_revenue = total_cash
    
    # 5. Total Expenses (within period)
    cursor.execute('''
        SELECT SUM(amount) FROM expenses 
        WHERE clinic_id = ?
        AND expense_date >= ? AND expense_date <= ?
    ''', (clinic_id, start_date[:10], end_date[:10]))
    total_expenses = cursor.fetchone()[0] or 0
    
    # 6. Net Profit
    net_profit = net_revenue - total_expenses
    
    # -------------------------
    # Build grouped (per-visit) transactions with installments
    # (first page of 20; further pages loaded lazily via /finance/transactions)
    # -------------------------
    today_str = today.isoformat()
    grouped_transactions, has_more = build_grouped_transactions(
        cursor, clinic_id, start_date, end_date, today_str, offset=0, limit=20
    )
    
    # 7. Recent expenses (last 10 within selected period)
    cursor.execute('''
        SELECT id, expense_date, category, description, amount
        FROM expenses
        WHERE clinic_id = ?
        AND expense_date >= ? AND expense_date <= ?
        ORDER BY expense_date DESC
        LIMIT 10
    ''', (clinic_id, start_date[:10], end_date[:10]))
    recent_expenses = cursor.fetchall()
    
    conn.close()
    
    return render_template(
        'finance.html',
        period=period,
        total_cash=total_cash,
        outstanding_loans=outstanding_loans,
        total_discounts=total_discounts,
        net_revenue=net_revenue,
        total_expenses=total_expenses,
        net_profit=net_profit,
        grouped_transactions=grouped_transactions,
        has_more=has_more,
        recent_expenses=recent_expenses,
        role=session.get('role'),
        today_str=today_str  # <-- Add this for template to show "Today" text
    )


@app.route('/finance/add_expense', methods=['POST'])
def add_expense():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Add a new expense entry"""
    if not has_permission(session.get('role', ''), 'finance.add_expense'):
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    expense_date = request.form.get('expense_date')
    category = request.form.get('category', 'Other')
    description = request.form.get('description', '').strip()
    amount = request.form.get('amount')
    
    try:
        amount = int(float(amount) * 100)  # Convert to Tambala
    except (TypeError, ValueError):
        flash('Invalid expense amount.', 'danger')
        return redirect(url_for('finance'))
    
    if not expense_date or amount <= 0:
        flash('Please fill in all required fields correctly.', 'danger')
        return redirect(url_for('finance'))
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO expenses (uuid, clinic_id, expense_date, category, description, amount, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_id, expense_date, category, description, amount, datetime.datetime.now().isoformat()))
    
    conn.commit()
    conn.close()
    
    flash('Expense added successfully!', 'success')
    return redirect(url_for('finance'))


@app.route('/finance/delete_expense/<int:expense_id>', methods=['POST'])
def delete_expense(expense_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Delete an expense entry"""
    if not has_permission(session.get('role', ''), 'finance.delete_expense'):
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM expenses WHERE id = ? AND clinic_id = ?", (expense_id, clinic_id))
    conn.commit()
    conn.close()
    
    flash('Expense deleted.', 'success')
    return redirect(url_for('finance'))
    
# ------------------------------------------------------------------
# STAFF MANAGEMENT ROUTES
# ------------------------------------------------------------------
@app.route('/staff/add', methods=['POST'])
@require_permission('staff.add', json_response=True)
def add_staff():
    """Add a staff member to the current clinic.

    If the username already belongs to an existing staff account (i.e. this
    person already works at another clinic), that existing account is linked
    to this clinic with the role given here — it is NOT duplicated. This is
    how a staff member ends up working at more than one clinic, each with
    its own role.
    """
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}

    full_name = request.form.get('full_name', '').strip()
    role = request.form.get('role', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    
    if not full_name or not role or not username:
        return {'success': False, 'error': 'Name, role, and username are required.'}
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    try:
        # Is this username an existing staff account (e.g. already working at another clinic)?
        cursor.execute("SELECT id FROM staff WHERE username = ?", (username,))
        existing = cursor.fetchone()
        
        if existing:
            new_staff_id = existing[0]
            
            # Already linked to this clinic?
            cursor.execute('''
                SELECT 1 FROM staff_clinics WHERE staff_id = ? AND clinic_id = ?
            ''', (new_staff_id, clinic_id))
            if cursor.fetchone():
                conn.close()
                return {'success': False, 'error': 'This staff member is already assigned to your clinic.'}
            
            cursor.execute('''
                INSERT INTO staff_clinics (staff_id, clinic_id, role)
                VALUES (?, ?, ?)
            ''', (new_staff_id, clinic_id, role))
            
            conn.commit()
            log_audit('ADD_STAFF', 'staff', new_staff_id,
              old_value=None, new_value=f"Linked existing staff to clinic. Role: {role}, Username: {username}")
            conn.close()
            return {'success': True}
        
        if not password:
            conn.close()
            return {'success': False, 'error': 'Password is required for a new staff account.'}
        
        hashed_pw = generate_password_hash(password)
        
        # 1. Insert into staff (no clinic_id column anymore)
        cursor.execute('''
            INSERT INTO staff (uuid, full_name, role, username, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), full_name, role, username, hashed_pw, datetime.datetime.now().isoformat()))
        
        new_staff_id = cursor.lastrowid
        
        # 2. Link them to the current clinic via staff_clinics, with their role AT THIS clinic
        cursor.execute('''
            INSERT INTO staff_clinics (staff_id, clinic_id, role)
            VALUES (?, ?, ?)
        ''', (new_staff_id, clinic_id, role))
        
        conn.commit()
        log_audit('ADD_STAFF', 'staff', new_staff_id, 
          old_value=None, new_value=f"Role: {role}, Username: {username}")
        conn.close()
        return {'success': True}
    except sqlite3.IntegrityError:
        conn.close()
        return {'success': False, 'error': 'A staff member with this username already exists.'}
    except Exception as e:
        conn.close()
        return {'success': False, 'error': str(e)}


@app.route('/staff/edit', methods=['POST'])
@require_permission('staff.edit', json_response=True)
def edit_staff():
    """Edit an existing staff member"""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}

    staff_id = request.form.get('staff_id')
    full_name = request.form.get('full_name', '').strip()
    role = request.form.get('role', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    
    if not staff_id or not full_name or not role or not username:
        return {'success': False, 'error': 'Name, role, and username are required.'}
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    # Make sure this staff member actually belongs to the current clinic
    cursor.execute('''
        SELECT 1 FROM staff_clinics WHERE staff_id = ? AND clinic_id = ?
    ''', (staff_id, clinic_id))
    if not cursor.fetchone():
        conn.close()
        return {'success': False, 'error': 'This staff member is not part of your clinic.'}
    
    try:
        # Fetch OLD values for audit logging
        cursor.execute('''
            SELECT full_name, username, role FROM staff WHERE id = ?
        ''', (staff_id,))
        old_staff = cursor.fetchone()
        
        cursor.execute('''
            SELECT role FROM staff_clinics WHERE staff_id = ? AND clinic_id = ?
        ''', (staff_id, clinic_id))
        old_clinic_role = cursor.fetchone()
        
        old_full_name = old_staff[0] if old_staff else 'Unknown'
        old_username = old_staff[1] if old_staff else 'Unknown'
        old_global_role = old_staff[2] if old_staff else 'Unknown'
        old_clinic_role_val = old_clinic_role[0] if old_clinic_role else 'Unknown'
        
        if password:
            # Update with new password
            hashed_pw = generate_password_hash(password)
            cursor.execute('''
                UPDATE staff SET full_name = ?, username = ?, password_hash = ?, updated_at = ?
                WHERE id = ?
            ''', (full_name, username, hashed_pw, datetime.datetime.now().isoformat(), staff_id))
        else:
            # Update without changing password
            cursor.execute('''
                UPDATE staff SET full_name = ?, username = ?, updated_at = ?
                WHERE id = ?
            ''', (full_name, username, datetime.datetime.now().isoformat(), staff_id))
        
        # Role is per-clinic: update it only for THIS clinic's membership
        cursor.execute('''
            UPDATE staff_clinics SET role = ? WHERE staff_id = ? AND clinic_id = ?
        ''', (role, staff_id, clinic_id))
        
        conn.commit()
        
        # Build detailed audit diff
        audit_parts = []
        if old_full_name != full_name:
            audit_parts.append(f"Name: '{old_full_name}' → '{full_name}'")
        if old_username != username:
            audit_parts.append(f"Username: '{old_username}' → '{username}'")
        if old_clinic_role_val != role:
            audit_parts.append(f"Role (this clinic): '{old_clinic_role_val}' → '{role}'")
        if password:
            audit_parts.append("Password: RESET")
        
        log_audit('EDIT_STAFF', 'staff', staff_id,
          old_value=", ".join(audit_parts) if audit_parts else "No changes",
          new_value="Updated details")
        
        conn.close()
        return {'success': True}
    except sqlite3.IntegrityError:
        conn.close()
        return {'success': False, 'error': 'A staff member with this username already exists.'}
    except Exception as e:
        conn.close()
        return {'success': False, 'error': str(e)}


@app.route('/staff/deactivate/<int:staff_id>', methods=['POST'])
@require_permission('staff.deactivate', json_response=True)
def deactivate_staff(staff_id):
    """Soft-deactivate a staff member (deactivation is account-wide, across all their clinics)"""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}

    # Prevent self-deactivation
    if staff_id == session.get('staff_id'):
        return {'success': False, 'error': 'You cannot deactivate your own account.'}
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    # Make sure this staff member actually belongs to the current clinic
    cursor.execute('''
        SELECT 1 FROM staff_clinics WHERE staff_id = ? AND clinic_id = ?
    ''', (staff_id, clinic_id))
    if not cursor.fetchone():
        conn.close()
        return {'success': False, 'error': 'This staff member is not part of your clinic.'}
    
    cursor.execute('''
        UPDATE staff SET is_active = 0, updated_at = ?
        WHERE id = ?
    ''', (datetime.datetime.now().isoformat(), staff_id))
    conn.commit()
    log_audit('DEACTIVATE_STAFF', 'staff', staff_id, 
          old_value='Active', new_value='Deactivated')
    conn.close()
    return {'success': True}


@app.route('/staff/reactivate/<int:staff_id>', methods=['POST'])
@require_permission('staff.reactivate', json_response=True)
def reactivate_staff(staff_id):
    """Reactivate a deactivated staff member"""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}

    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    # Make sure this staff member actually belongs to the current clinic
    cursor.execute('''
        SELECT 1 FROM staff_clinics WHERE staff_id = ? AND clinic_id = ?
    ''', (staff_id, clinic_id))
    if not cursor.fetchone():
        conn.close()
        return {'success': False, 'error': 'This staff member is not part of your clinic.'}
    
    cursor.execute('''
        UPDATE staff SET is_active = 1, updated_at = ?
        WHERE id = ?
    ''', (datetime.datetime.now().isoformat(), staff_id))
    conn.commit()
    log_audit('REACTIVATE_STAFF', 'staff', staff_id, 
          old_value='Inactive', new_value='Reactivated')
    conn.close()
    return {'success': True}    
    
    

# ------------------------------------------------------------------
# STAFF MANAGEMENT
# ------------------------------------------------------------------
@app.route('/staff', methods=['GET'])
@require_permission('staff.view')
def staff():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))

    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    # Fetch all staff for this clinic, with their ROLE AT THIS CLINIC
    cursor.execute('''
        SELECT staff.id, staff.full_name, staff_clinics.role, staff.username, staff.is_active
        FROM staff
        JOIN staff_clinics ON staff.id = staff_clinics.staff_id
        WHERE staff_clinics.clinic_id = ?
        ORDER BY staff_clinics.role, staff.full_name
    ''', (clinic_id,))
    staff_list = cursor.fetchall()
    conn.close()
    
    return render_template('staff.html', staff_list=staff_list, role=session.get('role'))
    
    
def get_dashboard_data(clinic_id):
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    # --- Queue stats ---
    cursor.execute("SELECT COUNT(*) FROM appointments WHERE clinic_id = ? AND status = 'Waiting'", (clinic_id,))
    waiting_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM appointments WHERE clinic_id = ? AND status = 'Pending'", (clinic_id,))
    pending_count = cursor.fetchone()[0]

    total_queue_count = waiting_count + pending_count

    # --- "Seen Today" (Consultation + Retail + Old Loan Repayments) ---
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

    # --- Inventory stats ---
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

    # --- Price List stats ---
    cursor.execute("SELECT COUNT(*) FROM price_list WHERE clinic_id = ? AND is_active = 1", (clinic_id,))
    priced_items_count = cursor.fetchone()[0]

    # --- Cashier stats ---
    cursor.execute("SELECT COUNT(*) FROM visits WHERE clinic_id = ? AND status = 'Ready for Cashier'", (clinic_id,))
    cashier_count = cursor.fetchone()[0]

    # --- Loans stats ---
    cursor.execute("SELECT COUNT(*) FROM visits WHERE clinic_id = ? AND status = 'Loan Active'", (clinic_id,))
    loans_count = cursor.fetchone()[0]

    # --- Total Cash Collected TODAY ---
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

    # --- Appointments stats ---
    cursor.execute("SELECT COUNT(*) FROM appointments WHERE clinic_id = ? AND status IN ('Pending', 'Scheduled')", (clinic_id,))
    appointments_count = cursor.fetchone()[0]

    conn.close()

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


@app.route('/dashboard')
def dashboard():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))

    stats = get_dashboard_data(clinic_id)

    return render_template(
        'dashboard.html',
        role=session.get('role'),
        **stats
    )


@app.route('/api/dashboard')
def api_dashboard():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    stats = get_dashboard_data(clinic_id)
    stats['fetched_at'] = datetime.datetime.now().isoformat()
    return jsonify(stats)

# ------------------------------------------------------------------
# ABOUT & CONTACT PAGES
# ------------------------------------------------------------------
@app.route('/about')
def about():
    """Display the About page with project information."""
    return render_template('about.html', role=session.get('role'))

@app.route('/contact')
def contact():
    """Display the Contact Us page."""
    return render_template('contact.html', role=session.get('role'))
    
    
@app.route('/audit_log')
@require_permission('audit.view')
def view_audit_log():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    
    conn = sqlite3.connect('clinic.db')
    conn.row_factory = sqlite3.Row  # <--- THIS is the magic line!
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
    
    # Convert sqlite3.Row objects to dicts so HTML can use keys
    logs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return render_template('audit_log.html', logs=logs, role=session.get('role'))   
    
# ------------------------------------------------------------------
# RUN THE APP
# ------------------------------------------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)