from flask import Blueprint, request, session, jsonify
from werkzeug.security import check_password_hash
from db import get_db
from utils.security import _login_rate_limited, _record_login_attempt, _login_attempts

bp = Blueprint('auth_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - AUTH
# ------------------------------------------------------------------
@bp.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    limiter_key = f"{request.remote_addr}:{username}"
    if _login_rate_limited(limiter_key):
        return jsonify({'success': False, 'error': 'Too many login attempts. Please wait a minute and try again.'}), 429

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, role, password_hash FROM staff WHERE username = ? AND is_active = 1", (username,))
    user = cursor.fetchone()
    
    if user and check_password_hash(user[2], password):
        _login_attempts.pop(limiter_key, None)
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
    
    _record_login_attempt(limiter_key)
    return jsonify({'success': False, 'error': 'Invalid username or password'}), 401

@bp.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'success': True})

@bp.route('/api/verify', methods=['GET'])
def api_verify():
    if 'staff_id' not in session:
        return jsonify({'success': False}), 401
    
    # Fetch the is_developer flag from the database
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_developer FROM staff WHERE id = ?", (session['staff_id'],))
    row = cursor.fetchone()
    is_developer = bool(row[0]) if row else False
    
    return jsonify({
        'success': True,
        'staff_id': session['staff_id'],
        'role': session['role'],
        'clinic_id': session.get('clinic_id'),
        'clinic_name': session.get('clinic_name'),
        'is_developer': is_developer  # <--- ADD THIS
    })