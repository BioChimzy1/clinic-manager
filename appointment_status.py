"""
One-time cleanup script.

Some appointments were created while an older version of the server was
running (before the Walk-In -> Waiting / Appointment -> Pending status
logic was in place). Those rows have appointment_type = 'Appointment' but
status is stuck at 'Waiting' instead of 'Pending'.

This script finds exactly those rows and corrects their status to
'Pending', matching what register() would have set if the current code
had been running at the time. It does NOT touch anything else --
Walk-In rows, or any row whose status is already correct, are left alone.

Run this once with:
    python3 fix_stale_appointment_status.py

Safe to run multiple times -- after the first run, there will be nothing
left to fix, and it will report 0 rows updated.
"""
import sqlite3

conn = sqlite3.connect('clinic.db')
cursor = conn.cursor()

cursor.execute('''
    SELECT id, patient_id, appointment_type, status
    FROM appointments
    WHERE appointment_type = 'Appointment' AND status = 'Waiting'
''')
stale_rows = cursor.fetchall()

print(f"Found {len(stale_rows)} stale row(s) to fix:")
for row in stale_rows:
    print(f"  appointment id={row[0]}, patient_id={row[1]}, type={row[2]}, status={row[3]} -> will become 'Pending'")

if stale_rows:
    cursor.execute('''
        UPDATE appointments
        SET status = 'Pending'
        WHERE appointment_type = 'Appointment' AND status = 'Waiting'
    ''')
    conn.commit()
    print(f"\nFixed. {cursor.rowcount} row(s) updated to status='Pending'.")
else:
    print("\nNothing to fix -- all rows already have correct status values.")

conn.close()