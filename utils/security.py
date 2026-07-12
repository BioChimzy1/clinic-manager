import time
from collections import defaultdict
from functools import wraps
from flask import session, jsonify
from roles_permissions import has_permission

# ------------------------------------------------------------------
# LOGIN RATE LIMITING (simple in-memory, per-process)
# ------------------------------------------------------------------
# NOTE: this is per-process. On PythonAnywhere's free/single-worker tier
# that's fine. If you ever move to multiple workers, this needs to move
# to something shared (e.g. a db table) or it can be bypassed by hitting
# different workers.
_login_attempts = defaultdict(list)
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 60

def _login_rate_limited(key):
    now = time.time()
    _login_attempts[key] = [t for t in _login_attempts[key] if now - t < LOGIN_ATTEMPT_WINDOW_SECONDS]
    return len(_login_attempts[key]) >= LOGIN_ATTEMPT_LIMIT

def _record_login_attempt(key):
    _login_attempts[key].append(time.time())

# ------------------------------------------------------------------
# PERMISSION DECORATOR (API version)
# ------------------------------------------------------------------
def require_permission(permission):
    """Gate a route behind a permission string.
    Returns JSON error on denial instead of redirect."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            user_role = session.get('role', '')
            if not has_permission(user_role, permission):
                return jsonify({'success': False, 'error': 'Permission denied.'}), 403
            return view_func(*args, **kwargs)
        return wrapped
    return decorator
