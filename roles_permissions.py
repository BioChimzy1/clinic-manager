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
    "appointment.view": {"admin", "doctor", "pharmacist", "cashier", "receptionist", "nurse"},
    "appointment.create": {"admin", "doctor", "receptionist"},
    "appointment.schedule": {"admin", "doctor", "receptionist"},
    "appointment.confirm": {"admin", "doctor"},
    "appointment.cancel": {"admin", "doctor", "receptionist"},
    "appointment.reschedule": {"admin", "doctor", "receptionist"},
    "appointment.mark_missed": {"admin", "doctor", "receptionist"},
    "appointment.check_in": {"admin", "doctor", "receptionist", "nurse"},

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

    # Loans / Credit Accounting
    "loan.view": {"admin", "cashier", "doctor"},
    "loan.create": {"admin", "cashier", "doctor"},
    "loan.record_payment": {"admin", "cashier", "doctor"},
    "loan.set_due_date": {"admin", "cashier", "doctor"},

    # Standalone Over-the-counter Retail (Drafts Workflows)
    "retail.view": {"admin", "pharmacist", "cashier"},
    "retail.create_draft": {"admin", "pharmacist", "cashier"},
    "retail.finalize": {"admin", "pharmacist", "cashier"},
    "retail.cancel_draft": {"admin", "pharmacist", "cashier"},

    # Backoffice Finance
    "finance.view": {"admin", "doctor", "cashier"},
    "finance.view_transactions": {"admin", "doctor", "cashier"},
    "finance.add_expense": {"admin", "doctor", "cashier", "ground_worker"}, # Ground workers can file expenses
    "finance.delete_expense": {"admin"},

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