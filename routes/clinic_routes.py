import uuid
import datetime
from flask import Blueprint, request, session, jsonify
from db import get_db
from utils.security import require_permission
from utils.currency import get_all_currencies, get_clinic_currency
from utils.data_helpers import get_current_clinic_id

bp = Blueprint('clinic_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - CLINIC
# ------------------------------------------------------------------
@bp.route('/api/clinics', methods=['GET'])
@require_permission('clinic.view')
def api_clinics():
    return jsonify({
        'clinics': [{'id': c[0], 'name': c[1], 'role': c[2]} for c in session.get('user_clinics', [])]
    })

@bp.route('/api/clinics/select', methods=['POST'])
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

@bp.route('/api/clinics/setup', methods=['POST'])
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

@bp.route('/api/clinics/create', methods=['POST'])
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
    
@bp.route('/api/currencies', methods=['GET'])
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

@bp.route('/api/clinic/currency', methods=['GET'])
@require_permission('clinic.view')
def api_clinic_currency():
    """Get current clinic's currency"""
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    currency = get_clinic_currency(clinic_id)
    return jsonify(currency)

@bp.route('/api/clinic/currency', methods=['POST'])
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
