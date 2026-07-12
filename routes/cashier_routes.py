import uuid
import math
import datetime
from collections import defaultdict
from flask import Blueprint, request, jsonify, current_app
from db import get_db
from utils.security import require_permission
from utils.audit import log_audit
from utils.currency import get_clinic_currency
from utils.data_helpers import get_current_clinic_id

bp = Blueprint('cashier_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - CASHIER
# ------------------------------------------------------------------
@bp.route('/api/cashier/list', methods=['GET'])
@require_permission('cashier.view')
def api_cashier_list():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            visits.id,
            visits.uuid,
            visits.visit_date,
            visits.diagnosis,
            visits.total_fee,
            visits.amount_paid,
            visits.loan_witness,
            visits.status,
            visits.discount_amount,
            visits.discount_reason,
            visits.loan_due_date,
            patients.name AS patient_name,
            patients.id AS patient_id
        FROM visits
        JOIN patients ON visits.patient_id = patients.id
        WHERE visits.clinic_id = ?
          AND visits.status = 'Ready for Cashier'
          AND (visits.is_retail IS NULL OR visits.is_retail = 0)
        ORDER BY visits.created_at ASC
    ''', (clinic_id,))
    cashier_list = cursor.fetchall()

    visit_ids = [r[0] for r in cashier_list]
    items_by_visit = {}
    if visit_ids:
        qmarks = ','.join(['?'] * len(visit_ids))
        cursor.execute(f'''
            SELECT visit_id, item_name, item_type, quantity
            FROM visit_items
            WHERE visit_id IN ({qmarks})
        ''', visit_ids)
        from collections import defaultdict
        items_by_visit = defaultdict(list)
        for v_id, item_name, item_type, quantity in cursor.fetchall():
            items_by_visit[v_id].append({
                'name': item_name,
                'category': item_type,
                'quantity': quantity
            })

    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'cashier_list': [{
            'id': r[0],
            'uuid': r[1],
            'visit_date': r[2],
            'diagnosis': r[3],
            'total_fee': r[4],
            'amount_paid': r[5],
            'loan_witness': r[6],
            'status': r[7],
            'discount_amount': r[8],
            'discount_reason': r[9],
            'loan_due_date': r[10],
            'patient_name': r[11],
            'patient_id': r[12],
            'items_sold': items_by_visit.get(r[0], [])
        } for r in cashier_list],
        'currency': currency
    })

@bp.route('/api/cashier/view/<int:visit_id>', methods=['GET'])
@require_permission('cashier.view')
def api_cashier_view(visit_id):
    conn = get_db()
    cursor = conn.cursor()
    
    clinic_id = get_current_clinic_id()
    cursor.execute('''
        SELECT visits.total_fee, visits.amount_paid, visits.diagnosis, visits.status,
               patients.name AS patient_name
        FROM visits
        LEFT JOIN patients ON visits.patient_id = patients.id
        WHERE visits.id = ? AND visits.clinic_id = ?
    ''', (visit_id, clinic_id))
    visit = cursor.fetchone()
    
    if not visit:
        return jsonify({'error': 'Visit not found'}), 404

    cursor.execute('''
        SELECT item_type, item_name, quantity, price_per_unit, total_line_price
        FROM visit_items
        WHERE visit_id = ?
    ''', (visit_id,))
    items = cursor.fetchall()
    
    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'patient': visit[4] or '🏪 Retail Sale',
        'diagnosis': visit[2],
        'total': visit[0],
        'paid': visit[1],
        'status': visit[3],
        'items': [{'type': i[0], 'name': i[1], 'qty': i[2], 'unit_price': i[3], 'total': i[4]} for i in items],
        'currency': currency
    })

@bp.route('/api/cashier/process/<int:visit_id>', methods=['POST'])
@require_permission('payment.process_full')
def api_cashier_process(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    
    data = request.get_json()
    payment_mode = data.get('payment_mode')
    
    if payment_mode not in ['full', 'loan']:
        return jsonify({'success': False, 'error': 'Invalid payment mode.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT total_fee, amount_paid, status, patient_id, appointment_id, loan_witness
            FROM visits WHERE id = ? AND clinic_id = ?
        ''', (visit_id, clinic_id))
        visit_row = cursor.fetchone()
        
        if not visit_row:
            return jsonify({'success': False, 'error': 'Visit not found.'}), 404
        
        total_fee, current_paid, status, patient_id, appointment_id, existing_witness = visit_row
        
        if status not in ['Ready for Cashier', 'Loan Active']:
            return jsonify({'success': False, 'error': 'Visit is not in a payable state.'}), 400
        
        now = datetime.datetime.now().isoformat()

        cursor.execute('''
            UPDATE visits SET status = 'Processing', updated_at = ?
            WHERE id = ? AND status IN ('Ready for Cashier', 'Loan Active')
        ''', (now, visit_id))

        if cursor.rowcount == 0:
            return jsonify({'success': False, 'error': 'This payment was already processed.'}), 400

        rounded_total = data.get('rounded_total')
        round_to = data.get('round_to') or 0

        # Get clinic currency for rounding
        currency = get_clinic_currency(clinic_id)
        subunit_ratio = currency['subunit_ratio']

        if rounded_total is not None and round_to:
            try:
                rounded_total = int(rounded_total)
                round_to_subunit = int(round_to) * subunit_ratio
            except (TypeError, ValueError):
                conn.rollback()
                return jsonify({'success': False, 'error': 'Invalid rounding value.'}), 400

            if round_to_subunit <= 0 or round_to_subunit % (subunit_ratio * 100) != 0:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Invalid rounding step.'}), 400

            expected_rounded = math.floor(total_fee / round_to_subunit + 0.5) * round_to_subunit
            if rounded_total != expected_rounded:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Rounded total does not match the selected rounding step.'}), 400

            cursor.execute('''
                UPDATE visits SET total_fee = ?, updated_at = ?
                WHERE id = ?
            ''', (rounded_total, now, visit_id))
            total_fee = rounded_total
        
        payment_channel = data.get('payment_channel', 'Cash')
        payment_reference = data.get('payment_reference')
        medical_aid_company = data.get('medical_aid_company')
        
        cursor.execute('''
            UPDATE visits
            SET payment_channel = ?, payment_reference = ?, medical_aid_company = ?
            WHERE id = ?
        ''', (payment_channel, payment_reference, medical_aid_company, visit_id))
        
        if payment_mode == 'full':
            discount_amount = int(data.get('discount_amount') or 0)
            discount_reason = (data.get('discount_reason') or '').strip()

            if discount_amount < 0:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Invalid discount amount.'}), 400

            if discount_amount > total_fee:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Discount cannot exceed the total fee.'}), 400

            if discount_amount > 0 and not discount_reason:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Discount reason is required.'}), 400

            amount_due = total_fee - discount_amount

            if current_paid > amount_due:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Amount already paid exceeds the discounted total. Adjust the discount first.'}), 400

            collected_now = amount_due - current_paid

            cursor.execute('''
                UPDATE visits
                SET amount_paid = ?,
                    discount_amount = ?,
                    discount_reason = ?,
                    status = 'Paid',
                    updated_at = ?
                WHERE id = ?
            ''', (amount_due, discount_amount, discount_reason or None, now, visit_id))

            if current_paid > 0 and collected_now > 0:
                cursor.execute('''
                    INSERT INTO loan_payments (uuid, visit_id, payment_date, amount, created_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (str(uuid.uuid4()), visit_id, now, collected_now, now))
            
        elif payment_mode == 'loan':
            amount_paid_now = int(data.get('amount_paid') or 0)
            witness_id = data.get('witness_id')
            loan_due_date = data.get('loan_due_date')
            discount_amount = int(data.get('discount_amount') or 0)
            discount_reason = (data.get('discount_reason') or '').strip()

            if discount_amount < 0:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Invalid discount amount.'}), 400

            if discount_amount > total_fee:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Discount cannot exceed the total fee.'}), 400

            if discount_amount > 0 and not discount_reason:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Discount reason is required.'}), 400

            effective_total = total_fee - discount_amount

            if amount_paid_now is None or amount_paid_now < 0:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Invalid payment amount.'}), 400

            if current_paid > effective_total:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Amount already paid exceeds the discounted total. Adjust the discount first.'}), 400

            new_total_paid = current_paid + amount_paid_now

            if new_total_paid >= effective_total:
                conn.rollback()
                return jsonify({'success': False, 'error': 'This payment would cover the full remaining balance. Use Full Payment instead.'}), 400
            
            if not witness_id:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Witness is required for loans.'}), 400
            
            if loan_due_date and loan_due_date.strip():
                try:
                    datetime.datetime.strptime(loan_due_date, '%Y-%m-%d')
                except ValueError:
                    conn.rollback()
                    return jsonify({'success': False, 'error': 'Invalid due date format. Use YYYY-MM-DD.'}), 400
            else:
                loan_due_date = None

            cursor.execute('''
                SELECT staff.full_name
                FROM staff
                JOIN staff_clinics ON staff.id = staff_clinics.staff_id
                WHERE staff.id = ? AND staff.is_active = 1 AND staff_clinics.clinic_id = ?
            ''', (witness_id, clinic_id))
            witness_row = cursor.fetchone()
            witness_name = witness_row[0] if witness_row else 'Unknown Staff'
            
            cursor.execute('''
                UPDATE visits
                SET amount_paid = ?,
                    loan_witness = ?,
                    loan_due_date = ?,
                    discount_amount = ?,
                    discount_reason = ?,
                    status = 'Loan Active',
                    updated_at = ?
                WHERE id = ?
            ''', (new_total_paid, witness_name, loan_due_date, discount_amount, discount_reason or None, now, visit_id))
            
            cursor.execute('''
                INSERT INTO loan_payments (uuid, visit_id, payment_date, amount, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (str(uuid.uuid4()), visit_id, now, amount_paid_now, now))
        
        # INVENTORY DEDUCTION: FEFO (First-Expired, First-Out)
        # Fixed: uses inventory_id instead of text matching
        cursor.execute('''
            SELECT inventory_id, quantity
            FROM visit_items
            WHERE visit_items.visit_id = ?
              AND visit_items.inventory_id IS NOT NULL
        ''', (visit_id,))
        items_to_deduct = cursor.fetchall()
        
        for inventory_id, qty_to_deduct in items_to_deduct:
            # price_list.inventory_id points at one specific batch row, but
            # a given item_name+category can have several batches (different
            # expiry dates) in this clinic. Look up the item's identity first,
            # then pull ALL of that clinic's batches for it -- this matches
            # the availability check in api_visit_create, which also sums
            # quantity across every batch sharing this item_name+category.
            cursor.execute('''
                SELECT item_name, category
                FROM inventory
                WHERE id = ? AND clinic_id = ? AND is_active = 1
            ''', (inventory_id, clinic_id))
            linked_item = cursor.fetchone()

            if linked_item is None:
                print(f"WARNING: inventory_id {inventory_id} not found in clinic {clinic_id}; skipped deduction")
                continue

            linked_name, linked_category = linked_item

            cursor.execute('''
                SELECT id, quantity, expiry_date
                FROM inventory
                WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?))
                  AND category = ?
                  AND clinic_id = ?
                  AND is_active = 1
                  AND quantity > 0
                ORDER BY expiry_date ASC
            ''', (linked_name, linked_category, clinic_id))
            batches = cursor.fetchall()
            
            remaining = qty_to_deduct
            
            for batch_id, batch_qty, expiry in batches:
                if remaining <= 0:
                    break
                
                if batch_qty > 0:
                    deduct = min(remaining, batch_qty)
                    new_qty = batch_qty - deduct
                    cursor.execute('''
                        UPDATE inventory
                        SET quantity = ?, updated_at = ?
                        WHERE id = ?
                    ''', (new_qty, now, batch_id))
                    remaining -= deduct
            
            if remaining > 0:
                print(f"WARNING: Could not fully deduct inventory_id {inventory_id} ({linked_name}), still {remaining} units short across all batches")
        
        conn.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        conn.rollback()
        current_app.logger.exception("Unhandled error in request")
        return jsonify({'success': False, 'error': 'Something went wrong. Please try again.'}), 500

@bp.route('/api/cashier/send_back/<int:visit_id>', methods=['POST'])
@require_permission('cashier.send_back')
def api_cashier_send_back(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    reason = (data.get('reason') or '').strip() or None

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT visits.status, visits.appointment_id, visits.is_retail, patients.name
        FROM visits
        LEFT JOIN patients ON visits.patient_id = patients.id
        WHERE visits.id = ? AND visits.clinic_id = ?
    ''', (visit_id, clinic_id))
    row = cursor.fetchone()

    if row is None:
        return jsonify({'success': False, 'error': 'Visit not found.'}), 404

    status, appointment_id, is_retail, patient_name = row

    if status != 'Ready for Cashier':
        return jsonify({'success': False, 'error': f'This visit is "{status}" and can no longer be sent back (already paid or on a loan).'}), 400

    cursor.execute('''
        SELECT price_list_id, item_type, item_name, quantity, price_per_unit
        FROM visit_items
        WHERE visit_id = ?
    ''', (visit_id,))
    items = [
        {
            'price_list_id': r[0],
            'type': r[1],
            'name': r[2],
            'qty': r[3],
            'price_per_unit': r[4]
        }
        for r in cursor.fetchall()
    ]

    now = datetime.datetime.now().isoformat()

    if is_retail:
        cursor.execute('''
            UPDATE visits SET status = 'Cancelled', return_reason = ?, updated_at = ?
            WHERE id = ? AND status = 'Ready for Cashier'
        ''', (reason, now, visit_id))

        if cursor.rowcount == 0:
            return jsonify({'success': False, 'error': 'Could not send this sale back -- it may have just been paid.'}), 400

        conn.commit()
        log_audit('SEND_BACK_RETAIL', 'visits', visit_id,
                  old_value='Ready for Cashier',
                  new_value=f"Sent back to Retail cart. Reason: {reason or '(none given)'}")
        return jsonify({'success': True, 'returned_to': 'retail', 'items': items})

    if appointment_id is None:
        return jsonify({'success': False, 'error': 'This visit has no linked appointment to send back to.'}), 400

    cursor.execute('''
        UPDATE visits SET status = 'Returned to Doctor', return_reason = ?, updated_at = ? WHERE id = ?
    ''', (reason, now, visit_id))

    cursor.execute('''
        UPDATE appointments SET status = 'Returned to Doctor', updated_at = ? WHERE id = ?
    ''', (now, appointment_id))

    conn.commit()
    log_audit('SEND_BACK_TO_DOCTOR', 'visits', visit_id,
              old_value='Ready for Cashier',
              new_value=f"Returned to Doctor. Patient: {patient_name}. Reason: {reason or '(none given)'}")
    return jsonify({'success': True, 'returned_to': 'doctor', 'items': items})
