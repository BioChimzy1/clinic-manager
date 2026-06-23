import sqlite3

conn = sqlite3.connect('clinic.db')
cursor = conn.cursor()

# Check how many rows are in the staff table
cursor.execute("SELECT COUNT(*) FROM staff")
count = cursor.fetchone()[0]

print(f"🔍 Total staff records in database: {count}")

if count > 0:
    print("\n📋 Staff details:")
    cursor.execute("SELECT id, username, role, full_name FROM staff")
    rows = cursor.fetchall()
    for row in rows:
        print(f"   ID: {row[0]} | Username: {row[1]} | Role: {row[2]} | Name: {row[3]}")
else:
    print("❌ The staff table is completely EMPTY.")

conn.close()