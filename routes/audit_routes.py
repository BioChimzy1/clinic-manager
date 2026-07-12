import sqlite3
from flask import Blueprint, jsonify
from db import get_db
from utils.security import require_permission
from utils.data_helpers import get_current_clinic_id

bp = Blueprint('audit_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - AUDIT
# ------------------------------------------------------------------
@bp.route('/api/audit', methods=['GET'])
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
