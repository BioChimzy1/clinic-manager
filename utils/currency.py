from flask import g
from db import get_db
from utils.data_helpers import get_current_clinic_id

def get_clinic_currency(clinic_id):
    """Get the currency for a specific clinic with request-scoped caching"""
    
    # Check if already cached in this request
    cache_key = f'currency_{clinic_id}'
    if hasattr(g, cache_key):
        return getattr(g, cache_key)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT c.currency_id, cur.code, cur.symbol, cur.subunit_name, cur.subunit_ratio
        FROM clinics c
        JOIN currencies cur ON c.currency_id = cur.id
        WHERE c.id = ?
    ''', (clinic_id,))
    row = cursor.fetchone()
    
    if row:
        result = {
            'id': row[0],
            'code': row[1],
            'symbol': row[2],
            'subunit_name': row[3],
            'subunit_ratio': row[4]
        }
    else:
        # Fallback to MWK
        result = {'code': 'MWK', 'symbol': 'MK', 'subunit_name': 'Tambala', 'subunit_ratio': 100}
    
    # Cache it for this request
    setattr(g, cache_key, result)
    
    return result

def format_amount(amount, currency=None):
    """Format an amount in the given currency.

    Amounts are stored in subunits (e.g. tambala, cents) — divide by
    subunit_ratio to get the main-unit display value.
    """
    if currency is None:
        clinic_id = get_current_clinic_id()
        if clinic_id:
            currency = get_clinic_currency(clinic_id)
        else:
            currency = {'code': 'MWK', 'symbol': 'MK', 'subunit_ratio': 100}

    amount = amount or 0
    main_amount = amount / currency['subunit_ratio']
    formatted = f"{currency['symbol']} {main_amount:,.2f}"
    return formatted

def get_all_currencies():
    """Get all active currencies"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, code, name, symbol, subunit_ratio, is_default
        FROM currencies
        WHERE is_active = 1
        ORDER BY is_default DESC, name ASC
    ''')
    return cursor.fetchall()
