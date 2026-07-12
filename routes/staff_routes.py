import uuid
import sqlite3
import datetime
from flask import Blueprint, request, session, jsonify, current_app
from werkzeug.security import generate_password_hash
from roles_permissions import ROLES
from db import get_db
from utils.security import require_permission
from utils.audit import log_audit
from utils.data_helpers import get_current_clinic_id

bp = Blueprint('staff_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - STAFF
# ------------------------------------------------------------------
@bp.route('/api/staff/list', methods=['GET'])
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

@bp.route('/api/staff', methods=['GET'])
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

@bp.route('/api/staff/add', methods=['POST'])
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

    role_key = role.strip().lower()
    if role_key not in ROLES:
        return jsonify({'success': False, 'error': 'Invalid role.'}), 400
    if role_key == 'admin' and session.get('role', '').strip().lower() != 'admin':
        return jsonify({'success': False, 'error': 'Only an administrator can assign the admin role.'}), 403

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
        
        if len(password) < 8:
            return jsonify({'success': False, 'error': 'Password must be at least 8 characters long.'}), 400
        
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
        current_app.logger.exception("Unhandled error in request")
        return jsonify({'success': False, 'error': 'Something went wrong. Please try again.'}), 500

@bp.route('/api/staff/edit', methods=['POST'])
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

    role_key = role.strip().lower()
    if role_key not in ROLES:
        return jsonify({'success': False, 'error': 'Invalid role.'}), 400
    if role_key == 'admin' and session.get('role', '').strip().lower() != 'admin':
        return jsonify({'success': False, 'error': 'Only an administrator can assign the admin role.'}), 403

    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT sc.role, s.username
        FROM staff_clinics sc
        JOIN staff s ON s.id = sc.staff_id
        WHERE sc.staff_id = ? AND sc.clinic_id = ?
    ''', (staff_id, clinic_id))
    existing_link = cursor.fetchone()
    if not existing_link:
        return jsonify({'success': False, 'error': 'This staff member is not part of your clinic.'}), 400

    current_role_key, target_username = existing_link
    current_role_key = (current_role_key or '').strip().lower()

    if current_role_key == 'admin' and session.get('role', '').strip().lower() != 'admin':
        return jsonify({'success': False, 'error': 'Only an administrator can modify another administrator\'s account.'}), 403

    # The seeded default admin account is the account of last resort -- if
    # its role is ever changed away from admin (by anyone, including itself),
    # there's a real chance no one is left who can undo it, which is exactly
    # what happened before this fix. Its role is permanently locked once it
    # IS admin; other fields (name, username, password) can still be edited
    # normally, and if it's ever demoted by some other means, an admin can
    # still restore it back to 'admin' -- only moving AWAY from admin is
    # blocked, not back to it.
    if target_username == 'admin' and current_role_key == 'admin' and role_key != 'admin':
        return jsonify({'success': False, 'error': "The default System Administrator account's role can never be changed away from admin."}), 403
    
    try:
        if password:
            if len(password) < 8:
                return jsonify({'success': False, 'error': 'Password must be at least 8 characters long.'}), 400
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
        current_app.logger.exception("Unhandled error in request")
        return jsonify({'success': False, 'error': 'Something went wrong. Please try again.'}), 500

@bp.route('/api/staff/deactivate/<int:staff_id>', methods=['POST'])
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

@bp.route('/api/staff/reactivate/<int:staff_id>', methods=['POST'])
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
