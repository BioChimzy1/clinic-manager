import os
import re
from flask import Blueprint, request, jsonify, send_from_directory

bp = Blueprint('static_routes', __name__)

@bp.route('/service-worker.js')
def service_worker():
    # Served from root (not /static/) on purpose. A service worker's
    # maximum control area defaults to the folder it's served from --
    # registering it at /static/service-worker.js would only ever let
    # it control pages under /static/, never /queue, /register, etc.
    # The Service-Worker-Allowed header makes the wider scope explicit.
    response = send_from_directory('static', 'service-worker.js')
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    return response    

# ------------------------------------------------------------------
# SERVE SPA SHELL
# ------------------------------------------------------------------
# In app.py, replace the serve_spa route with this:
@bp.route('/', defaults={'path': ''})
@bp.route('/<path:path>')
def serve_static_page(path):
    if path.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404
    if path == '':
        path = 'home'
    # Allow-list instead of blacklist: only letters, numbers, underscore,
    # hyphen and forward slash (for nested page paths). Anything else
    # (including '..', backslashes, absolute paths) is rejected outright.
    if not re.fullmatch(r'[A-Za-z0-9_\-/]+', path):
        return jsonify({'error': 'Not found'}), 404
    safe_path = path.strip('/')
    try:
        return send_from_directory('static/pages', f'{safe_path}.html')
    except FileNotFoundError:
        return send_from_directory('static/pages', '404.html') if os.path.exists('static/pages/404.html') else jsonify({'error': 'Not found'}), 404


@bp.after_app_request
def add_cache_headers(response):
    # Allow service worker to cache static assets and pages
    if request.path.startswith('/static/') or request.path in [
        '/register', '/queue', '/price_list', '/inventory', 
        '/dashboard', '/login', '/home', '/about', '/contact',
        '/cashier', '/loans', '/retail', '/appointments', 
        '/finance', '/staff'
    ]:
        response.headers['Cache-Control'] = 'public, max-age=3600'

    # Basic security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response
