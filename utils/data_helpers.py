import datetime
from collections import defaultdict
from flask import session
from db import get_db

def get_current_clinic_id():
    cached = session.get('clinic_id')
    if cached:
        return cached
    staff_id = session.get('staff_id')
    if not staff_id:
        return None
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT clinic_id FROM staff_clinics WHERE staff_id = ? LIMIT 1', (staff_id,))
    row = cursor.fetchone()
    clinic_id = row[0] if row else None
    if clinic_id:
        session['clinic_id'] = clinic_id
    return clinic_id

def get_price_list_data(clinic_id):
    cursor = get_db().cursor()
    today_str = datetime.date.today().isoformat()

    cursor.execute('''
        SELECT price_list.id, price_list.item_type, price_list.item_name,
               price_list.price, price_list.quantity,
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
        ORDER BY
            CASE
                WHEN price_list.inventory_id IS NULL THEN 0
                WHEN COALESCE(stock.usable_qty, 0) > 0 THEN 0
                WHEN COALESCE(stock.expired_qty, 0) > 0 THEN 1
                ELSE 2
            END,
            price_list.item_name
    ''', (today_str, today_str, clinic_id, clinic_id))
    rows = cursor.fetchall()
    return rows

def get_inventory_data(clinic_id):
    cursor = get_db().cursor()
    cursor.execute("""
        SELECT id, category, item_name, quantity, min_alert_level, expiry_date
        FROM inventory
        WHERE clinic_id = ? AND is_active = 1
        ORDER BY expiry_date ASC
    """, (clinic_id,))
    rows = cursor.fetchall()
    return rows

def get_queue_data(clinic_id):
    cursor = get_db().cursor()
    cursor.execute('''
        SELECT patients.id, patients.name, patients.sex, patients.phone,
               appointments.appointment_type, appointments.status
        FROM patients
        JOIN appointments ON patients.id = appointments.patient_id
        WHERE appointments.status IN ('Waiting', 'Pending', 'Returned to Doctor')
          AND appointments.clinic_id = ?
        ORDER BY
            CASE WHEN appointments.status = 'Returned to Doctor' THEN 0 ELSE 1 END,
            appointments.created_at ASC
    ''', (clinic_id,))
    queue_list = cursor.fetchall()
    return queue_list

def get_loans_data(clinic_id):
    cursor = get_db().cursor()
    cursor.execute('''
        SELECT
            visits.id,
            visits.total_fee,
            visits.amount_paid,
            visits.loan_witness,
            visits.loan_due_date,
            visits.created_at,
            visits.is_retail,
            patients.name AS patient_name
        FROM visits
        LEFT JOIN patients ON visits.patient_id = patients.id
        WHERE visits.clinic_id = ?
          AND visits.status = 'Loan Active'
        ORDER BY visits.loan_due_date IS NULL, visits.loan_due_date ASC, visits.created_at ASC
    ''', (clinic_id,))
    loan_list = cursor.fetchall()
    return loan_list

def get_dashboard_data(clinic_id):
    cursor = get_db().cursor()

    cursor.execute("SELECT COUNT(*) FROM appointments WHERE clinic_id = ? AND status = 'Waiting'", (clinic_id,))
    waiting_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM appointments WHERE clinic_id = ? AND status = 'Pending'", (clinic_id,))
    pending_count = cursor.fetchone()[0]

    total_queue_count = waiting_count + pending_count

    today = datetime.date.today()
    today_start = today.isoformat() + 'T00:00:00'
    today_end = today.isoformat() + 'T23:59:59'

    cursor.execute('''
        SELECT COUNT(DISTINCT unique_id) FROM (
            SELECT id AS unique_id
            FROM visits
            WHERE clinic_id = ? 
            AND visit_date >= ? AND visit_date <= ?
            AND (is_retail IS NULL OR is_retail = 0)
            
            UNION
            
            SELECT id AS unique_id
            FROM visits
            WHERE clinic_id = ? 
            AND visit_date >= ? AND visit_date <= ?
            AND is_retail = 1
            
            UNION
            
            SELECT loan_payments.visit_id AS unique_id
            FROM loan_payments
            JOIN visits ON loan_payments.visit_id = visits.id
            WHERE visits.clinic_id = ? 
            AND loan_payments.payment_date >= ? AND loan_payments.payment_date <= ?
            AND visits.visit_date < ?
        )
    ''', (clinic_id, today_start, today_end, 
          clinic_id, today_start, today_end, 
          clinic_id, today_start, today_end, today_start))

    seen_today_count = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM inventory WHERE clinic_id = ? AND is_active = 1", (clinic_id,))
    total_items_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM inventory WHERE clinic_id = ? AND is_active = 1 AND quantity <= min_alert_level", (clinic_id,))
    low_stock_count = cursor.fetchone()[0]

    cutoff = (today + datetime.timedelta(days=14)).isoformat()
    cursor.execute(
        "SELECT COUNT(*) FROM inventory WHERE clinic_id = ? AND is_active = 1 AND expiry_date IS NOT NULL AND expiry_date <= ? AND expiry_date >= ?",
        (clinic_id, cutoff, today.isoformat())
    )
    expiring_soon_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM price_list WHERE clinic_id = ? AND is_active = 1", (clinic_id,))
    priced_items_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM visits WHERE clinic_id = ? AND status = 'Ready for Cashier'", (clinic_id,))
    cashier_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM visits WHERE clinic_id = ? AND status = 'Loan Active'", (clinic_id,))
    loans_count = cursor.fetchone()[0]

    cursor.execute('''
        SELECT SUM(amount_paid) FROM visits 
        WHERE clinic_id = ?
        AND status = 'Paid' 
        AND id NOT IN (SELECT DISTINCT visit_id FROM loan_payments)
        AND updated_at >= ? AND updated_at <= ?
    ''', (clinic_id, today_start, today_end))
    today_cash_direct = cursor.fetchone()[0] or 0

    cursor.execute('''
        SELECT SUM(loan_payments.amount) FROM loan_payments
        JOIN visits ON loan_payments.visit_id = visits.id
        WHERE visits.clinic_id = ?
        AND loan_payments.payment_date >= ? AND loan_payments.payment_date <= ?
    ''', (clinic_id, today_start, today_end))
    today_cash_from_loans = cursor.fetchone()[0] or 0

    today_cash_collected = today_cash_direct + today_cash_from_loans

    cursor.execute("SELECT COUNT(*) FROM appointments WHERE clinic_id = ? AND status IN ('Pending', 'Scheduled')", (clinic_id,))
    appointments_count = cursor.fetchone()[0]

    return {
        'waiting_count': waiting_count,
        'pending_count': pending_count,
        'total_queue_count': total_queue_count,
        'seen_today_count': seen_today_count,
        'total_items_count': total_items_count,
        'low_stock_count': low_stock_count,
        'expiring_soon_count': expiring_soon_count,
        'priced_items_count': priced_items_count,
        'cashier_count': cashier_count,
        'loans_count': loans_count,
        'today_cash_collected': today_cash_collected,
        'appointments_count': appointments_count,
    }

def get_period_dates(period):
    today = datetime.date.today()
    if period == 'today':
        start_date = today.isoformat() + 'T00:00:00'
        end_date = today.isoformat() + 'T23:59:59'
    elif period == 'week':
        start_date = (today - datetime.timedelta(days=7)).isoformat() + 'T00:00:00'
        end_date = today.isoformat() + 'T23:59:59'
    elif period == 'month':
        start_date = (today - datetime.timedelta(days=30)).isoformat() + 'T00:00:00'
        end_date = today.isoformat() + 'T23:59:59'
    else:
        start_date = (today - datetime.timedelta(days=7)).isoformat() + 'T00:00:00'
        end_date = today.isoformat() + 'T23:59:59'
        period = 'week'
    return start_date, end_date, period

def build_grouped_transactions(cursor, clinic_id, start_date, end_date, today_str, offset=0, limit=20):
    cursor.execute('''
        SELECT loan_payments.visit_id, DATE(loan_payments.payment_date) AS pay_date,
               SUM(loan_payments.amount) AS amount_sum,
               MAX(loan_payments.payment_date) AS pay_ts
        FROM loan_payments
        JOIN visits ON loan_payments.visit_id = visits.id
        WHERE loan_payments.payment_date >= ? AND loan_payments.payment_date <= ?
          AND visits.clinic_id = ?
        GROUP BY loan_payments.visit_id, DATE(loan_payments.payment_date)
    ''', (start_date, end_date, clinic_id))
    loan_grouped = cursor.fetchall()

    cursor.execute('''
        SELECT id AS visit_id, DATE(updated_at) AS pay_date, amount_paid AS amount_sum,
               updated_at AS pay_ts
        FROM visits
        WHERE status = 'Paid'
          AND clinic_id = ?
          AND id NOT IN (SELECT DISTINCT visit_id FROM loan_payments)
          AND updated_at >= ? AND updated_at <= ?
    ''', (clinic_id, start_date, end_date))
    direct_grouped = cursor.fetchall()

    from collections import defaultdict
    installments_map = defaultdict(list)

    for visit_id, pay_date, amount_sum, pay_ts in loan_grouped:
        if pay_date:
            installments_map[visit_id].append({'date': pay_date, 'amount': amount_sum, 'ts': pay_ts})
    for visit_id, pay_date, amount_sum, pay_ts in direct_grouped:
        if pay_date:
            installments_map[visit_id].append({'date': pay_date, 'amount': amount_sum, 'ts': pay_ts})

    grouped_transactions = []
    has_more = False
    if installments_map:
        visit_latest = []
        for vid, insts in installments_map.items():
            insts_sorted = sorted(insts, key=lambda x: x['ts'], reverse=True)
            installments_map[vid] = insts_sorted
            latest_date = insts_sorted[0]['date']
            latest_amount = insts_sorted[0]['amount']
            latest_ts = insts_sorted[0]['ts']
            today_total = sum(inst['amount'] for inst in insts_sorted if inst['date'] == today_str)
            visit_latest.append((vid, latest_date, latest_amount, today_total, latest_ts))

        visit_latest.sort(key=lambda x: x[4], reverse=True)

        total_count = len(visit_latest)
        page = visit_latest[offset:offset + limit]
        has_more = (offset + limit) < total_count
        top_visit_ids = [v[0] for v in page]

        if top_visit_ids:
            qmarks = ','.join(['?'] * len(top_visit_ids))
            cursor.execute(f'''
                SELECT visits.id, visits.total_fee, visits.amount_paid, visits.discount_amount, visits.status, visits.is_retail, patients.name
                FROM visits
                LEFT JOIN patients ON visits.patient_id = patients.id
                WHERE visits.id IN ({qmarks})
            ''', top_visit_ids)
            visit_rows = cursor.fetchall()
            visits_by_id = {r[0]: r for r in visit_rows}

            cursor.execute(f'''
                SELECT visit_id, item_name, item_type, quantity
                FROM visit_items
                WHERE visit_id IN ({qmarks})
            ''', top_visit_ids)
            items_by_visit = defaultdict(list)
            for v_id, item_name, item_type, quantity in cursor.fetchall():
                items_by_visit[v_id].append({
                    'name': item_name,
                    'category': item_type,
                    'quantity': quantity
                })

            for vid, latest_date, latest_amount, today_total, latest_ts in page:
                vrow = visits_by_id.get(vid)
                if vrow:
                    if today_total and today_total > 0:
                        summary_date = today_str
                        summary_amount = today_total
                    else:
                        summary_date = latest_date
                        summary_amount = latest_amount

                    total_fee = vrow[1] or 0
                    amount_paid = vrow[2] or 0
                    discount_amount = vrow[3] or 0
                    outstanding = max(0, total_fee - discount_amount - amount_paid)

                    grouped_transactions.append({
                        'visit_id': vrow[0],
                        'patient': vrow[6] or '🏪 Retail Sale',
                        'summary_date': summary_date,
                        'summary_amount': summary_amount,
                        'latest_date': latest_date,
                        'latest_amount': latest_amount,
                        'today_total': today_total,
                        'total_fee': total_fee,
                        'amount_paid': amount_paid,
                        'discount_amount': discount_amount,
                        'outstanding': outstanding,
                        'status': vrow[4] or '',
                        'is_retail': vrow[5] or 0,
                        'items_sold': items_by_visit.get(vid, []),
                        'installments': installments_map.get(vid, [])
                    })

    return grouped_transactions, has_more
