import uuid
import datetime
from collections import defaultdict
from flask import Blueprint, request, jsonify, current_app
from db import get_db
from utils.security import require_permission
from utils.currency import get_clinic_currency
from utils.data_helpers import get_current_clinic_id

bp = Blueprint('retail_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - RETAIL
# ------------------------------------------------------------------
@bp.route('/api/retail/items', methods=['GET'])
@require_permission('retail.view')
def api_retail_items():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    conn = get_db()
    cursor = conn.cursor()
    today_str = datetime.date.today().isoformat()
    cursor.execute('''
        SELECT price_list.id, price_list.item_name, price_list.item_type, price_list.price,
               price_list.quantity AS pack_quantity, price_list.inventory_id,
               COALESCE(stock.usable_qty, 0) AS usable_qty
        FROM price_list
        LEFT JOIN inventory AS linked_item ON price_list.inventory_id = linked_item.id
        LEFT JOIN (
            SELECT LOWER(TRIM(item_name)) AS name_key, category,
                   SUM(CASE WHEN expiry_date >= ? THEN quantity ELSE 0 END) AS usable_qty
            FROM inventory WHERE is_active = 1 AND clinic_id = ? GROUP BY name_key, category
        ) AS stock ON stock.name_key = LOWER(TRIM(linked_item.item_name))
                 AND stock.category = linked_item.category
        WHERE price_list.clinic_id = ? AND price_list.is_active = 1
        ORDER BY price_list.item_name
    ''', (today_str, clinic_id, clinic_id))
    items = cursor.fetchall()
    
    currency = get_clinic_currency(clinic_id)
    
    return jsonify({
        'items': [{'id': r[0], 'name': r[1], 'type': r[2], 'price': r[3], 'packQty': r[4], 'inventory_id': r[5], 'usableQty': r[6]} for r in items],
        'currency': currency
    })

@bp.route('/api/retail/create_draft', methods=['POST'])
@require_permission('retail.create_draft')
def api_retail_create_draft():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400
    
    data = request.get_json()
    cart = data.get('cart', [])
    
    if not cart:
        return jsonify({'success': False, 'error': 'Cart is empty.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        total_fee = 0
        now = datetime.datetime.now().isoformat()
        today_str = datetime.date.today().isoformat()
        visit_items = []

        for item in cart:
            cursor.execute('''
                SELECT price_list.id, price_list.item_name, price_list.item_type, price_list.price, 
                       price_list.quantity AS pack_quantity, price_list.inventory_id,
                       COALESCE(stock.usable_qty, 0) AS usable_qty
                FROM price_list
                LEFT JOIN inventory AS linked_item ON price_list.inventory_id = linked_item.id
                LEFT JOIN (
                    SELECT LOWER(TRIM(item_name)) AS name_key, category,
                           SUM(CASE WHEN expiry_date >= ? THEN quantity ELSE 0 END) AS usable_qty
                    FROM inventory WHERE is_active = 1 AND clinic_id = ? GROUP BY name_key, category
                ) AS stock ON stock.name_key = LOWER(TRIM(linked_item.item_name)) 
                         AND stock.category = linked_item.category
                WHERE price_list.id = ? AND price_list.clinic_id = ? AND price_list.is_active = 1
            ''', (today_str, clinic_id, item['price_list_id'], clinic_id))
            
            row = cursor.fetchone()
            if not row:
                return jsonify({'success': False, 'error': f'Item not found: {item["name"]}'}), 400
            
            pl_id, pl_name, pl_type, pl_price, pl_pack_qty, pl_inv_id, usable_qty = row
            qty_sold = item['qty']

            if pl_inv_id is not None and qty_sold > usable_qty:
                return jsonify({'success': False, 'error': f'Only {usable_qty} units of "{pl_name}" available.'}), 400
            
            pack_qty = pl_pack_qty if pl_pack_qty and pl_pack_qty > 0 else 1
            price_per_unit = pl_price / pack_qty
            line_total = round(price_per_unit * qty_sold)
            total_fee += line_total

            visit_items.append({
                'pl_id': pl_id,
                'pl_name': pl_name,
                'pl_type': pl_type,
                'pl_inv_id': pl_inv_id,
                'qty_sold': qty_sold,
                'price_per_unit': price_per_unit,
                'line_total': line_total
            })
            
        cursor.execute('''
            INSERT INTO visits (uuid, clinic_id, patient_id, doctor_id, appointment_id, visit_date, 
                                diagnosis, total_fee, amount_paid, status, created_at, updated_at, is_retail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (str(uuid.uuid4()), clinic_id, None, None, None, now, 'Retail Sale', total_fee, 0, 'Ready for Cashier', now, now, 1))
        
        visit_id = cursor.lastrowid
        
        for item in visit_items:
            cursor.execute('''
                INSERT INTO visit_items (uuid, visit_id, inventory_id, price_list_id, item_type, item_name,
                                         quantity, price_per_unit, total_line_price, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (str(uuid.uuid4()), visit_id, item['pl_inv_id'], item['pl_id'], item['pl_type'], item['pl_name'], 
                  item['qty_sold'], round(item['price_per_unit']), item['line_total'], now))
        
        conn.commit()
        return jsonify({'success': True, 'visit_id': visit_id})
        
    except Exception as e:
        conn.rollback()
        current_app.logger.exception("Unhandled error in request")
        return jsonify({'success': False, 'error': 'Something went wrong. Please try again.'}), 500

@bp.route('/api/retail/pending', methods=['GET'])
@require_permission('retail.view')
def api_retail_pending():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, total_fee, amount_paid, status, created_at
        FROM visits
        WHERE clinic_id = ?
          AND is_retail = 1
          AND status = 'Ready for Cashier'
        ORDER BY created_at DESC
    ''', (clinic_id,))
    rows = cursor.fetchall()

    visit_ids = [r[0] for r in rows]
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

    return jsonify({
        'success': True,
        'pending': [
            {
                'visit_id': r[0],
                'total_fee': r[1],
                'amount_paid': r[2],
                'status': r[3],
                'created_at': r[4],
                'items_sold': items_by_visit.get(r[0], [])
            }
            for r in rows
        ]
    })

@bp.route('/api/retail/cancel/<int:visit_id>', methods=['POST'])
@require_permission('retail.cancel_draft')
def api_retail_cancel(visit_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT status, amount_paid, is_retail
        FROM visits
        WHERE id = ? AND clinic_id = ?
    ''', (visit_id, clinic_id))
    row = cursor.fetchone()

    if not row:
        return jsonify({'success': False, 'error': 'Visit not found.'}), 404

    status, amount_paid, is_retail = row

    if not is_retail:
        return jsonify({'success': False, 'error': 'This is not a retail sale.'}), 400

    if amount_paid and amount_paid > 0:
        return jsonify({'success': False, 'error': 'This sale already has a payment recorded and cannot be cancelled. Resume and complete it instead.'}), 400

    now = datetime.datetime.now().isoformat()
    cursor.execute('''
        UPDATE visits
        SET status = 'Cancelled', updated_at = ?
        WHERE id = ? AND status = 'Ready for Cashier'
    ''', (now, visit_id))

    if cursor.rowcount == 0:
        return jsonify({'success': False, 'error': 'Only unpaid drafts can be cancelled.'}), 400

    conn.commit()
    return jsonify({'success': True})
