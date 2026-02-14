"""First-run setup wizard."""
import bcrypt
import bleach
from flask import (
    Blueprint, request, redirect, url_for,
    render_template, flash, g, session
)

setup_bp = Blueprint('setup', __name__)


def has_admin():
    """Check if an admin user exists."""
    db = g.db
    admin = db.execute('SELECT id FROM users WHERE is_admin = 1').fetchone()
    return admin is not None


@setup_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    if has_admin():
        flash('Setup already complete.', 'info')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        step = request.form.get('step', '1')

        if step == '1':
            # Create admin account
            username = bleach.clean(request.form.get('username', '').strip())
            email = request.form.get('email', '').strip().lower()
            password = request.form.get('password', '')

            errors = []
            if not username or len(username) < 3:
                errors.append('Username must be at least 3 characters.')
            if not email or '@' not in email:
                errors.append('Valid email is required.')
            if len(password) < 8:
                errors.append('Password must be at least 8 characters.')

            if errors:
                for e in errors:
                    flash(e, 'error')
                return render_template('setup/index.html', step=1)

            password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

            db = g.db
            db.execute(
                '''INSERT INTO users (username, email, password_hash, display_name,
                   is_admin, is_verified, email_verified)
                   VALUES (?, ?, ?, ?, 1, 1, 1)''',
                (username, email, password_hash, username)
            )
            db.commit()

            user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
            session['user_id'] = user['id']
            session.permanent = True

            flash('Admin account created!', 'success')
            return render_template('setup/index.html', step=2)

        elif step == '2':
            # Site settings
            site_name = bleach.clean(request.form.get('site_name', 'Chirp').strip())
            site_desc = bleach.clean(request.form.get('site_description', '').strip())
            theme_color = request.form.get('theme_color', '#6750A4')

            db = g.db
            db.execute('INSERT OR REPLACE INTO site_settings (key, value) VALUES (?, ?)',
                       ('site_name', site_name))
            db.execute('INSERT OR REPLACE INTO site_settings (key, value) VALUES (?, ?)',
                       ('site_description', site_desc))
            db.execute('INSERT OR REPLACE INTO site_settings (key, value) VALUES (?, ?)',
                       ('theme_color', theme_color))
            db.commit()

            flash('Setup complete! Welcome to your new Chirp instance! 🐦', 'success')
            return redirect(url_for('feed.home'))

    return render_template('setup/index.html', step=1)
