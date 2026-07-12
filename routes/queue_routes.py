import uuid
import datetime
from flask import Blueprint, request, session, jsonify
from db import get_db
from utils.security import require_permission
from utils.audit import log_audit
from utils.data_helpers import get_current_clinic_id, get_queue_data

bp = Blueprint('queue_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - QUEUE
# ------------------------------------------------------------------
@bp.route('/api/queue', methods=['GET'])
@require_permission('queue.view')
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

@bp.route('/api/queue/register', methods=['POST'])
@require_permission('queue.register_patient')
def api_queue_register():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'error': 'invalid_json'}), 400

    client_uuid = data.get('client_uuid')
    name = data.get('name')
    if not client_uuid or not name:
        return jsonify({'error': 'missing_fields'}), 400

    date_of_birth = data.get('date_of_birth')
    sex = data.get('sex')
    phone = data.get('phone')
    location = data.get('location')
    appointment_type = data.get('appointment_type', 'Walk-In')

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('SELECT id FROM patients WHERE uuid = ?', (client_uuid,))
    existing = cursor.fetchone()
    if existing:
        patient_id = existing[0]
        cursor.execute('''
            SELECT status FROM appointments WHERE patient_id = ? ORDER BY id DESC LIMIT 1
        ''', (patient_id,))
        row = cursor.fetchone()
        status = row[0] if row else 'Waiting'
        return jsonify({
            'status': 'already_processed',
            'patient_id': patient_id,
            'queue_status': status
        }), 200

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

    return jsonify({
        'status': 'processed',
        'patient_id': patient_id,
        'queue_status': queue_status
    }), 201
