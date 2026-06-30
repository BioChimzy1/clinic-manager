import sqlite3
import datetime
import uuid
import math
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session, flash
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)

# THIS IS REQUIRED FOR LOGIN SESSIONS TO WORK
app.secret_key = 'your_secret_key_here_change_this_later'

app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# ------------------------------------------------------------------
# HELPER FUNCTION
# ------------------------------------------------------------------
def get_current_clinic_id():
    """Returns the clinic_id of the currently logged-in staff member."""
    staff_id = session.get('staff_id')
    if not staff_id:
        return None
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute("SELECT clinic_id FROM staff WHERE id = ? AND is_active = 1", (staff_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] else None
    
# ------------------------------------------------------------------
# AUDIT LOGGING HELPER
# ------------------------------------------------------------------
def log_audit(action, table_name, record_id, old_value=None, new_value=None):
    """Log an action to the audit_log table."""
    staff_id = session.get('staff_id')
    if not staff_id:
        return  # Skip logging if no staff logged in (shouldn't happen)
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO audit_log (staff_id, action, table_name, record_id, old_value, new_value, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (staff_id, action, table_name, record_id, old_value, new_value, datetime.datetime.now().isoformat()))
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
    
    # 2. staff
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE,
            clinic_id INTEGER,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            is_synced INTEGER DEFAULT 0,
            FOREIGN KEY (clinic_id) REFERENCES clinics (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_staff_uuid ON staff(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_staff_clinic ON staff(clinic_id);')
    
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
            action TEXT NOT NULL,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            old_value TEXT,
            new_value TEXT,
            timestamp TEXT,
            FOREIGN KEY (staff_id) REFERENCES staff (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_log_staff ON audit_log(staff_id);')
    
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
            INSERT INTO staff (uuid, clinic_id, full_name, role, username, password_hash, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(uuid.uuid4()),
            None,  # No clinic yet; the setup page will assign one
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
        cursor.execute("SELECT id, role, password_hash, clinic_id FROM staff WHERE username = ? AND is_active = 1", (username,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user[2], password):
            session['staff_id'] = user[0]
            session['role'] = user[1]
            
            # Redirect to setup if they have no clinic
            if not user[3] or user[3] == 0:
                return redirect(url_for('setup_clinic'))
            
            return redirect(url_for('dashboard'))
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
    
    cursor.execute("SELECT clinic_id FROM staff WHERE id = ?", (session['staff_id'],))
    existing = cursor.fetchone()
    if existing and existing[0]:
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
        
        cursor.execute('''
            UPDATE staff SET clinic_id = ?, updated_at = ? WHERE id = ?
        ''', (clinic_id, datetime.datetime.now().isoformat(), session['staff_id']))
        
        conn.commit()
        conn.close()
        
        flash(f'Clinic "{clinic_name}" created successfully! You are now the Admin.', 'success')
        return redirect(url_for('dashboard'))
    
    conn.close()
    return render_template('setup_clinic.html')

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

# ------------------------------------------------------------------
# ACTIVE QUEUE ROUTE
# ------------------------------------------------------------------
@app.route('/queue')
def queue():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT patients.id, patients.name, patients.sex, patients.phone, 
               appointments.appointment_type, appointments.status
        FROM patients
        JOIN appointments ON patients.id = appointments.patient_id
        WHERE appointments.status IN ('Waiting', 'Pending')
        ORDER BY appointments.created_at ASC
    ''')
    queue_list = cursor.fetchall()
    conn.close()
    
    return render_template('queue.html', queue=queue_list, total_in_queue=len(queue_list))
    
# ------------------------------------------------------------------
# APPOINTMENT MANAGEMENT
# ------------------------------------------------------------------
@app.route('/appointments')
def appointments():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """View all scheduled appointments"""
    allowed_roles = ['admin', 'cashier', 'doctor', 'receptionist']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
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
        WHERE appointments.status NOT IN ('Waiting', 'Pending', 'In Progress', 'Completed')
        ORDER BY appointments.appointment_date ASC
    ''')
    appointment_list = cursor.fetchall()
    conn.close()

    return render_template('appointments.html', appointments=appointment_list, role=session.get('role'))


@app.route('/appointments/schedule', methods=['GET', 'POST'])
def schedule_appointment():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Schedule a new appointment for an existing or new patient"""
    allowed_roles = ['admin', 'cashier', 'doctor', 'receptionist']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
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
    cursor.execute("SELECT id, name, phone FROM patients WHERE is_active = 1 ORDER BY name")
    patients = cursor.fetchall()
    conn.close()

    return render_template('schedule_appointment.html', patients=patients)


@app.route('/appointments/update/<int:appt_id>', methods=['POST'])
def update_appointment(appt_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Update appointment status (Confirm, Cancel, Reschedule, Missed, Check In)"""
    allowed_roles = ['admin', 'cashier', 'doctor', 'receptionist']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))

    data = request.get_json()
    action = data.get('action')
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
    allowed_roles = ['admin', 'cashier', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
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
        ORDER BY appointments.created_at DESC
        LIMIT 1
    ''', (patient_id,))
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
def inventory():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    if request.method == 'POST':
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
                  AND is_active = 1
                  AND id != ?
            ''', (item_name, category, min_alert, datetime.datetime.now().isoformat(),
                  original_name, original_category, inventory_id))

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
                WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND category = ? AND expiry_date = ? AND is_active = 1
                ORDER BY id ASC
            """, (item_name, category, expiry))
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
                WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND category = ? AND expiry_date = ? AND is_active = 1
            """, (item_name, category, expiry))
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
    cursor.execute("SELECT id, category, item_name, quantity, min_alert_level, expiry_date FROM inventory WHERE is_active = 1 ORDER BY expiry_date ASC")
    items = cursor.fetchall()
    cursor.execute("SELECT item_name FROM inventory WHERE is_active = 1")
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

    cursor.execute("SELECT quantity FROM inventory WHERE id = ? AND is_active = 1", (item_id,))
    row = cursor.fetchone()

    if row is None:
        conn.close()
        return {'success': False, 'error': 'Item not found.'}

    current_qty = row[0]

    if amount_to_remove > current_qty:
        conn.close()
        return {'success': False, 'error': f'Cannot remove {amount_to_remove} -- only {current_qty} in stock.'}

    new_qty = current_qty - amount_to_remove

    cursor.execute(
        "UPDATE inventory SET quantity = ?, updated_at = ? WHERE id = ?",
        (new_qty, datetime.datetime.now().isoformat(), item_id)
    )
    conn.commit()
    conn.close()

    return {'success': True, 'new_quantity': new_qty}




# ------------------------------------------------------------------
# PRICE LIST MANAGEMENT
# ------------------------------------------------------------------
@app.route('/price_list', methods=['GET', 'POST'])
def price_list():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    if request.method == 'POST':
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
        ORDER BY
            CASE
                WHEN price_list.inventory_id IS NULL THEN 0
                WHEN COALESCE(stock.usable_qty, 0) > 0 THEN 0
                WHEN COALESCE(stock.expired_qty, 0) > 0 THEN 1
                ELSE 2
            END,
            price_list.item_name
    ''', (today_str, today_str))
    price_list = cursor.fetchall()
    
    cursor.execute("SELECT item_name, category FROM inventory WHERE is_active = 1")
    inventory_items = cursor.fetchall()
    
    conn.close()
    
    return render_template('price_list.html', price_list=price_list, inventory_items=inventory_items)

@app.route('/price_list/update/<int:item_id>', methods=['POST'])
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
        cursor.execute("SELECT price, quantity, item_type, item_name FROM price_list WHERE id = ?", (item_id,))
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
            WHERE id = ?
        ''', (new_price, new_qty, datetime.datetime.now().isoformat(), item_id))

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
        WHERE id = ?
    ''', (datetime.datetime.now().isoformat(), item_id))
    conn.commit()
    log_audit('DELETE_PRICE', 'price_list', item_id, 
          old_value='Active', new_value='Deactivated (Soft Delete)')
    conn.close()
    flash('Price item deactivated successfully. History preserved.', 'success')
    return redirect(url_for('price_list'))
    
@app.route('/price_list/history/<int:item_id>', methods=['GET'])
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
    cursor.execute("SELECT id, full_name FROM staff WHERE clinic_id = ? AND is_active = 1 ORDER BY full_name", (clinic_id,))
    staff = cursor.fetchall()
    conn.close()
    return {'staff': [{'id': s[0], 'name': s[1]} for s in staff]}    
    
    
@app.route('/visit/<int:patient_id>', methods=['GET', 'POST'])
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
        WHERE patients.id = ? AND appointments.status IN ('Waiting', 'Pending') 
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
        cursor.execute('SELECT id FROM visits WHERE appointment_id = ?', (appointment_id,))
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
        ORDER BY price_list.item_type, price_list.item_name
    ''', (today_str, today_str))
    all_priced_items = cursor.fetchall()

    conn.close()

    return render_template(
        'visit.html',
        patient_id=p_id,
        patient_name=p_name,
        patient_sex=p_sex,
        patient_dob=p_dob,
        appointment_type=appointment_type,
        priced_items=all_priced_items
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
    # Role check: Admin, Cashier, or Doctor (case-insensitive)
    allowed_roles = ['admin', 'cashier', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
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
                          
@app.route('/cashier/view/<int:visit_id>', methods=['GET'])
def view_cashier_invoice(visit_id):
    """View-only route to show the items charged for a specific visit.
    Used by both the Cashier page (consultation visits) and the Retail
    page (resuming a pending retail draft) -- LEFT JOIN is required so
    retail visits, which have patient_id = NULL, don't 404 here."""
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    # Fetch Visit Details
    cursor.execute('''
        SELECT visits.total_fee, visits.amount_paid, visits.diagnosis, visits.status,
               patients.name AS patient_name
        FROM visits
        LEFT JOIN patients ON visits.patient_id = patients.id
        WHERE visits.id = ?
    ''', (visit_id,))
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
    # Role check
    allowed_roles = ['admin', 'cashier', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
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
            FROM visits WHERE id = ?
        ''', (visit_id,))
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
                WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND is_active = 1
                ORDER BY expiry_date ASC
            ''', (item_name,))
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


@app.route('/loans', methods=['GET'])
def loans():
    """Single combined view of every visit with an active loan, whether
    it came from a consultation (has a real patient) or a retail sale
    (patient_id IS NULL, is_retail = 1). This is now the ONLY place
    loans are browsed from -- /cashier intentionally excludes
    'Loan Active' visits entirely (it only shows brand-new 'Ready for
    Cashier' visits), and /retail's pending-drafts list is a separate
    concept (unpaid drafts, not loans specifically -- a retail loan
    will appear in both lists, which is correct: one answers "what's
    an unfinished retail sale", the other answers "who owes money").
    Linking into loan_details.html from here is what actually lets
    someone record a repayment; that page's form is already correct
    (additive, clamped to the real balance, no repeated witness
    requirement) so no payment logic needed to change here."""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))

    allowed_roles = ['admin', 'cashier', 'doctor', 'receptionist']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
        flash('You do not have permission to access Loans.', 'danger')
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

    today_str = datetime.date.today().isoformat()
    return render_template('loans.html', loan_list=loan_list, today_str=today_str, role=session.get('role'))


@app.route('/cashier/loan/<int:visit_id>', methods=['GET'])
def loan_details(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """View loan payment history for a specific visit"""
    # Role check
    allowed_roles = ['admin', 'cashier', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
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
    # Role check
    allowed_roles = ['admin', 'cashier', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
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
            FROM visits WHERE id = ?
        ''', (visit_id,))
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
    
    allowed_roles = ['admin', 'cashier', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
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
    cursor.execute('''
        SELECT id, item_name, item_type, price, quantity
        FROM price_list
        WHERE is_active = 1
        ORDER BY item_name
    ''')
    items = cursor.fetchall()
    conn.close()

    return render_template('retail.html', items=items, role=session.get('role'))

@app.route('/retail/create_draft', methods=['POST'])
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
                WHERE price_list.id = ? AND price_list.is_active = 1
            ''', (today_str, item['price_list_id']))
            
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
@app.route('/finance')
def finance():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    """Finance dashboard with revenue, loans, discounts, and expenses"""
    # Role check
    allowed_roles = ['admin', 'cashier', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
        flash('You do not have permission to access the Finance page.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    # Get date range filter
    period = request.args.get('period', 'today')
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
    
    # 1. Total Cash Collected (direct visits paid in full during range)
    cursor.execute('''
        SELECT SUM(amount_paid) FROM visits 
        WHERE status = 'Paid' 
        AND id NOT IN (SELECT DISTINCT visit_id FROM loan_payments)
        AND updated_at >= ? AND updated_at <= ?
    ''', (start_date, end_date))
    total_cash_direct = cursor.fetchone()[0] or 0

    # 1b. Total cash from loan payments in range
    cursor.execute('''
        SELECT SUM(amount) FROM loan_payments
        WHERE payment_date >= ? AND payment_date <= ?
    ''', (start_date, end_date))
    total_cash_from_loans = cursor.fetchone()[0] or 0

    total_cash = total_cash_direct + total_cash_from_loans
    
    # 2. Outstanding Loans
    cursor.execute('''
        SELECT SUM(total_fee - COALESCE(discount_amount, 0) - amount_paid) FROM visits 
        WHERE status = 'Loan Active'
    ''')
    outstanding_loans = cursor.fetchone()[0] or 0
    
    # 3. Total Discounts Given (within period)
    cursor.execute('''
        SELECT SUM(discount_amount) FROM visits 
        WHERE discount_amount > 0 
        AND updated_at >= ? AND updated_at <= ?
    ''', (start_date, end_date))
    total_discounts = cursor.fetchone()[0] or 0
    
    # 4. Net Revenue
    net_revenue = total_cash
    
    # 5. Total Expenses (within period)
    cursor.execute('''
        SELECT SUM(amount) FROM expenses 
        WHERE expense_date >= ? AND expense_date <= ?
    ''', (start_date[:10], end_date[:10]))
    total_expenses = cursor.fetchone()[0] or 0
    
    # 6. Net Profit
    net_profit = net_revenue - total_expenses
    
    # -------------------------
    # Build grouped (per-visit) transactions with installments
    # -------------------------
    today_str = today.isoformat()
    # 1) loan_payments grouped by visit_id and by DATE(payment_date)
    #    NOTE: we keep MAX(payment_date) as a full timestamp (pay_ts) purely
    #    for ordering -- pay_date stays a plain date for display/grouping.
    cursor.execute('''
        SELECT visit_id, DATE(payment_date) AS pay_date, SUM(amount) AS amount_sum,
               MAX(payment_date) AS pay_ts
        FROM loan_payments
        WHERE payment_date >= ? AND payment_date <= ?
        GROUP BY visit_id, DATE(payment_date)
    ''', (start_date, end_date))
    loan_grouped = cursor.fetchall()  # list of (visit_id, pay_date, amount_sum, pay_ts)
    
    # 2) direct visit payments (visits fully paid in the period and not in loan_payments)
    cursor.execute('''
        SELECT id AS visit_id, DATE(updated_at) AS pay_date, amount_paid AS amount_sum,
               updated_at AS pay_ts
        FROM visits
        WHERE status = 'Paid'
          AND id NOT IN (SELECT DISTINCT visit_id FROM loan_payments)
          AND updated_at >= ? AND updated_at <= ?
    ''', (start_date, end_date))
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
    
    # Build grouped_transactions, limit 10 by most recent payment date (prefer visits with payments today)
    grouped_transactions = []
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
            # We'll choose a summary per-visit later (prefer today's total if present)
            visit_latest.append((vid, latest_date, latest_amount, today_total, latest_ts))
        
        # Order by full timestamp DESC (true newest-first, no day-level ties) and keep top 10
        visit_latest.sort(key=lambda x: x[4], reverse=True)
        top_visits = visit_latest[:10]
        top_visit_ids = [v[0] for v in top_visits]
        
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
            
            for vid, latest_date, latest_amount, today_total, latest_ts in top_visits:
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
    
    # 7. Recent expenses (last 10 within selected period)
    cursor.execute('''
        SELECT id, expense_date, category, description, amount
        FROM expenses
        WHERE expense_date >= ? AND expense_date <= ?
        ORDER BY expense_date DESC
        LIMIT 10
    ''', (start_date[:10], end_date[:10]))
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
    allowed_roles = ['admin', 'cashier', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
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
    allowed_roles = ['admin', 'cashier', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()
    
    flash('Expense deleted.', 'success')
    return redirect(url_for('finance'))
    
# ------------------------------------------------------------------
# STAFF MANAGEMENT ROUTES
# ------------------------------------------------------------------
@app.route('/staff/add', methods=['POST'])
def add_staff():
    """Add a new staff member to the current clinic"""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}
    
    allowed_roles = ['admin', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
        return {'success': False, 'error': 'Permission denied.'}
    
    full_name = request.form.get('full_name', '').strip()
    role = request.form.get('role', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    
    if not full_name or not role or not username or not password:
        return {'success': False, 'error': 'All fields are required.'}
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    try:
        hashed_pw = generate_password_hash(password)
        cursor.execute('''
            INSERT INTO staff (uuid, clinic_id, full_name, role, username, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), clinic_id, full_name, role, username, hashed_pw, datetime.datetime.now().isoformat()))
        conn.commit()
        log_audit('ADD_STAFF', 'staff', cursor.lastrowid, 
          old_value=None, new_value=f"Role: {role}, Username: {username}")
        conn.close()
        return {'success': True}
    except sqlite3.IntegrityError:
        conn.close()
        return {'success': False, 'error': 'Username already exists.'}
    except Exception as e:
        conn.close()
        return {'success': False, 'error': str(e)}


@app.route('/staff/edit', methods=['POST'])
def edit_staff():
    """Edit an existing staff member"""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}
    
    allowed_roles = ['admin', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
        return {'success': False, 'error': 'Permission denied.'}
    
    staff_id = request.form.get('staff_id')
    full_name = request.form.get('full_name', '').strip()
    role = request.form.get('role', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    
    if not staff_id or not full_name or not role or not username:
        return {'success': False, 'error': 'Name, role, and username are required.'}
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    try:
        if password:
            # Update with new password
            hashed_pw = generate_password_hash(password)
            cursor.execute('''
                UPDATE staff SET full_name = ?, role = ?, username = ?, password_hash = ?, updated_at = ?
                WHERE id = ? AND clinic_id = ?
            ''', (full_name, role, username, hashed_pw, datetime.datetime.now().isoformat(), staff_id, clinic_id))
        else:
            # Update without changing password
            cursor.execute('''
                UPDATE staff SET full_name = ?, role = ?, username = ?, updated_at = ?
                WHERE id = ? AND clinic_id = ?
            ''', (full_name, role, username, datetime.datetime.now().isoformat(), staff_id, clinic_id))
        
        conn.commit()
        log_audit('EDIT_STAFF', 'staff', staff_id, 
          old_value=f"Name: {full_name}, Role: {role}", 
          new_value=f"Updated details")
        conn.close()
        return {'success': True}
    except sqlite3.IntegrityError:
        conn.close()
        return {'success': False, 'error': 'Username already exists.'}
    except Exception as e:
        conn.close()
        return {'success': False, 'error': str(e)}


@app.route('/staff/deactivate/<int:staff_id>', methods=['POST'])
def deactivate_staff(staff_id):
    """Soft-deactivate a staff member"""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}
    
    allowed_roles = ['admin', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
        return {'success': False, 'error': 'Permission denied.'}
    
    # Prevent self-deactivation
    if staff_id == session.get('staff_id'):
        return {'success': False, 'error': 'You cannot deactivate your own account.'}
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE staff SET is_active = 0, updated_at = ?
        WHERE id = ? AND clinic_id = ?
    ''', (datetime.datetime.now().isoformat(), staff_id, clinic_id))
    conn.commit()
    log_audit('DEACTIVATE_STAFF', 'staff', staff_id, 
          old_value='Active', new_value='Deactivated')
    conn.close()
    return {'success': True}


@app.route('/staff/reactivate/<int:staff_id>', methods=['POST'])
def reactivate_staff(staff_id):
    """Reactivate a deactivated staff member"""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return {'success': False, 'error': 'No clinic selected.'}
    
    allowed_roles = ['admin', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
        return {'success': False, 'error': 'Permission denied.'}
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE staff SET is_active = 1, updated_at = ?
        WHERE id = ? AND clinic_id = ?
    ''', (datetime.datetime.now().isoformat(), staff_id, clinic_id))
    conn.commit()
    log_audit('REACTIVATE_STAFF', 'staff', staff_id, 
          old_value='Inactive', new_value='Reactivated')
    conn.close()
    return {'success': True}    
    
    

# ------------------------------------------------------------------
# STAFF MANAGEMENT
# ------------------------------------------------------------------
@app.route('/staff', methods=['GET'])
def staff():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    
    # Only Admin and Doctor can manage staff
    allowed_roles = ['admin', 'doctor']
    user_role = session.get('role', '').lower()
    if user_role not in allowed_roles:
        flash('You do not have permission to manage staff.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    
    # Fetch all active staff for this clinic
    cursor.execute('''
        SELECT id, full_name, role, username, is_active
        FROM staff
        WHERE clinic_id = ?
        ORDER BY role, full_name
    ''', (clinic_id,))
    staff_list = cursor.fetchall()
    conn.close()
    
    return render_template('staff.html', staff_list=staff_list, role=session.get('role'))
    
    
@app.route('/dashboard')
def dashboard():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    # --- Queue stats ---
    cursor.execute("SELECT COUNT(*) FROM appointments WHERE status = 'Waiting'")
    waiting_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM appointments WHERE status = 'Pending'")
    pending_count = cursor.fetchone()[0]

    total_queue_count = waiting_count + pending_count

    # --- FIXED: "Seen Today" (Consultation + Retail + Old Loan Repayments) ---
    today = datetime.date.today()
    today_start = today.isoformat() + 'T00:00:00'
    today_end = today.isoformat() + 'T23:59:59'

    cursor.execute('''
        SELECT COUNT(DISTINCT unique_id) FROM (
            -- 1. Consultation visits started today (includes Paid, Ready, Loan Active)
            SELECT id AS unique_id
            FROM visits
            WHERE clinic_id = ? 
            AND visit_date >= ? AND visit_date <= ?
            AND (is_retail IS NULL OR is_retail = 0)
            
            UNION
            
            -- 2. Retail sales created today
            SELECT id AS unique_id
            FROM visits
            WHERE clinic_id = ? 
            AND visit_date >= ? AND visit_date <= ?
            AND is_retail = 1
            
            UNION
            
            -- 3. Loan payments made today on OLDER visits (visit created before today)
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

    # --- Cashier stats: brand-new visits awaiting a first payment ---
    cursor.execute("SELECT COUNT(*) FROM visits WHERE clinic_id = ? AND status = 'Ready for Cashier'", (clinic_id,))
    cashier_count = cursor.fetchone()[0]

    # --- Loans stats: everyone with an active loan, consultation or retail ---
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
    cursor.execute("SELECT COUNT(*) FROM appointments WHERE status IN ('Pending', 'Scheduled')")
    appointments_count = cursor.fetchone()[0]
    
    conn.close()
    
    return render_template(
        'dashboard.html',
        role=session.get('role'),
        waiting_count=waiting_count,
        pending_count=pending_count,
        total_queue_count=total_queue_count,
        seen_today_count=seen_today_count,
        total_items_count=total_items_count,
        low_stock_count=low_stock_count,
        expiring_soon_count=expiring_soon_count,
        priced_items_count=priced_items_count,
        cashier_count=cashier_count,
        loans_count=loans_count,
        today_cash_collected=today_cash_collected,
        appointments_count=appointments_count
    )

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
def view_audit_log():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return redirect(url_for('setup_clinic'))
    
    # Only Admin can view audit logs
    if session.get('role', '').lower() != 'admin':
        flash('Permission denied.', 'danger')
        return redirect(url_for('dashboard'))
    
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT audit_log.id, staff.full_name, audit_log.action, audit_log.table_name, 
               audit_log.record_id, audit_log.old_value, audit_log.new_value, audit_log.timestamp
        FROM audit_log
        LEFT JOIN staff ON audit_log.staff_id = staff.id
        ORDER BY audit_log.timestamp DESC
        LIMIT 50
    ''')
    logs = cursor.fetchall()
    conn.close()
    
    return render_template('audit_log.html', logs=logs, role=session.get('role'))    
    
# ------------------------------------------------------------------
# RUN THE APP
# ------------------------------------------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)