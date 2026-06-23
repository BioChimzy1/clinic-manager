import sqlite3
import uuid
import datetime
from werkzeug.security import generate_password_hash

conn = sqlite3.connect('clinic.db')
cursor = conn.cursor()

# Create the default clinic (ID 1)
cursor.execute('''
    INSERT OR IGNORE INTO clinics (uuid, clinic_name, created_at, updated_at)
    VALUES (?, 'Boyd Medical Clinic', ?, ?)
''', (str(uuid.uuid4()), datetime.datetime.now().isoformat(), datetime.datetime.now().isoformat()))

# Create the admin user (Username: admin, Password: admin123)
admin_hash = generate_password_hash('admin123')
cursor.execute('''
    INSERT OR IGNORE INTO staff (uuid, clinic_id, full_name, role, username, password_hash, created_at, updated_at)
    VALUES (?, 1, 'System Admin', 'Admin', 'admin', ?, ?, ?)
''', (str(uuid.uuid4()), admin_hash, datetime.datetime.now().isoformat(), datetime.datetime.now().isoformat()))

conn.commit()
conn.close()
print("✅ Admin user created! Username: admin, Password: admin123")