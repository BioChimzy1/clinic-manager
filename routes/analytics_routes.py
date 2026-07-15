import datetime
from flask import Blueprint, jsonify, request, session
from db import get_db
from utils.security import require_permission, require_developer
from utils.data_helpers import get_current_clinic_id

bp = Blueprint('analytics_routes', __name__)

# Statuses that represent an abandoned/superseded attempt rather than a
# real encounter or sale -- excluded everywhere we count/sum visits so a
# send-back-and-redo isn't counted twice. See dashboard/finance fixes.
DEAD_VISIT_STATUSES = ('Returned to Doctor', 'Cancelled')


# ------------------------------------------------------------------
# SHARED HELPERS
# ------------------------------------------------------------------
def _clinic_filter(clinic_ids, column='clinic_id'):
    """
    Returns (sql_fragment, params) to append after a base WHERE clause.
    clinic_ids=None means no filter (developer tier, all clinics).
    clinic_ids=[] means "no clinics" -> matches nothing.
    """
    if clinic_ids is None:
        return '', []
    if not clinic_ids:
        return ' AND 1=0', []
    placeholders = ','.join(['?'] * len(clinic_ids))
    return f' AND {column} IN ({placeholders})', list(clinic_ids)


def _dead_status_filter(column='status'):
    placeholders = ','.join(['?'] * len(DEAD_VISIT_STATUSES))
    return f' AND {column} NOT IN ({placeholders})', list(DEAD_VISIT_STATUSES)


def get_owned_clinic_ids(staff_id):
    """Clinics this staff member owns (has an 'admin' assignment in)."""
    cursor = get_db().cursor()
    cursor.execute(
        "SELECT clinic_id FROM staff_clinics WHERE staff_id = ? AND LOWER(role) = 'admin'",
        (staff_id,)
    )
    return [r[0] for r in cursor.fetchall()]


# ------------------------------------------------------------------
# CORE METRIC BUILDERS (shared by all three tiers)
# ------------------------------------------------------------------
def _build_overview(cursor, clinic_ids):
    cf, cf_params = _clinic_filter(clinic_ids)
    df, df_params = _dead_status_filter()

    if clinic_ids is None:
        cursor.execute("SELECT COUNT(*) FROM clinics")
        total_clinics = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM staff WHERE is_active = 1")
        total_staff = cursor.fetchone()[0]
    else:
        total_clinics = len(clinic_ids)
        sf, sf_params = _clinic_filter(clinic_ids, column='clinic_id')
        cursor.execute(
            f"SELECT COUNT(DISTINCT staff_id) FROM staff_clinics WHERE 1=1{sf}",
            sf_params
        )
        total_staff = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM patients WHERE is_active = 1{cf}", cf_params)
    total_patients = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM visits WHERE 1=1{cf}{df}", cf_params + df_params)
    total_visits = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT SUM(amount_paid) FROM visits WHERE status = 'Paid'{cf}", cf_params
    )
    total_revenue = cursor.fetchone()[0] or 0

    cursor.execute(f"SELECT COUNT(*) FROM inventory WHERE is_active = 1{cf}", cf_params)
    total_inventory_items = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM price_list WHERE is_active = 1{cf}", cf_params)
    total_price_items = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM visits WHERE status = 'Loan Active'{cf}", cf_params)
    active_loans = cursor.fetchone()[0]

    today = datetime.date.today().isoformat()
    cursor.execute(
        f"SELECT COUNT(*) FROM visits WHERE DATE(created_at) = ?{cf}{df}",
        [today] + cf_params + df_params
    )
    today_visits = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT SUM(amount_paid) FROM visits WHERE status = 'Paid' AND DATE(updated_at) = ?{cf}",
        [today] + cf_params
    )
    today_revenue = cursor.fetchone()[0] or 0

    return {
        'total_clinics': total_clinics,
        'total_staff': total_staff,
        'total_patients': total_patients,
        'total_visits': total_visits,
        'total_revenue': total_revenue,
        'total_inventory_items': total_inventory_items,
        'total_price_items': total_price_items,
        'active_loans': active_loans,
        'today_visits': today_visits,
        'today_revenue': today_revenue,
        'fetched_at': datetime.datetime.now().isoformat()
    }


def _build_visits_timeline(cursor, clinic_ids, period):
    cf, cf_params = _clinic_filter(clinic_ids)
    df, df_params = _dead_status_filter()

    if period == 'day':
        window, group_expr = "date('now', '-30 days')", "DATE(created_at)"
    elif period == 'week':
        window, group_expr = "date('now', '-84 days')", "strftime('%Y-%W', created_at)"
    else:
        period = 'month'
        window, group_expr = "date('now', '-365 days')", "strftime('%Y-%m', created_at)"

    cursor.execute(
        f'''
        SELECT {group_expr} as label, COUNT(*) as visit_count
        FROM visits
        WHERE created_at >= {window}{cf}{df}
        GROUP BY {group_expr}
        ORDER BY label ASC
        ''',
        cf_params + df_params
    )
    return period, [{'label': r[0], 'visits': r[1]} for r in cursor.fetchall()]


def _build_revenue_timeline(cursor, clinic_ids, period):
    cf, cf_params = _clinic_filter(clinic_ids)

    if period == 'day':
        window, group_expr = "date('now', '-30 days')", "DATE(updated_at)"
    elif period == 'week':
        window, group_expr = "date('now', '-84 days')", "strftime('%Y-%W', updated_at)"
    else:
        period = 'month'
        window, group_expr = "date('now', '-365 days')", "strftime('%Y-%m', updated_at)"

    cursor.execute(
        f'''
        SELECT {group_expr} as label, SUM(amount_paid) as revenue
        FROM visits
        WHERE status = 'Paid' AND updated_at >= {window}{cf}
        GROUP BY {group_expr}
        ORDER BY label ASC
        ''',
        cf_params
    )
    return period, [{'label': r[0], 'revenue': r[1] or 0} for r in cursor.fetchall()]


def _build_top_items(cursor, clinic_ids):
    cf, cf_params = _clinic_filter(clinic_ids, column='v.clinic_id')
    cursor.execute(
        f'''
        SELECT 
            vi.item_name,
            vi.item_type,
            SUM(vi.quantity) as total_sold,
            COUNT(DISTINCT vi.visit_id) as visit_count
        FROM visit_items vi
        JOIN visits v ON vi.visit_id = v.id
        WHERE v.status IN ('Paid', 'Loan Active'){cf}
        GROUP BY vi.item_name, vi.item_type
        ORDER BY total_sold DESC
        LIMIT 20
        ''',
        cf_params
    )
    return [{'name': r[0], 'type': r[1], 'total_sold': r[2], 'visit_count': r[3]}
            for r in cursor.fetchall()]


def _build_staff_performance(cursor, clinic_ids):
    vf, vf_params = _clinic_filter(clinic_ids, column='v.clinic_id')
    
    if clinic_ids is None:
        # Developer tier - show all active staff across all clinics
        cursor.execute('''
            SELECT 
                s.full_name,
                s.role,
                COUNT(DISTINCT v.id) as visits_handled,
                COUNT(DISTINCT p.id) as patients_seen,
                SUM(v.total_fee) as total_billed,
                SUM(v.amount_paid) as total_collected
            FROM staff s
            LEFT JOIN visits v ON s.id = v.doctor_id 
                AND v.status NOT IN ('Returned to Doctor', 'Cancelled')
            LEFT JOIN patients p ON v.patient_id = p.id
            WHERE s.is_active = 1 AND s.is_developer = 0
            GROUP BY s.id, s.full_name, s.role
            ORDER BY visits_handled DESC
            LIMIT 20
        ''')
    else:
        # Clinic/owner tier - only show staff from specified clinics
        sf, sf_params = _clinic_filter(clinic_ids, column='clinic_id')
        cursor.execute(
            f'''
            SELECT 
                s.full_name,
                s.role,
                COUNT(DISTINCT v.id) as visits_handled,
                COUNT(DISTINCT p.id) as patients_seen,
                SUM(v.total_fee) as total_billed,
                SUM(v.amount_paid) as total_collected
            FROM staff s
            JOIN (SELECT DISTINCT staff_id FROM staff_clinics WHERE 1=1{sf}) scoped_staff
                ON scoped_staff.staff_id = s.id
            LEFT JOIN visits v ON s.id = v.doctor_id 
                AND v.status NOT IN ('Returned to Doctor', 'Cancelled'){vf}
            LEFT JOIN patients p ON v.patient_id = p.id
            WHERE s.is_active = 1
            GROUP BY s.id, s.full_name, s.role
            ORDER BY visits_handled DESC
            LIMIT 20
            ''',
            sf_params + vf_params
        )
    
    rows = cursor.fetchall()
    return [{
        'name': r[0], 'role': r[1], 'visits_handled': r[2],
        'patients_seen': r[3], 'total_billed': r[4] or 0, 'total_collected': r[5] or 0
    } for r in rows]


def _build_inventory_status(cursor, clinic_ids):
    cf, cf_params = _clinic_filter(clinic_ids)
    today = datetime.date.today().isoformat()
    cutoff = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()

    cursor.execute(
        f'''
        SELECT category, SUM(quantity) as total_qty, COUNT(*) as item_count
        FROM inventory
        WHERE is_active = 1{cf}
        GROUP BY category
        ORDER BY total_qty DESC
        ''',
        cf_params
    )
    by_category = cursor.fetchall()

    cursor.execute(
        f'''
        SELECT category, COUNT(*) as expiring_count, SUM(quantity) as expiring_qty
        FROM inventory
        WHERE is_active = 1
        AND expiry_date BETWEEN ? AND ?{cf}
        GROUP BY category
        ''',
        [today, cutoff] + cf_params
    )
    expiring = cursor.fetchall()

    cursor.execute(
        f'''
        SELECT COUNT(*) FROM inventory
        WHERE is_active = 1 AND quantity <= min_alert_level AND quantity > 0{cf}
        ''',
        cf_params
    )
    low_stock = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT COUNT(*) FROM inventory WHERE is_active = 1 AND quantity = 0{cf}",
        cf_params
    )
    out_of_stock = cursor.fetchone()[0]

    return {
        'by_category': [{'category': r[0], 'total_quantity': r[1], 'item_count': r[2]} for r in by_category],
        'expiring_soon': [{'category': r[0], 'count': r[1], 'quantity': r[2]} for r in expiring],
        'low_stock_count': low_stock,
        'out_of_stock_count': out_of_stock
    }


def _build_loan_performance(cursor, clinic_ids):
    cf, cf_params = _clinic_filter(clinic_ids)

    cursor.execute(
        f'''
        SELECT 
            COUNT(*) as total_loans,
            SUM(total_fee) as total_loaned,
            SUM(amount_paid) as total_repaid,
            SUM(total_fee - amount_paid) as total_outstanding
        FROM visits
        WHERE status = 'Loan Active'{cf}
        ''',
        cf_params
    )
    loan_stats = cursor.fetchone()

    today = datetime.date.today().isoformat()
    cursor.execute(
        f'''
        SELECT COUNT(*) as overdue_count, SUM(total_fee - amount_paid) as overdue_amount
        FROM visits
        WHERE status = 'Loan Active' AND loan_due_date < ?{cf}
        ''',
        [today] + cf_params
    )
    overdue = cursor.fetchone()

    return {
        'total_loans': loan_stats[0] or 0,
        'total_loaned': loan_stats[1] or 0,
        'total_repaid': loan_stats[2] or 0,
        'total_outstanding': loan_stats[3] or 0,
        'repayment_rate': ((loan_stats[2] or 0) / (loan_stats[1] or 1)) * 100 if loan_stats[1] else 0,
        'overdue_count': overdue[0] or 0,
        'overdue_amount': overdue[1] or 0
    }


def _build_activity_heatmap(cursor, clinic_ids):
    cf, cf_params = _clinic_filter(clinic_ids)
    df, df_params = _dead_status_filter()

    cursor.execute(
        f'''
        SELECT 
            strftime('%w', created_at) as day_of_week,
            strftime('%H', created_at) as hour,
            COUNT(*) as activity_count
        FROM visits
        WHERE created_at >= date('now', '-7 days'){cf}{df}
        GROUP BY day_of_week, hour
        ORDER BY day_of_week ASC, hour ASC
        ''',
        cf_params + df_params
    )
    data = cursor.fetchall()

    days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    matrix = []
    for day_idx in range(7):
        day_data = {'day': days[day_idx], 'hours': []}
        for hour in range(24):
            hour_data = next((r[2] for r in data if int(r[0]) == day_idx and int(r[1]) == hour), 0)
            day_data['hours'].append(hour_data)
        matrix.append(day_data)

    return {'days': days, 'matrix': matrix}


def _build_audit_summary(cursor, clinic_ids):
    cf, cf_params = _clinic_filter(clinic_ids)

    cursor.execute(
        f'''
        SELECT action, COUNT(*) as action_count
        FROM audit_log
        WHERE 1=1{cf}
        GROUP BY action
        ORDER BY action_count DESC
        LIMIT 15
        ''',
        cf_params
    )
    actions = cursor.fetchall()

    al_cf, al_cf_params = _clinic_filter(clinic_ids, column='al.clinic_id')
    cursor.execute(
        f'''
        SELECT s.full_name, COUNT(al.id) as action_count
        FROM audit_log al
        LEFT JOIN staff s ON al.staff_id = s.id
        WHERE 1=1{al_cf}
        GROUP BY al.staff_id, s.full_name
        ORDER BY action_count DESC
        LIMIT 10
        ''',
        al_cf_params
    )
    active_staff = cursor.fetchall()

    cursor.execute(
        f'''
        SELECT DATE(timestamp) as day, COUNT(*) as activity_count
        FROM audit_log
        WHERE timestamp >= date('now', '-30 days'){cf}
        GROUP BY DATE(timestamp)
        ORDER BY day ASC
        ''',
        cf_params
    )
    daily_activity = cursor.fetchall()

    return {
        'actions': [{'action': r[0], 'count': r[1]} for r in actions],
        'active_staff': [{'name': r[0] or 'System', 'count': r[1]} for r in active_staff],
        'daily_activity': [{'date': r[0], 'count': r[1]} for r in daily_activity]
    }


# ==================================================================
# TIER 1: CLINIC-SCOPED (the clinic currently selected in session)
# ==================================================================
@bp.route('/api/analytics/clinic/clinics', methods=['GET'])
@require_permission('analytics.view')
def api_clinic_clinics():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    cursor = get_db().cursor()
    cf, cf_params = _clinic_filter([clinic_id], column='c.id')
    cursor.execute(f'''
        SELECT c.id, c.clinic_name, c.created_at,
               COUNT(DISTINCT sc.staff_id) as staff_count,
               COUNT(DISTINCT p.id) as patient_count,
               COUNT(DISTINCT v.id) as visit_count,
               COALESCE(SUM(v.amount_paid), 0) as total_revenue
        FROM clinics c
        LEFT JOIN staff_clinics sc ON c.id = sc.clinic_id
        LEFT JOIN patients p ON c.id = p.clinic_id AND p.is_active = 1
        LEFT JOIN visits v ON c.id = v.clinic_id AND v.status = 'Paid'
        WHERE 1=1{cf}
        GROUP BY c.id, c.clinic_name, c.created_at
    ''', cf_params)
    row = cursor.fetchone()
    return jsonify({'clinics': [{'id': row[0], 'name': row[1], 'created_at': row[2],
        'staff_count': row[3], 'patient_count': row[4], 'visit_count': row[5],
        'total_revenue': row[6]}] if row else []})
        
        
@bp.route('/api/analytics/clinic/overview', methods=['GET'])
@require_permission('analytics.view')
def api_clinic_overview():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    cursor = get_db().cursor()
    return jsonify(_build_overview(cursor, [clinic_id]))


@bp.route('/api/analytics/clinic/visits-timeline', methods=['GET'])
@require_permission('analytics.view')
def api_clinic_visits_timeline():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    period = request.args.get('period', 'month')
    cursor = get_db().cursor()
    period, data = _build_visits_timeline(cursor, [clinic_id], period)
    return jsonify({'period': period, 'data': data})


@bp.route('/api/analytics/clinic/revenue-timeline', methods=['GET'])
@require_permission('analytics.view')
def api_clinic_revenue_timeline():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    period = request.args.get('period', 'month')
    cursor = get_db().cursor()
    period, data = _build_revenue_timeline(cursor, [clinic_id], period)
    return jsonify({'period': period, 'data': data})


@bp.route('/api/analytics/clinic/top-items', methods=['GET'])
@require_permission('analytics.view')
def api_clinic_top_items():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    cursor = get_db().cursor()
    return jsonify({'items': _build_top_items(cursor, [clinic_id])})


@bp.route('/api/analytics/clinic/staff-performance', methods=['GET'])
@require_permission('analytics.view')
def api_clinic_staff_performance():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    cursor = get_db().cursor()
    return jsonify({'staff': _build_staff_performance(cursor, [clinic_id])})


@bp.route('/api/analytics/clinic/inventory-status', methods=['GET'])
@require_permission('analytics.view')
def api_clinic_inventory_status():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    cursor = get_db().cursor()
    return jsonify(_build_inventory_status(cursor, [clinic_id]))


@bp.route('/api/analytics/clinic/loan-performance', methods=['GET'])
@require_permission('analytics.view')
def api_clinic_loan_performance():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    cursor = get_db().cursor()
    return jsonify(_build_loan_performance(cursor, [clinic_id]))


@bp.route('/api/analytics/clinic/activity-heatmap', methods=['GET'])
@require_permission('analytics.view')
def api_clinic_activity_heatmap():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    cursor = get_db().cursor()
    return jsonify(_build_activity_heatmap(cursor, [clinic_id]))


@bp.route('/api/analytics/clinic/audit-summary', methods=['GET'])
@require_permission('analytics.view')
def api_clinic_audit_summary():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    cursor = get_db().cursor()
    return jsonify(_build_audit_summary(cursor, [clinic_id]))



# ==================================================================
# TIER 2: OWNER AGGREGATE (across every clinic this staff owns)
# ==================================================================
@bp.route('/api/analytics/owner/overview', methods=['GET'])
@require_permission('analytics.view_aggregate')
def api_owner_overview():
    clinic_ids = get_owned_clinic_ids(session.get('staff_id'))
    cursor = get_db().cursor()
    return jsonify(_build_overview(cursor, clinic_ids))


@bp.route('/api/analytics/owner/clinics', methods=['GET'])
@require_permission('analytics.view_aggregate')
def api_owner_clinics():
    """Per-clinic breakdown, restricted to clinics this owner actually owns."""
    clinic_ids = get_owned_clinic_ids(session.get('staff_id'))
    cursor = get_db().cursor()
    cf, cf_params = _clinic_filter(clinic_ids, column='c.id')

    cursor.execute(
        f'''
        SELECT 
            c.id, c.clinic_name, c.created_at,
            COUNT(DISTINCT sc.staff_id) as staff_count,
            COUNT(DISTINCT p.id) as patient_count,
            COUNT(DISTINCT v.id) as visit_count,
            COALESCE(SUM(v.amount_paid), 0) as total_revenue
        FROM clinics c
        LEFT JOIN staff_clinics sc ON c.id = sc.clinic_id
        LEFT JOIN patients p ON c.id = p.clinic_id AND p.is_active = 1
        LEFT JOIN visits v ON c.id = v.clinic_id AND v.status = 'Paid'
        WHERE 1=1{cf}
        GROUP BY c.id, c.clinic_name, c.created_at
        ORDER BY c.created_at DESC
        ''',
        cf_params
    )
    clinics = cursor.fetchall()
    return jsonify({'clinics': [{
        'id': r[0], 'name': r[1], 'created_at': r[2], 'staff_count': r[3],
        'patient_count': r[4], 'visit_count': r[5], 'total_revenue': r[6]
    } for r in clinics]})


@bp.route('/api/analytics/owner/visits-timeline', methods=['GET'])
@require_permission('analytics.view_aggregate')
def api_owner_visits_timeline():
    clinic_ids = get_owned_clinic_ids(session.get('staff_id'))
    period = request.args.get('period', 'month')
    cursor = get_db().cursor()
    period, data = _build_visits_timeline(cursor, clinic_ids, period)
    return jsonify({'period': period, 'data': data})


@bp.route('/api/analytics/owner/revenue-timeline', methods=['GET'])
@require_permission('analytics.view_aggregate')
def api_owner_revenue_timeline():
    clinic_ids = get_owned_clinic_ids(session.get('staff_id'))
    period = request.args.get('period', 'month')
    cursor = get_db().cursor()
    period, data = _build_revenue_timeline(cursor, clinic_ids, period)
    return jsonify({'period': period, 'data': data})


@bp.route('/api/analytics/owner/top-items', methods=['GET'])
@require_permission('analytics.view_aggregate')
def api_owner_top_items():
    clinic_ids = get_owned_clinic_ids(session.get('staff_id'))
    cursor = get_db().cursor()
    return jsonify({'items': _build_top_items(cursor, clinic_ids)})


@bp.route('/api/analytics/owner/staff-performance', methods=['GET'])
@require_permission('analytics.view_aggregate')
def api_owner_staff_performance():
    clinic_ids = get_owned_clinic_ids(session.get('staff_id'))
    cursor = get_db().cursor()
    return jsonify({'staff': _build_staff_performance(cursor, clinic_ids)})


@bp.route('/api/analytics/owner/inventory-status', methods=['GET'])
@require_permission('analytics.view_aggregate')
def api_owner_inventory_status():
    clinic_ids = get_owned_clinic_ids(session.get('staff_id'))
    cursor = get_db().cursor()
    return jsonify(_build_inventory_status(cursor, clinic_ids))


@bp.route('/api/analytics/owner/loan-performance', methods=['GET'])
@require_permission('analytics.view_aggregate')
def api_owner_loan_performance():
    clinic_ids = get_owned_clinic_ids(session.get('staff_id'))
    cursor = get_db().cursor()
    return jsonify(_build_loan_performance(cursor, clinic_ids))


@bp.route('/api/analytics/owner/activity-heatmap', methods=['GET'])
@require_permission('analytics.view_aggregate')
def api_owner_activity_heatmap():
    clinic_ids = get_owned_clinic_ids(session.get('staff_id'))
    cursor = get_db().cursor()
    return jsonify(_build_activity_heatmap(cursor, clinic_ids))


@bp.route('/api/analytics/owner/audit-summary', methods=['GET'])
@require_permission('analytics.view_aggregate')
def api_owner_audit_summary():
    clinic_ids = get_owned_clinic_ids(session.get('staff_id'))
    cursor = get_db().cursor()
    return jsonify(_build_audit_summary(cursor, clinic_ids))


# ==================================================================
# TIER 3: DEVELOPER (platform-wide, every clinic, no tenancy limit)
# ==================================================================
@bp.route('/api/analytics/developer/overview', methods=['GET'])
@require_developer
def api_developer_overview():
    cursor = get_db().cursor()
    return jsonify(_build_overview(cursor, None))


@bp.route('/api/analytics/developer/clinics', methods=['GET'])
@require_developer
def api_developer_clinics():
    cursor = get_db().cursor()
    cursor.execute('''
        SELECT 
            c.id, c.clinic_name, c.created_at,
            COUNT(DISTINCT sc.staff_id) as staff_count,
            COUNT(DISTINCT p.id) as patient_count,
            COUNT(DISTINCT v.id) as visit_count,
            COALESCE(SUM(v.amount_paid), 0) as total_revenue
        FROM clinics c
        LEFT JOIN staff_clinics sc ON c.id = sc.clinic_id
        LEFT JOIN patients p ON c.id = p.clinic_id AND p.is_active = 1
        LEFT JOIN visits v ON c.id = v.clinic_id AND v.status = 'Paid'
        GROUP BY c.id, c.clinic_name, c.created_at
        ORDER BY c.created_at DESC
    ''')
    clinics = cursor.fetchall()
    return jsonify({'clinics': [{
        'id': r[0], 'name': r[1], 'created_at': r[2], 'staff_count': r[3],
        'patient_count': r[4], 'visit_count': r[5], 'total_revenue': r[6]
    } for r in clinics]})


@bp.route('/api/analytics/developer/visits-timeline', methods=['GET'])
@require_developer
def api_developer_visits_timeline():
    period = request.args.get('period', 'month')
    cursor = get_db().cursor()
    period, data = _build_visits_timeline(cursor, None, period)
    return jsonify({'period': period, 'data': data})


@bp.route('/api/analytics/developer/revenue-timeline', methods=['GET'])
@require_developer
def api_developer_revenue_timeline():
    period = request.args.get('period', 'month')
    cursor = get_db().cursor()
    period, data = _build_revenue_timeline(cursor, None, period)
    return jsonify({'period': period, 'data': data})


@bp.route('/api/analytics/developer/top-items', methods=['GET'])
@require_developer
def api_developer_top_items():
    cursor = get_db().cursor()
    return jsonify({'items': _build_top_items(cursor, None)})


@bp.route('/api/analytics/developer/staff-performance', methods=['GET'])
@require_developer
def api_developer_staff_performance():
    cursor = get_db().cursor()
    return jsonify({'staff': _build_staff_performance(cursor, None)})


@bp.route('/api/analytics/developer/inventory-status', methods=['GET'])
@require_developer
def api_developer_inventory_status():
    cursor = get_db().cursor()
    return jsonify(_build_inventory_status(cursor, None))


@bp.route('/api/analytics/developer/loan-performance', methods=['GET'])
@require_developer
def api_developer_loan_performance():
    cursor = get_db().cursor()
    return jsonify(_build_loan_performance(cursor, None))


@bp.route('/api/analytics/developer/activity-heatmap', methods=['GET'])
@require_developer
def api_developer_activity_heatmap():
    cursor = get_db().cursor()
    return jsonify(_build_activity_heatmap(cursor, None))


@bp.route('/api/analytics/developer/audit-summary', methods=['GET'])
@require_developer
def api_developer_audit_summary():
    cursor = get_db().cursor()
    return jsonify(_build_audit_summary(cursor, None))


@bp.route('/api/analytics/developer/export', methods=['GET'])
@require_developer
def api_developer_export():
    """Export all visits, every clinic, as CSV."""
    from io import StringIO
    import csv

    cursor = get_db().cursor()
    cursor.execute('''
        SELECT 
            v.id, v.created_at, v.visit_date, v.diagnosis, v.total_fee,
            v.amount_paid, v.status, p.name as patient_name,
            s.full_name as doctor_name, c.clinic_name
        FROM visits v
        LEFT JOIN patients p ON v.patient_id = p.id
        LEFT JOIN staff s ON v.doctor_id = s.id
        LEFT JOIN clinics c ON v.clinic_id = c.id
        ORDER BY v.created_at DESC
        LIMIT 1000
    ''')

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Date', 'Visit Date', 'Diagnosis', 'Total Fee',
                      'Amount Paid', 'Status', 'Patient', 'Doctor', 'Clinic'])
    for row in cursor.fetchall():
        writer.writerow(row)

    return jsonify({
        'csv': output.getvalue(),
        'filename': f'analytics_export_{datetime.date.today().isoformat()}.csv'
    })
