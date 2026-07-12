import datetime
from flask import session
from db import get_db
from utils.data_helpers import get_current_clinic_id

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
