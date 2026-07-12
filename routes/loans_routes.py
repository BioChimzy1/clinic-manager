import uuid
import datetime
from flask import Blueprint, request, jsonify, current_app
from db import get_db
from utils.security import require_permission
from utils.currency import get_clinic_currency
from utils.data_helpers import get_current_clinic_id, get_loans_data

bp = Blueprint('loans_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - LOANS
# ------------------------------------------------------------------
@bp.route('/api/loans', methods=['GET'])
@require_permission('loan.view_list')
def api_loans():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    loan_list = get_loans_data(clinic_id)
    today_str = datetime.date.today().isoformat()

    loans = []
    for row in loan_list:
        due_date = row[4]
        loans.append({
            'id': row[0],
            'total_fee': row[1],
            'amount_paid': row[2],
            'balance': row[1] - row[2],
            'witness': row[3],
            'due_date': due_date,
            'is_overdue': bool(due_date and due_date < today_str),
            'is_retail': row[6] == 1,
            'patient_name': row[7],
        })

    currency = get_clinic_currency(clinic_id)

    return jsonify({
        'loans': loans,
        'fetched_at': datetime.datetime.now().isoformat(),
        'currency': currency
    })

@bp.route('/api/loans/details/<int:visit_id>', methods=['GET'])
@require_permission('loan.view')
def api_loan_details(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            visits.id,
            visits.total_fee,
            visits.amount_paid,
            visits.loan_witness,
            visits.loan_due_date,
            visits.status,
            patients.name AS patient_name,
            patients.id AS patient_id
        FROM visits
        LEFT JOIN patients ON visits.patient_id = patients.id
        WHERE visits.id = ? AND visits.clinic_id = ?
    ''', (visit_id, clinic_id))
    visit = cursor.fetchone()
    
    if not visit:
        return jsonify({'error': 'Visit not found'}), 404
    
    cursor.execute('''
        SELECT payment_date, amount
        FROM loan_payments
        WHERE visit_id = ?
        ORDER BY payment_date ASC
    ''', (visit_id,))
    payments = cursor.fetchall()
    
    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'visit': {
            'id': visit[0],
            'total_fee': visit[1],
            'amount_paid': visit[2],
            'loan_witness': visit[3],
            'loan_due_date': visit[4],
            'status': visit[5],
            'patient_name': visit[6],
            'patient_id': visit[7]
        },
        'payments': [{'date': p[0], 'amount': p[1]} for p in payments],
        'currency': currency
    })

@bp.route('/api/loans/pay/<int:visit_id>', methods=['POST'])
@require_permission('loan.record_payment')
def api_loan_pay(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    
    data = request.get_json()
    amount = data.get('amount')
    
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid payment amount.'}), 400
    
    if amount <= 0:
        return jsonify({'success': False, 'error': 'Amount must be greater than 0.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT total_fee, amount_paid, status, discount_amount
            FROM visits WHERE id = ? AND clinic_id = ?
        ''', (visit_id, clinic_id))
        visit = cursor.fetchone()
        
        if not visit:
            return jsonify({'success': False, 'error': 'Visit not found.'}), 404
        
        total_fee, current_paid, status, discount_amount = visit
        effective_total = total_fee - (discount_amount or 0)
        
        if status != 'Loan Active':
            return jsonify({'success': False, 'error': 'This visit is not an active loan.'}), 400
        
        new_total_paid = current_paid + amount
        
        if new_total_paid > effective_total:
            return jsonify({'success': False, 'error': 'Payment exceeds remaining loan balance.'}), 400
        
        now = datetime.datetime.now().isoformat()
        
        cursor.execute('''
            INSERT INTO loan_payments (uuid, visit_id, payment_date, amount, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), visit_id, now, amount, now))
        
        cursor.execute('''
            UPDATE visits
            SET amount_paid = ?,
                updated_at = ?
            WHERE id = ?
        ''', (new_total_paid, now, visit_id))
        
        if new_total_paid >= effective_total:
            cursor.execute('''
                UPDATE visits
                SET status = 'Paid',
                    loan_witness = NULL,
                    loan_due_date = NULL,
                    updated_at = ?
                WHERE id = ?
            ''', (now, visit_id))
        
        conn.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        conn.rollback()
        current_app.logger.exception("Unhandled error in request")
        return jsonify({'success': False, 'error': 'Something went wrong. Please try again.'}), 500
