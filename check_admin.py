import sqlite3

conn = sqlite3.connect('clinic.db')
cursor = conn.cursor()
cursor.execute("SELECT username, role FROM staff WHERE username = 'admin'")
user = cursor.fetchone()
conn.close()

if user:
    print(f"✅ Admin user found: {user[0]} ({user[1]})")
else:
    print("❌ Admin user NOT found. Run add_admin.py again.")