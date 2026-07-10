# migrate_add_currency.py
import sqlite3
import datetime

DATABASE = 'clinic.db'  # match your DATABASE constant

def migrate():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS currencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            subunit_name TEXT NOT NULL,
            subunit_ratio INTEGER DEFAULT 100,
            is_active INTEGER DEFAULT 1,
            is_default INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
    ''')

    now = datetime.datetime.now().isoformat()
    cursor.execute('''
        INSERT OR IGNORE INTO currencies (code, name, symbol, subunit_name, subunit_ratio, is_default, created_at)
        VALUES
        ('MWK', 'Malawian Kwacha', 'MK', 'Tambala', 100, 1, ?),
        ('USD', 'US Dollar', '$', 'Cent', 100, 0, ?),
        ('EUR', 'Euro', '€', 'Cent', 100, 0, ?),
        ('GBP', 'British Pound', '£', 'Pence', 100, 0, ?),
        ('ZAR', 'South African Rand', 'R', 'Cent', 100, 0, ?)
    ''', (now, now, now, now, now))

    cursor.execute("PRAGMA table_info(clinics)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'currency_id' not in columns:
        cursor.execute('ALTER TABLE clinics ADD COLUMN currency_id INTEGER DEFAULT 1')
        print("Added currency_id column to clinics.")
    else:
        print("currency_id already present, skipping.")

    conn.commit()
    conn.close()
    print("✅ Currency migration complete.")

if __name__ == '__main__':
    migrate()