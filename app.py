import os
from flask import Flask

app = Flask(__name__)

# THIS IS REQUIRED FOR LOGIN SESSIONS TO WORK
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    raise RuntimeError(
        "SECRET_KEY environment variable is not set. Refusing to start with an "
        "insecure fallback key. Set it in your Termux shell profile (~/.bashrc) "
        "for local dev, or in the WSGI configuration file on PythonAnywhere for "
        "production. See past chat notes for how each was set up."
    )
app.secret_key = _secret_key

app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['SESSION_COOKIE_SECURE'] = True      # only send session cookie over HTTPS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'   # basic CSRF mitigation
app.config['SESSION_COOKIE_HTTPONLY'] = True    # JS can't read the cookie

# ------------------------------------------------------------------
# DATABASE (connection helpers + schema init live in db.py)
# ------------------------------------------------------------------
from db import BASE_DIR, DATABASE, get_db, close_db

app.teardown_appcontext(close_db)

# ------------------------------------------------------------------
# BLUEPRINTS
# ------------------------------------------------------------------
from routes.auth_routes import bp as auth_bp
from routes.clinic_routes import bp as clinic_bp
from routes.dashboard_routes import bp as dashboard_bp
from routes.queue_routes import bp as queue_bp
from routes.inventory_routes import bp as inventory_bp
from routes.price_list_routes import bp as price_list_bp
from routes.cashier_routes import bp as cashier_bp
from routes.loans_routes import bp as loans_bp
from routes.retail_routes import bp as retail_bp
from routes.finance_routes import bp as finance_bp
from routes.staff_routes import bp as staff_bp
from routes.appointment_routes import bp as appointment_bp
from routes.visit_routes import bp as visit_bp
from routes.audit_routes import bp as audit_bp
from routes.static_routes import bp as static_bp
from routes.analytics_routes import bp as analytics_bp

app.register_blueprint(auth_bp)
app.register_blueprint(clinic_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(queue_bp)
app.register_blueprint(inventory_bp)
app.register_blueprint(price_list_bp)
app.register_blueprint(cashier_bp)
app.register_blueprint(loans_bp)
app.register_blueprint(retail_bp)
app.register_blueprint(finance_bp)
app.register_blueprint(staff_bp)
app.register_blueprint(appointment_bp)
app.register_blueprint(visit_bp)
app.register_blueprint(audit_bp)
app.register_blueprint(analytics_bp)
# static_bp must be registered LAST: it owns the catch-all '/<path:path>'
# route, which would otherwise swallow every other route registered after it.
app.register_blueprint(static_bp)

# ------------------------------------------------------------------
# RUN THE APP
# ------------------------------------------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
