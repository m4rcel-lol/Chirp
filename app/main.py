import os
import secrets
import json
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, g, request, session, redirect, url_for,
    render_template, flash, jsonify, abort
)
from dotenv import load_dotenv

load_dotenv()

from database import get_db, close_db, init_db

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_VIDEO_SIZE', 104857600))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
    seconds=int(os.environ.get('SESSION_LIFETIME', 7200))
)

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database'), exist_ok=True)


# ── Helpers ──────────────────────────────────────────────────────────

def login_required(f):
    """Decorator to require login."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.user is None:
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Decorator to require admin access."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.user is None:
            return redirect(url_for('auth.login'))
        if not g.user['is_admin']:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def get_site_settings():
    """Load site settings from database."""
    db = get_db()
    rows = db.execute('SELECT key, value FROM site_settings').fetchall()
    return {row['key']: row['value'] for row in rows}


# ── Before / After Request ──────────────────────────────────────────

@app.before_request
def before_request():
    g.db = get_db()
    g.user = None
    g.site_settings = get_site_settings()

    user_id = session.get('user_id')
    if user_id:
        g.user = g.db.execute(
            'SELECT * FROM users WHERE id = ? AND is_suspended = 0',
            (user_id,)
        ).fetchone()
        if g.user is None:
            session.clear()

    # CSRF token
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)

    # CSRF validation for POST/PUT/DELETE
    if request.method in ('POST', 'PUT', 'DELETE'):
        if request.content_type and 'application/json' in request.content_type:
            token = request.headers.get('X-CSRF-Token', '')
        else:
            token = request.form.get('csrf_token', '')
        # Skip CSRF for API routes with API key auth
        if not request.path.startswith('/api/v1/'):
            if token != session.get('csrf_token'):
                abort(403)


@app.teardown_appcontext
def teardown_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


# ── Template Context ─────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    unread_count = 0
    if g.user:
        row = g.db.execute(
            'SELECT COUNT(*) as c FROM notifications WHERE user_id = ? AND is_read = 0',
            (g.user['id'],)
        ).fetchone()
        unread_count = row['c'] if row else 0

    return {
        'current_user': g.user,
        'site_settings': g.site_settings,
        'unread_notifications': unread_count,
        'csrf_token': session.get('csrf_token', ''),
        'now': datetime.utcnow(),
    }


# ── Error Handlers ──────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return render_template('errors/404.html'), 404


@app.errorhandler(403)
def forbidden(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Forbidden'}), 403
    return render_template('errors/403.html'), 403


@app.errorhandler(500)
def server_error(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    return render_template('errors/500.html'), 500


# ── Index Route ──────────────────────────────────────────────────────

@app.route('/')
def index():
    if g.user:
        return redirect(url_for('feed.home'))
    return redirect(url_for('auth.login'))


# ── Register Blueprints ─────────────────────────────────────────────

from routes.auth import auth_bp
from routes.posts import posts_bp
from routes.feed import feed_bp
from routes.admin import admin_bp
from routes.messages import messages_bp
from routes.notifications import notifications_bp
from routes.api import api_bp
from routes.setup import setup_bp

app.register_blueprint(auth_bp)
app.register_blueprint(posts_bp)
app.register_blueprint(feed_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(messages_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(api_bp)
app.register_blueprint(setup_bp)


# ── Initialize Database ─────────────────────────────────────────────

with app.app_context():
    init_db()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
