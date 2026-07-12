import uuid
import datetime
from flask import Blueprint, request, jsonify
from db import get_db
from utils.security import require_permission
from utils.audit import log_audit
from utils.data_helpers import get_current_clinic_id, get_inventory_data

bp = Blueprint('inventory_routes', __name__)

# ------------------------------------------------------------------
# API ROUTES - INVENTORY
# ------------------------------------------------------------------
@bp.route('/api/inventory', methods=['GET'])
@require_permission('inventory.view')
def api_inventory():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'error': 'no_clinic'}), 400
    
    rows = get_inventory_data(clinic_id)
    today_str = datetime.date.today().isoformat()
    expiry_cutoff_str = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
    
    items = [
        {
            'id': row[0],
            'category': row[1],
            'name': row[2],
            'qty': row[3],
            'min_alert': row[4],
            'expiry': row[5]
        }
        for row in rows
    ]
    
    return jsonify({
        'items': items,
        'fetched_at': datetime.datetime.now().isoformat(),
        'today': today_str,
        'expiry_cutoff': expiry_cutoff_str
    })

@bp.route('/api/inventory/add', methods=['POST'])
@require_permission('inventory.add')
def api_inventory_add():
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    category = data.get('category')
    item_name = data.get('item_name', '').strip()
    quantity = int(data.get('quantity', 0))
    min_alert = int(data.get('min_alert_level', 10))
    expiry = data.get('expiry_date')

    if quantity < 0:
        return jsonify({'success': False, 'error': 'Quantity cannot be negative'}), 400

    item_name = ' '.join(item_name.split())
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id FROM inventory 
        WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND category = ? AND expiry_date = ? AND clinic_id = ? AND is_active = 1
    """, (item_name, category, expiry, clinic_id))
    existing = cursor.fetchone()

    if existing:
        return jsonify({'success': False, 'error': f'"{item_name}" with this exact expiry date already exists in stock'}), 400

    cursor.execute('''
        INSERT INTO inventory (uuid, clinic_id, category, item_name, quantity, min_alert_level, expiry_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), clinic_id, category, item_name, quantity, min_alert, expiry, datetime.datetime.now().isoformat()))

    conn.commit()
    log_audit('ADD_INVENTORY', 'inventory', cursor.lastrowid, 
              old_value=None, new_value=f"{item_name}, Qty: {quantity}")
    return jsonify({'success': True})

@bp.route('/api/inventory/edit/<int:inventory_id>', methods=['POST'])
@require_permission('inventory.edit')
def api_inventory_edit(inventory_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    category = data.get('category')
    item_name = data.get('item_name', '').strip()
    quantity = int(data.get('quantity', 0))
    min_alert = int(data.get('min_alert_level', 10))
    expiry = data.get('expiry_date')

    if quantity < 0:
        return jsonify({'success': False, 'error': 'Quantity cannot be negative'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT item_name, category FROM inventory WHERE id = ? AND clinic_id = ? AND is_active = 1",
        (inventory_id, clinic_id)
    )
    original = cursor.fetchone()

    if original is None:
        return jsonify({'success': False, 'error': 'Item not found'}), 404

    original_name, original_category = original
    item_name = ' '.join(item_name.split())
    now = datetime.datetime.now().isoformat()

    cursor.execute('''
        UPDATE inventory 
        SET category = ?, item_name = ?, quantity = quantity + ?, min_alert_level = ?, expiry_date = ?, updated_at = ?
        WHERE id = ? AND clinic_id = ? AND is_active = 1
    ''', (category, item_name, quantity, min_alert, expiry, now, inventory_id, clinic_id))

    cursor.execute('''
        UPDATE inventory
        SET item_name = ?, category = ?, min_alert_level = ?, updated_at = ?
        WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?))
          AND category = ?
          AND clinic_id = ?
          AND is_active = 1
          AND id != ?
    ''', (item_name, category, min_alert, now,
          original_name, original_category, clinic_id, inventory_id))

    cursor.execute('''
        UPDATE price_list 
        SET item_name = ?, updated_at = ? 
        WHERE inventory_id IN (
            SELECT id FROM inventory
            WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND category = ? AND clinic_id = ? AND is_active = 1
        )
    ''', (item_name, now, item_name, category, clinic_id))

    cursor.execute("""
        SELECT id, quantity FROM inventory 
        WHERE LOWER(TRIM(item_name)) = LOWER(TRIM(?)) AND category = ? AND expiry_date = ? AND clinic_id = ? AND is_active = 1
        ORDER BY id ASC
    """, (item_name, category, expiry, clinic_id))
    rows = cursor.fetchall()

    if len(rows) > 1:
        first_id = rows[0][0]
        total_qty = sum(row[1] for row in rows)
        cursor.execute("UPDATE inventory SET quantity = ? WHERE id = ?", (total_qty, first_id))
        for dup_id in [row[0] for row in rows[1:]]:
            cursor.execute(
                "UPDATE price_list SET inventory_id = ?, updated_at = ? WHERE inventory_id = ?",
                (first_id, now, dup_id)
            )
            cursor.execute("DELETE FROM inventory WHERE id = ?", (dup_id,))

    conn.commit()
    log_audit('EDIT_INVENTORY', 'inventory', inventory_id, 
              old_value=f"Original: {original_name}", 
              new_value=f"New: {item_name}, Qty +{quantity}")
    return jsonify({'success': True})

@bp.route('/api/inventory/reduce/<int:item_id>', methods=['POST'])
@require_permission('inventory.reduce')
def api_inventory_reduce(item_id):
    clinic_id = get_current_clinic_id()
    if not clinic_id:
        return jsonify({'success': False, 'error': 'no_clinic'}), 400

    data = request.get_json()
    amount_to_remove = data.get('amount')

    try:
        amount_to_remove = int(amount_to_remove)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid amount.'}), 400

    if amount_to_remove <= 0:
        return jsonify({'success': False, 'error': 'Amount must be greater than 0.'}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT quantity, item_name FROM inventory WHERE id = ? AND clinic_id = ? AND is_active = 1", (item_id, clinic_id))
    row = cursor.fetchone()

    if row is None:
        return jsonify({'success': False, 'error': 'Item not found.'}), 404

    current_qty, item_name = row

    if amount_to_remove > current_qty:
        return jsonify({'success': False, 'error': f'Cannot remove {amount_to_remove} -- only {current_qty} in stock.'}), 400

    new_qty = current_qty - amount_to_remove

    cursor.execute(
        "UPDATE inventory SET quantity = ?, updated_at = ? WHERE id = ?",
        (new_qty, datetime.datetime.now().isoformat(), item_id)
    )
    conn.commit()

    log_audit('REDUCE_INVENTORY', 'inventory', item_id,
              old_value=f"{item_name}, Qty: {current_qty}",
              new_value=f"{item_name}, Qty: {new_qty} (-{amount_to_remove})")

    return jsonify({'success': True, 'new_quantity': new_qty})
