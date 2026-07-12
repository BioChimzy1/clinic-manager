import uuid
import datetime
from flask import Blueprint, request, session, jsonify, current_app
from db import get_db
from utils.security import require_permission
from utils.audit import log_audit
from utils.currency import get_clinic_currency
from utils.data_helpers import get_current_clinic_id, get_price_list_data

bp = Blueprint('price_list_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - PRICE LIST
# ------------------------------------------------------------------
@bp.route('/api/price_list', methods=['GET'])
@require_permission('price_list.view')
def api_price_list():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400

    rows = get_price_list_data(clinic_id)
    items = [
        {
            'id': row[0],
            'item_type': row[1],
            'item_name': row[2],
            'price': row[3],
            'quantity': row[4],
            'usable_qty': row[5],
            'expired_qty': row[6],
            'no_stock_concept': row[7]
        }
        for row in rows
    ]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT item_name, category FROM inventory WHERE clinic_id = ? AND is_active = 1", (clinic_id,))
    inventory_items = [{'name': r[0], 'category': r[1]} for r in cursor.fetchall()]

    return jsonify({
        'items': items,
        'inventory_items': inventory_items,
        'fetched_at': datetime.datetime.now().isoformat(),
        'currency': get_clinic_currency(clinic_id)
    })

@bp.route('/api/price_list/add', methods=['POST'])
@require_permission('price_list.create')
def api_price_list_add():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    item_type = data.get('item_type')
    item_name = data.get('item_name')
    
    # Get clinic currency to determine subunit ratio
    currency = get_clinic_currency(clinic_id)
    subunit_ratio = currency['subunit_ratio']
    
    # Convert price to subunit (e.g., 100 for MWK tambala, 100 for USD cents, etc.)
    price = int(float(data.get('price', 0)) * subunit_ratio)
    quantity = int(data.get('quantity', 1))

    if price < 0 or quantity < 0:
        return jsonify({'success': False, 'error': 'Price and quantity cannot be negative.'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM inventory WHERE item_name = ? AND category = ? AND clinic_id = ? AND is_active = 1",
        (item_name, item_type, clinic_id)
    )
    inv_row = cursor.fetchone()
    inventory_id = inv_row[0] if inv_row else None
    
    cursor.execute('''
        INSERT INTO price_list (uuid, clinic_id, inventory_id, item_type, item_name, price, quantity, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_id, inventory_id, item_type, item_name, price, quantity, datetime.datetime.now().isoformat()))
    conn.commit()
    return jsonify({'success': True})

@bp.route('/api/price_list/update/<int:item_id>', methods=['POST'])
@require_permission('price_list.edit')
def api_price_list_update(item_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'No clinic selected.'}), 400
    
    data = request.get_json()
    new_price = data.get('price')
    new_qty = data.get('quantity')

    try:
        if new_price is None or float(new_price) < 0:
            return jsonify({'success': False, 'error': 'Price cannot be negative.'}), 400
        if new_qty is None or int(new_qty) < 0:
            return jsonify({'success': False, 'error': 'Quantity cannot be negative.'}), 400
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid price or quantity.'}), 400

    conn = get_db()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT price, quantity, item_type, item_name FROM price_list WHERE id = ? AND clinic_id = ?", (item_id, clinic_id))
        row = cursor.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Item not found.'}), 404

        old_price, old_qty, item_type, item_name = row
        
        # Get clinic currency
        currency = get_clinic_currency(clinic_id)
        subunit_ratio = currency['subunit_ratio']
        
        # Convert new_price to subunit
        new_price_subunit = int(float(new_price) * subunit_ratio)

        cursor.execute('''
            UPDATE price_list 
            SET price = ?, quantity = ?, updated_at = ? 
            WHERE id = ? AND clinic_id = ?
        ''', (new_price_subunit, new_qty, datetime.datetime.now().isoformat(), item_id, clinic_id))

        if old_price != new_price_subunit or old_qty != new_qty:
            cursor.execute('''
                INSERT INTO price_history (price_list_id, item_type, item_name, old_price, new_price, old_quantity, new_quantity, changed_by_staff_id, changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (item_id, item_type, item_name, old_price, new_price_subunit, old_qty, new_qty, session.get('staff_id'), datetime.datetime.now().isoformat()))

        conn.commit()
        log_audit('UPDATE_PRICE', 'price_list', item_id, 
                  old_value=f"Price: {old_price}, Qty: {old_qty}", 
                  new_value=f"Price: {new_price_subunit}, Qty: {new_qty}")
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        current_app.logger.exception("Unhandled error in request")
        return jsonify({'success': False, 'error': 'Something went wrong. Please try again.'}), 500

@bp.route('/api/price_list/delete/<int:item_id>', methods=['DELETE'])
@require_permission('price_list.delete')
def api_price_list_delete(item_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE price_list 
        SET is_active = 0, updated_at = ? 
        WHERE id = ? AND clinic_id = ?
    ''', (datetime.datetime.now().isoformat(), item_id, clinic_id))
    conn.commit()
    log_audit('DELETE_PRICE', 'price_list', item_id, 
              old_value='Active', new_value='Deactivated (Soft Delete)')
    return jsonify({'success': True})

@bp.route('/api/price_list/history/<int:item_id>', methods=['GET'])
@require_permission('price_list.view_history')
def api_price_list_history(item_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'No clinic selected'}), 403

    conn = get_db()
    cursor = conn.cursor()

    # Confirm this price_list item actually belongs to the current clinic
    # before returning its history -- otherwise any clinic's staff could
    # pull another clinic's price change history just by guessing item_id.
    cursor.execute("SELECT id FROM price_list WHERE id = ? AND clinic_id = ?", (item_id, clinic_id))
    if not cursor.fetchone():
        return jsonify({'error': 'Item not found'}), 404

    cursor.execute('''
        SELECT item_type, item_name, old_price, new_price, old_quantity, new_quantity, changed_at, staff.full_name
        FROM price_history
        LEFT JOIN staff ON price_history.changed_by_staff_id = staff.id
        WHERE price_list_id = ?
        ORDER BY changed_at DESC
    ''', (item_id,))
    rows = cursor.fetchall()
    
    return jsonify({
        'history': [{
            'type': r[0],
            'name': r[1],
            'old_price': r[2],
            'new_price': r[3],
            'old_qty': r[4],
            'new_qty': r[5],
            'changed_at': r[6],
            'changed_by': r[7] or 'System'
        } for r in rows],
        'currency': get_clinic_currency(clinic_id)
    })
