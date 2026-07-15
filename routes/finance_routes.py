import uuid
import datetime
from flask import Blueprint, request, jsonify
from db import get_db
from utils.security import require_permission
from utils.currency import get_clinic_currency
from utils.data_helpers import get_current_clinic_id, get_period_dates, build_grouped_transactions

bp = Blueprint('finance_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - FINANCE
# ------------------------------------------------------------------
@bp.route('/api/finance/stats', methods=['GET'])
@require_permission('finance.view')
def api_finance_stats():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    
    period = request.args.get('period', 'today')
    start_date, end_date, period = get_period_dates(period)
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT SUM(amount_paid) FROM visits 
        WHERE status = 'Paid'
        AND clinic_id = ?
        AND id NOT IN (SELECT DISTINCT visit_id FROM loan_payments)
        AND updated_at >= ? AND updated_at <= ?
    ''', (clinic_id, start_date, end_date))
    total_cash_direct = cursor.fetchone()[0] or 0

    cursor.execute('''
        SELECT SUM(loan_payments.amount)
        FROM loan_payments
        JOIN visits ON loan_payments.visit_id = visits.id
        WHERE visits.clinic_id = ?
        AND loan_payments.payment_date >= ? AND loan_payments.payment_date <= ?
    ''', (clinic_id, start_date, end_date))
    total_cash_from_loans = cursor.fetchone()[0] or 0

    total_cash = total_cash_direct + total_cash_from_loans
    
    cursor.execute('''
        SELECT SUM(total_fee - COALESCE(discount_amount, 0) - amount_paid) FROM visits 
        WHERE status = 'Loan Active'
          AND clinic_id = ?
    ''', (clinic_id,))
    outstanding_loans = cursor.fetchone()[0] or 0
    
    cursor.execute('''
        SELECT SUM(discount_amount) FROM visits 
        WHERE discount_amount > 0
        AND clinic_id = ?
        AND status NOT IN ('Returned to Doctor', 'Cancelled')
        AND updated_at >= ? AND updated_at <= ?
    ''', (clinic_id, start_date, end_date))
    total_discounts = cursor.fetchone()[0] or 0
    
    net_revenue = total_cash
    
    cursor.execute('''
        SELECT SUM(amount) FROM expenses 
        WHERE clinic_id = ?
        AND expense_date >= ? AND expense_date <= ?
    ''', (clinic_id, start_date[:10], end_date[:10]))
    total_expenses = cursor.fetchone()[0] or 0
    
    net_profit = net_revenue - total_expenses
    
    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'total_cash': total_cash,
        'outstanding_loans': outstanding_loans,
        'total_discounts': total_discounts,
        'net_revenue': net_revenue,
        'total_expenses': total_expenses,
        'net_profit': net_profit,
        'period': period,
        'currency': currency
    })

@bp.route('/api/finance/transactions', methods=['GET'])
@require_permission('finance.view_transactions')
def api_finance_transactions():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    period = request.args.get('period', 'today')
    try:
        offset = int(request.args.get('offset', 0))
    except ValueError:
        offset = 0

    start_date, end_date, period = get_period_dates(period)
    today_str = datetime.date.today().isoformat()

    conn = get_db()
    cursor = conn.cursor()
    grouped_transactions, has_more = build_grouped_transactions(
        cursor, clinic_id, start_date, end_date, today_str, offset=offset, limit=20
    )

    currency = get_clinic_currency(clinic_id)

    return jsonify({
        'transactions': grouped_transactions,
        'has_more': has_more,
        'next_offset': offset + 20,
        'currency': currency
    })

@bp.route('/api/finance/expenses', methods=['GET'])
@require_permission('finance.view')
def api_finance_expenses():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    
    period = request.args.get('period', 'today')
    start_date, end_date, period = get_period_dates(period)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, expense_date, category, description, amount
        FROM expenses
        WHERE clinic_id = ?
        AND expense_date >= ? AND expense_date <= ?
        ORDER BY expense_date DESC
        LIMIT 50
    ''', (clinic_id, start_date[:10], end_date[:10]))
    expenses = cursor.fetchall()
    
    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'expenses': [{'id': r[0], 'date': r[1], 'category': r[2], 'description': r[3], 'amount': r[4]} for r in expenses],
        'currency': currency
    })

@bp.route('/api/finance/expenses/add', methods=['POST'])
@require_permission('finance.add_expense')
def api_finance_add_expense():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    
    data = request.get_json()
    expense_date = data.get('expense_date')
    category = data.get('category', 'Other')
    description = data.get('description', '').strip()
    try:
        amount = int(float(data.get('amount', 0)) * 100)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid expense amount.'}), 400
    
    if not expense_date or amount <= 0:
        return jsonify({'success': False, 'error': 'Please fill in all required fields correctly.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO expenses (uuid, clinic_id, expense_date, category, description, amount, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_id, expense_date, category, description, amount, datetime.datetime.now().isoformat()))
    conn.commit()
    return jsonify({'success': True})

@bp.route('/api/finance/expenses/delete/<int:expense_id>', methods=['DELETE'])
@require_permission('finance.delete_expense')
def api_finance_delete_expense(expense_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM expenses WHERE id = ? AND clinic_id = ?", (expense_id, clinic_id))
    conn.commit()
    return jsonify({'success': True})