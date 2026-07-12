import datetime
from flask import Blueprint, jsonify
from db import get_db
from utils.security import require_permission
from utils.currency import get_clinic_currency
from utils.data_helpers import get_current_clinic_id, get_dashboard_data

bp = Blueprint('dashboard_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - DASHBOARD
# ------------------------------------------------------------------
@bp.route('/api/dashboard', methods=['GET'])
@require_permission('dashboard.view')
def api_dashboard():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    stats = get_dashboard_data(clinic_id)
    stats['fetched_at'] = datetime.datetime.now().isoformat()
    stats['currency'] = get_clinic_currency(clinic_id)
    return jsonify(stats)
    
    
@bp.route('/api/patients', methods=['GET'])
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
