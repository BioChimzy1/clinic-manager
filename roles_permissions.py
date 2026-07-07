# roles_permissions_improved.py

ROLES = {
    'admin': {'display_name': 'Administrator', 'description': 'Full system access'},
    'doctor': {'display_name': 'Doctor', 'description': 'Clinical operations & patient care'},
    'pharmacist': {'display_name': 'Pharmacist', 'description': 'Dispensing medicines, pharmacy inventory & price management'},
    'cashier': {'display_name': 'Cashier', 'description': 'Payments and financial records'},
    'receptionist': {'display_name': 'Receptionist', 'description': 'Patient registration & appointments'},
    'nurse': {'display_name': 'Nurse', 'description': 'Patient care, triage & vitals'},
    'guard': {'display_name': 'Guard', 'description': 'Security and visitor monitoring'},
    'ground_worker': {'display_name': 'Ground Worker', 'description': 'Maintenance & facility logging'}
}

PERMISSIONS = {
    # Queue & Flow
    "queue.view": {"admin", "doctor", "pharmacist", "cashier", "receptionist", "nurse", "guard"},
    "queue.register_patient": {"admin", "doctor", "receptionist"},
    "queue.triage": {"admin", "nurse", "doctor"},  # Added for clinical intake flow

    # Patient Records
    "patient.create": {"admin", "doctor", "receptionist"},
    "patient.edit": {"admin", "doctor", "receptionist"},
    "patient.view": {"admin", "doctor", "pharmacist", "cashier", "receptionist", "nurse"},
    "patient.delete": {"admin"},

    # Appointments
    # NOTE: cashier included on create/schedule/confirm/cancel/reschedule/
    # mark_missed/check_in to match live app.py behavior, where cashier has
    # always had blanket access to appointment management alongside admin,
    # doctor, and receptionist. appointment.review is a new key added purely
    # to give review_appointment() a proper permission to gate on (it had
    # none before).
    "appointment.view": {"admin", "doctor", "pharmacist", "cashier", "receptionist", "nurse"},
    "appointment.create": {"admin", "doctor", "receptionist", "cashier"},
    "appointment.schedule": {"admin", "doctor"},
    "appointment.confirm": {"admin", "doctor"},
    "appointment.cancel": {"admin", "doctor", "receptionist", "cashier"},
    "appointment.reschedule": {"admin", "doctor", "receptionist", "cashier"},
    "appointment.mark_missed": {"admin", "doctor", "receptionist", "cashier"},
    "appointment.check_in": {"admin", "doctor", "receptionist", "nurse", "cashier"},
    "appointment.review": {"admin", "doctor"},

    # Clinical Visits & Labs
    "visit.create": {"admin", "doctor"},
    "visit.view": {"admin", "doctor", "nurse", "cashier"},
    "visit.add_items": {"admin", "doctor"},
    "visit.edit_diagnosis": {"admin", "doctor"},
    "visit.add_vitals": {"admin", "doctor", "nurse"},
    "visit.order_labs": {"admin", "doctor"},  # Added for diagnostic workflow

    # Prescriptions & Dispensing
    "prescription.create": {"admin", "doctor"},
    "prescription.edit": {"admin", "doctor"},
    "prescription.view": {"admin", "doctor", "pharmacist", "nurse"},
    "dispense.create": {"admin", "pharmacist"},
    "dispense.cancel": {"admin", "pharmacist"},
    "dispense.view": {"admin", "doctor", "pharmacist"},

    # Pharmacy Inventory & Supply Chains
    "inventory.view": {"admin", "doctor", "pharmacist", "cashier", "nurse"},
    "inventory.add": {"admin", "pharmacist"},
    "inventory.edit": {"admin", "pharmacist"},
    "inventory.reduce": {"admin", "pharmacist"},
    "inventory.issue": {"admin", "pharmacist", "nurse"},
    "inventory.return": {"admin", "pharmacist", "nurse"},
    "inventory.view_expiry": {"admin", "doctor", "pharmacist", "nurse"},
    "inventory.manage_batches": {"admin", "pharmacist"}, # Added for expiration tracking

    # Pricing Engine
    "price_list.view": {"admin", "doctor", "pharmacist", "cashier"},
    "price_list.create": {"admin", "pharmacist"},
    "price_list.edit": {"admin", "pharmacist"},
    "price_list.delete": {"admin", "pharmacist"},
    "price_list.view_history": {"admin", "pharmacist"},

    # POS / Cashier
    "cashier.view": {"admin", "cashier", "doctor"},
    "payment.process_full": {"admin", "cashier", "doctor"},
    "payment.process_loan": {"admin", "cashier", "doctor"},
    "payment.apply_discount": {"admin", "cashier", "doctor"},
    "payment.apply_rounding": {"admin", "cashier", "doctor"},
    # Cashier sends a not-yet-paid visit back for editing -- either to
    # the doctor (consultation) or back into the live cart (retail).
    # Same role set as cashier.view since it's a cashier-side action.
    "cashier.send_back": {"admin", "cashier", "doctor"},

    # Loans / Credit Accounting
    # loan.view_list is separate from loan.view: the /loans list route has
    # always allowed receptionist alongside admin/cashier/doctor, while the
    # per-visit loan_details() and add_loan_payment() routes never have.
    # Keeping them as distinct keys avoids granting receptionist access to
    # loan_details()/add_loan_payment() as a side effect.
    "loan.view": {"admin", "cashier", "doctor"},
    "loan.view_list": {"admin", "cashier", "doctor", "receptionist"},
    "loan.create": {"admin", "cashier", "doctor"},
    "loan.record_payment": {"admin", "cashier", "doctor"},
    "loan.set_due_date": {"admin", "cashier", "doctor"},

    # Standalone Over-the-counter Retail (Drafts Workflows)
    # doctor included on retail.view to match live app.py behavior (doctor
    # has always had access to /retail alongside admin/cashier); pharmacist
    # was already part of this module's intended design.
    "retail.view": {"admin", "pharmacist", "cashier", "doctor"},
    "retail.create_draft": {"admin", "pharmacist", "cashier"},
    "retail.finalize": {"admin", "pharmacist", "cashier"},
    "retail.cancel_draft": {"admin", "pharmacist", "cashier"},

    # Backoffice Finance
    "finance.view": {"admin", "doctor", "cashier"},
    "finance.view_transactions": {"admin", "doctor", "cashier"},
    "finance.add_expense": {"admin", "doctor", "cashier", "ground_worker"}, # Ground workers can file expenses
    # DELIBERATE CALL, FLAG FOR REVIEW: this was originally {"admin"} only
    # in this module, which would have been a real tightening vs. live
    # app.py (admin/cashier/doctor). Reconciled to match what's live today
    # rather than silently restrict who can delete expenses. If admin-only
    # is actually what you want here, change this deliberately and confirm
    # with whoever relies on cashier/doctor having this today.
    "finance.delete_expense": {"admin", "cashier", "doctor"},

    # HR & Staff Auditing
    # NOTE: kept as admin+doctor to match the live app.py behavior
    # (allowed_roles = ['admin', 'doctor']) at the time this was wired in.
    # If you want to tighten this to admin-only, do it here deliberately
    # rather than as a side effect of adopting this module.
    "staff.view": {"admin", "doctor"},
    "staff.add": {"admin", "doctor"},
    "staff.edit": {"admin", "doctor"},
    "staff.deactivate": {"admin", "doctor"},
    "staff.reactivate": {"admin", "doctor"},

    # Multi-Tenant & Platform Configuration
    "clinic.setup": {"admin"},
    "clinic.create": {"admin"},
    "clinic.edit": {"admin"},

    # Audit Trail Logging
    "audit.view": {"admin"},
    "audit.export": {"admin"},
    
    # Facility Maintenance
    "facility.log_issue": {"admin", "ground_worker", "guard", "nurse"} # Added for low-clearance staff
}

def has_permission(role, permission):
    """Checks if a given role string holds explicit permissions.

    Role is normalized to lowercase before checking, since it's stored
    capitalized in the session/db (e.g. 'Admin', 'Doctor') but PERMISSIONS
    keys are lowercase ('admin', 'doctor').
    """
    if not role:
        return False
    return role.strip().lower() in PERMISSIONS.get(permission, set())