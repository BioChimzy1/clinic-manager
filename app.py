import sqlite3
import datetime
import uuid
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session, flash
from werkzeug.security import check_password_hash

app = Flask(__name__)

# THIS IS REQUIRED FOR LOGIN SESSIONS TO WORK
app.secret_key = 'your_secret_key_here_change_this_later'

app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# ------------------------------------------------------------------
# DATABASE INITIALIZATION (Your existing amazing schema)
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
    
        # 4. appointments (NEW: Includes appointment_type)
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
            visit_date TEXT NOT NULL,
            diagnosis TEXT,
            referral TEXT DEFAULT 'None',
            total_fee INTEGER DEFAULT 0,
            amount_paid INTEGER DEFAULT 0,
            loan_witness TEXT,
            status TEXT DEFAULT 'Open',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            is_synced INTEGER DEFAULT 0,
            FOREIGN KEY (clinic_id) REFERENCES clinics (id),
            FOREIGN KEY (patient_id) REFERENCES patients (id),
            FOREIGN KEY (doctor_id) REFERENCES staff (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_visits_uuid ON visits(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_visits_clinic ON visits(clinic_id);')
    
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
            FOREIGN KEY (visit_id) REFERENCES visits (id)
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
            description TEXT NOT NULL,
            amount INTEGER NOT NULL,
            created_at TEXT,
            is_synced INTEGER DEFAULT 0,
            FOREIGN KEY (clinic_id) REFERENCES clinics (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_expenses_uuid ON expenses(uuid);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_expenses_clinic ON expenses(clinic_id);')
    
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
    if request.path == '/login' or request.path.startswith('/static/'):
        return
    if 'staff_id' not in session:
        return redirect(url_for('login'))

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = sqlite3.connect('clinic.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, role, password_hash FROM staff WHERE username = ? AND is_active = 1", (username,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user[2], password):
            session['staff_id'] = user[0]
            session['role'] = user[1]
            return redirect(url_for('dashboard'))
        return render_template('login.html', error='Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ------------------------------------------------------------------
# PATIENT REGISTRATION (Walk-In OR Appointment)
# ------------------------------------------------------------------
@app.route('/register', methods=['GET', 'POST'])
def register():
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
        ''', (str(uuid.uuid4()), 1, name, date_of_birth, sex, phone, location, datetime.datetime.now().isoformat()))
        
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
            str(uuid.uuid4()), 1, patient_id, 1, 
            datetime.datetime.now().isoformat(), 
            appointment_type,
            'Consultation',
            status,
            datetime.datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
        
        return redirect(url_for('queue'))
        
    return render_template('register.html')

# ------------------------------------------------------------------
# ACTIVE QUEUE ROUTE
# ------------------------------------------------------------------
@app.route('/queue')
def queue():
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
# INVENTORY MANAGEMENT (Add & Edit)
# ------------------------------------------------------------------
@app.route('/inventory', methods=['GET', 'POST'])
def inventory():
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
            ''', (str(uuid.uuid4()), 1, category, item_name, quantity, min_alert, expiry, datetime.datetime.now().isoformat()))

            conn.commit()
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
        ''', (str(uuid.uuid4()), 1, inventory_id, item_type, item_name, price, quantity, datetime.datetime.now().isoformat()))
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
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    data = request.get_json()
    new_price = data.get('price')
    new_qty = data.get('quantity')

    # Guardrail: neither price nor quantity should ever go negative here.
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
        cursor.execute('''
            UPDATE price_list 
            SET price = ?, quantity = ?, updated_at = ? 
            WHERE id = ?
        ''', (new_price, new_qty, datetime.datetime.now().isoformat(), item_id))
        conn.commit()
        conn.close()
        return {'success': True}
    except Exception as e:
        conn.close()
        return {'success': False, 'error': str(e)}

@app.route('/price_list/delete/<int:item_id>')
def delete_price_item(item_id):
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM price_list WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('price_list'))

# ------------------------------------------------------------------
# STAFF MANAGEMENT (Coming Soon)
# ------------------------------------------------------------------
@app.route('/staff')
def staff():
    return render_template('staff.html', role=session.get('role'))

@app.route('/dashboard')
def dashboard():
    conn = sqlite3.connect('clinic.db')
    cursor = conn.cursor()

    # --- Queue stats ---
    cursor.execute("SELECT COUNT(*) FROM appointments WHERE status = 'Waiting'")
    waiting_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM appointments WHERE status = 'Pending'")
    pending_count = cursor.fetchone()[0]

    total_queue_count = waiting_count + pending_count

    # "Seen Today" has no real data source yet (no completed/done status or
    # timestamp exists until the Record Visit flow is built). Shown as a
    # placeholder 0 for now, intentionally, until that feature exists.
    seen_today_count = 0

    # --- Inventory stats ---
    cursor.execute("SELECT COUNT(*) FROM inventory WHERE is_active = 1")
    total_items_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM inventory WHERE is_active = 1 AND quantity <= min_alert_level")
    low_stock_count = cursor.fetchone()[0]

    # "Expiring soon" = within the next 14 days (inclusive of today)
    today = datetime.date.today()
    cutoff = (today + datetime.timedelta(days=14)).isoformat()
    cursor.execute(
        "SELECT COUNT(*) FROM inventory WHERE is_active = 1 AND expiry_date IS NOT NULL AND expiry_date <= ? AND expiry_date >= ?",
        (cutoff, today.isoformat())
    )
    expiring_soon_count = cursor.fetchone()[0]

    # --- Price List stats ---
    cursor.execute("SELECT COUNT(*) FROM price_list WHERE is_active = 1")
    priced_items_count = cursor.fetchone()[0]

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
        priced_items_count=priced_items_count
    )

# ------------------------------------------------------------------
# RUN THE APP
# ------------------------------------------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)