import uuid
import datetime
from flask import Blueprint, request, session, jsonify
from db import get_db
from utils.security import require_permission
from utils.audit import log_audit
from utils.currency import get_clinic_currency, format_amount
from utils.data_helpers import get_current_clinic_id

bp = Blueprint('visit_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - VISITS
# ------------------------------------------------------------------
@bp.route('/api/visit/create', methods=['POST'])
@require_permission('visit.create')
def api_visit_create():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    patient_id = data.get('patient_id')
    diagnosis = data.get('diagnosis', '').strip()
    selected_items = data.get('items', [])

    if not patient_id:
        return jsonify({'success': False, 'error': 'Patient ID required'}), 400
    if not diagnosis:
        return jsonify({'success': False, 'error': 'Diagnosis is required'}), 400
    if not selected_items:
        return jsonify({'success': False, 'error': 'Select at least one item'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT patients.id, patients.name, appointments.id, appointments.status
        FROM patients
        JOIN appointments ON patients.id = appointments.patient_id
        WHERE patients.id = ? AND appointments.clinic_id = ?
          AND appointments.status IN ('Waiting', 'Pending', 'Returned to Doctor') 
        ORDER BY appointments.created_at DESC
        LIMIT 1
    ''', (patient_id, clinic_id))
    row = cursor.fetchone()

    if row is None:
        return jsonify({'success': False, 'error': 'Patient not in active queue'}), 400

    p_id, p_name, appointment_id, appointment_status = row

    cursor.execute(
        "SELECT id FROM visits WHERE appointment_id = ? AND status != 'Returned to Doctor'",
        (appointment_id,)
    )
    existing_visit = cursor.fetchone()
    if existing_visit:
        return jsonify({'success': False, 'error': f'A visit was already saved for {p_name}'}), 400

    today_str = datetime.date.today().isoformat()
    line_items = []
    total_fee = 0

    for item in selected_items:
        price_list_id = item.get('price_list_id')
        qty = item.get('qty', 0)

        if qty <= 0:
            return jsonify({'success': False, 'error': 'Quantity must be at least 1'}), 400

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
                WHERE is_active = 1 AND clinic_id = ?
                GROUP BY name_key, category
            ) AS stock
                ON stock.name_key = LOWER(TRIM(linked_item.item_name))
                AND stock.category = linked_item.category
            WHERE price_list.id = ? AND price_list.clinic_id = ? AND price_list.is_active = 1
        ''', (today_str, today_str, clinic_id, price_list_id, clinic_id))
        item_row = cursor.fetchone()

        if item_row is None:
            return jsonify({'success': False, 'error': 'Selected item no longer exists'}), 400

        (pl_id, pl_name, pl_type, pl_price, pl_pack_quantity, pl_inventory_id, usable_qty, expired_qty) = item_row

        has_stock_concept = pl_inventory_id is not None
        if has_stock_concept and usable_qty <= 0:
            return jsonify({'success': False, 'error': f'"{pl_name}" has no usable stock available'}), 400

        if has_stock_concept and qty > usable_qty:
            return jsonify({'success': False, 'error': f'"{pl_name}": only {usable_qty} unit(s) available, but {qty} were prescribed'}), 400

        pack_quantity = pl_pack_quantity if pl_pack_quantity and pl_pack_quantity > 0 else 1
        price_per_unit = pl_price / pack_quantity
        line_total = round(price_per_unit * qty)

        total_fee += line_total
        line_items.append((pl_id, pl_name, pl_type, round(price_per_unit), pl_inventory_id, qty, line_total))

    now = datetime.datetime.now().isoformat()

    cursor.execute('''
        INSERT INTO visits (uuid, clinic_id, patient_id, doctor_id, appointment_id,
                             visit_date, diagnosis, total_fee, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_id, patient_id, session.get('staff_id'), appointment_id, now, diagnosis, total_fee, 'Ready for Cashier', now))
    visit_id = cursor.lastrowid

    for (pl_id, pl_name, pl_type, price_per_unit, pl_inventory_id, qty, line_total) in line_items:
        cursor.execute('''
            INSERT INTO visit_items (uuid, visit_id, inventory_id, price_list_id, item_type, item_name,
                                      quantity, price_per_unit, total_line_price, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), visit_id, pl_inventory_id, pl_id, pl_type, pl_name, qty, price_per_unit, line_total, now))

    cursor.execute("UPDATE appointments SET status = 'In Progress', updated_at = ? WHERE id = ?", (now, appointment_id))

    conn.commit()
    log_audit('CREATE_VISIT', 'visits', visit_id, 
              old_value=None, new_value=f"Patient: {p_name}, Total: {format_amount(total_fee)}")
    return jsonify({'success': True, 'visit_id': visit_id})

@bp.route('/api/visit/prefill/<int:patient_id>', methods=['GET'])
@require_permission('visit.create')
def api_visit_prefill(patient_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT patients.id, patients.name, patients.sex, patients.date_of_birth,
               appointments.id, appointments.appointment_type, appointments.status
        FROM patients
        JOIN appointments ON patients.id = appointments.patient_id
        WHERE patients.id = ? AND appointments.clinic_id = ?
          AND appointments.status IN ('Waiting', 'Pending', 'Returned to Doctor') 
        ORDER BY appointments.created_at DESC
        LIMIT 1
    ''', (patient_id, clinic_id))
    row = cursor.fetchone()

    if row is None:
        return jsonify({'error': 'Patient not in active queue'}), 404

    (p_id, p_name, p_sex, p_dob, appointment_id, appointment_type, appointment_status) = row

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
            WHERE is_active = 1 AND clinic_id = ?
            GROUP BY name_key, category
        ) AS stock
            ON stock.name_key = LOWER(TRIM(linked_item.item_name))
            AND stock.category = linked_item.category
        WHERE price_list.is_active = 1
          AND price_list.clinic_id = ?
        ORDER BY price_list.item_type, price_list.item_name
    ''', (today_str, today_str, clinic_id, clinic_id))
    all_priced_items = cursor.fetchall()

    prefill_diagnosis = None
    prefill_return_reason = None
    prefill_items = []

    if appointment_status == 'Returned to Doctor':
        cursor.execute('''
            SELECT id, diagnosis, return_reason
            FROM visits
            WHERE appointment_id = ? AND status = 'Returned to Doctor'
            ORDER BY created_at DESC
            LIMIT 1
        ''', (appointment_id,))
        prev_visit = cursor.fetchone()

        if prev_visit:
            prev_visit_id, prefill_diagnosis, prefill_return_reason = prev_visit
            cursor.execute('''
                SELECT price_list_id, quantity
                FROM visit_items
                WHERE visit_id = ? AND price_list_id IS NOT NULL
            ''', (prev_visit_id,))
            prefill_items = [{'id': r[0], 'qty': r[1]} for r in cursor.fetchall()]

    currency = get_clinic_currency(clinic_id)

    return jsonify({
        'patient_id': p_id,
        'patient_name': p_name,
        'patient_sex': p_sex,
        'patient_dob': p_dob,
        'appointment_type': appointment_type,
        'appointment_status': appointment_status,
        'priced_items': [{
            'id': r[0],
            'type': r[1],
            'name': r[2],
            'price': r[3],
            'defaultQuantity': r[4],
            'usableQty': r[5],
            'expiredQty': r[6],
            'noStockConcept': r[7]
        } for r in all_priced_items],
        'prefill_diagnosis': prefill_diagnosis,
        'prefill_return_reason': prefill_return_reason,
        'prefill_items': prefill_items,
        'currency': currency
    })
