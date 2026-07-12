import uuid
import datetime
from flask import Blueprint, request, session, jsonify
from roles_permissions import has_permission
from db import get_db
from utils.security import require_permission
from utils.data_helpers import get_current_clinic_id

bp = Blueprint('appointment_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - APPOINTMENTS
# ------------------------------------------------------------------
@bp.route('/api/appointments', methods=['GET'])
@require_permission('appointment.view')
def api_appointments():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    conn = get_db()
    cursor = conn.cursor()
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

    return jsonify({
        'appointments': [{
            'id': r[0],
            'date': r[1],
            'type': r[2],
            'status': r[3],
            'reason': r[4],
            'cancelled_reason': r[5],
            'check_in_time': r[6],
            'patient_id': r[7],
            'patient_name': r[8],
            'phone': r[9],
            'sex': r[10]
        } for r in appointment_list]
    })

@bp.route('/api/appointments/schedule', methods=['POST'])
@require_permission('appointment.schedule')
def api_appointment_schedule():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    
    data = request.get_json()
    patient_id = data.get('patient_id')
    appointment_date = data.get('appointment_date')
    reason = data.get('reason', 'Consultation')
    
    conn = get_db()
    cursor = conn.cursor()

    if not patient_id:
        name = data.get('new_name')
        dob = data.get('new_dob')
        sex = data.get('new_sex')
        phone = data.get('new_phone')
        location = data.get('new_location')
        
        if not name:
            return jsonify({'success': False, 'error': 'Name is required for new patient'}), 400
        
        cursor.execute('''
            INSERT INTO patients (uuid, clinic_id, name, date_of_birth, sex, phone, location, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), clinic_id, name, dob, sex, phone, location, datetime.datetime.now().isoformat()))
        patient_id = cursor.lastrowid
    
    cursor.execute('''
        INSERT INTO appointments (uuid, clinic_id, patient_id, doctor_id, appointment_date, appointment_type, reason, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_id, patient_id, session.get('staff_id'), appointment_date, 'Appointment', reason, 'Pending', datetime.datetime.now().isoformat()))
    
    conn.commit()
    return jsonify({'success': True})

@bp.route('/api/appointments/update/<int:appt_id>', methods=['POST'])
@require_permission('appointment.confirm')
def api_appointment_update(appt_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    
    data = request.get_json()
    action = data.get('action')

    action_permission = {
        'confirm': 'appointment.confirm',
        'cancel': 'appointment.cancel',
        'reschedule': 'appointment.reschedule',
        'missed': 'appointment.mark_missed',
        'check_in': 'appointment.check_in',
    }
    permission = action_permission.get(action)
    if permission is None:
        return jsonify({'success': False, 'error': 'Invalid action'}), 400
    if not has_permission(session.get('role', ''), permission):
        return jsonify({'success': False, 'error': 'Permission denied.'}), 403

    now = datetime.datetime.now().isoformat()

    conn = get_db()
    cursor = conn.cursor()

    # Confirm this appointment actually belongs to the current clinic before
    # touching it -- without this, any staff with appointment permissions
    # could confirm/cancel/reschedule/check-in ANY appointment system-wide
    # just by guessing appt_id.
    cursor.execute("SELECT id FROM appointments WHERE id = ? AND clinic_id = ?", (appt_id, clinic_id))
    if not cursor.fetchone():
        return jsonify({'success': False, 'error': 'Appointment not found.'}), 404

    if action == 'confirm':
        cursor.execute('''
            UPDATE appointments SET status = 'Scheduled', updated_at = ? WHERE id = ? AND clinic_id = ?
        ''', (now, appt_id, clinic_id))

    elif action == 'cancel':
        reason = data.get('reason', 'No reason provided')
        cursor.execute('''
            UPDATE appointments SET status = 'Cancelled', cancelled_reason = ?, updated_at = ? WHERE id = ? AND clinic_id = ?
        ''', (reason, now, appt_id, clinic_id))

    elif action == 'reschedule':
        new_date = data.get('new_date')
        cursor.execute('''
            UPDATE appointments SET appointment_date = ?, status = 'Pending', updated_at = ? WHERE id = ? AND clinic_id = ?
        ''', (new_date, now, appt_id, clinic_id))

    elif action == 'missed':
        cursor.execute('''
            UPDATE appointments SET status = 'Missed', updated_at = ? WHERE id = ? AND clinic_id = ?
        ''', (now, appt_id, clinic_id))

    elif action == 'check_in':
        cursor.execute('''
            UPDATE appointments SET status = 'Waiting', check_in_time = ?, updated_at = ? WHERE id = ? AND clinic_id = ?
        ''', (now, now, appt_id, clinic_id))

    else:
        return jsonify({'success': False, 'error': 'Invalid action'}), 400

    conn.commit()
    return jsonify({'success': True})

@bp.route('/api/appointments/review/<int:patient_id>', methods=['GET'])
@require_permission('appointment.review')
def api_appointment_review(patient_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    conn = get_db()
    cursor = conn.cursor()

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
        return jsonify({'error': 'This patient is not currently pending confirmation.'}), 404

    return jsonify({
        'patient_id': row[0],
        'patient_name': row[1],
        'patient_sex': row[2],
        'patient_dob': row[3],
        'patient_phone': row[4],
        'appointment_id': row[5],
        'appointment_date': row[6],
        'appointment_reason': row[7],
        'appointment_status': row[8]
    })

@bp.route('/api/appointments/review/<int:patient_id>', methods=['POST'])
@require_permission('appointment.review')
def api_appointment_review_action(patient_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    action = data.get('action')
    now = datetime.datetime.now().isoformat()

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT appointments.id
        FROM patients
        JOIN appointments ON patients.id = appointments.patient_id
        WHERE patients.id = ? AND appointments.status = 'Pending'
          AND appointments.clinic_id = ?
        ORDER BY appointments.created_at DESC
        LIMIT 1
    ''', (patient_id, clinic_id))
    row = cursor.fetchone()

    if row is None:
        return jsonify({'success': False, 'error': 'This patient is not currently pending confirmation.'}), 400

    appt_id = row[0]

    if action == 'confirm':
        cursor.execute('''
            UPDATE appointments SET status = 'Scheduled', updated_at = ? WHERE id = ?
        ''', (now, appt_id))
    elif action == 'cancel':
        reason = data.get('reason', 'No reason provided')
        cursor.execute('''
            UPDATE appointments SET status = 'Cancelled', cancelled_reason = ?, updated_at = ? WHERE id = ?
        ''', (reason, now, appt_id))
    elif action == 'reschedule':
        new_date = data.get('new_date')
        if new_date:
            cursor.execute('''
                UPDATE appointments SET appointment_date = ?, status = 'Pending', updated_at = ? WHERE id = ?
            ''', (new_date, now, appt_id))
        else:
            return jsonify({'success': False, 'error': 'Please provide a new date for rescheduling.'}), 400
    else:
        return jsonify({'success': False, 'error': 'Invalid action.'}), 400

    conn.commit()
    return jsonify({'success': True})
