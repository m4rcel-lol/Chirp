"""Authentication routes - registration, login, logout, profiles."""
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta

import bcrypt
import bleach
from flask import (
    Blueprint, request, session, redirect, url_for,
    render_template, flash, g, abort, jsonify, current_app
)
from werkzeug.utils import secure_filename

auth_bp = Blueprint('auth', __name__)

ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')


def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXT


def save_upload(file, subfolder='avatars'):
    """Save an uploaded file and return its path."""
    if not file or not file.filename:
        return None
    if not allowed_image(file.filename):
        return None
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(UPLOAD_DIR, subfolder)
    os.makedirs(path, exist_ok=True)
    filepath = os.path.join(path, filename)
    file.save(filepath)
    return f"/uploads/{subfolder}/{filename}"


# ── Registration ─────────────────────────────────────────────────────

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if g.user:
        return redirect(url_for('feed.home'))

    enable_reg = os.environ.get('ENABLE_REGISTRATION', 'true').lower() == 'true'
    if not enable_reg:
        flash('Registration is currently closed.', 'error')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        username = bleach.clean(request.form.get('username', '').strip())
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        display_name = bleach.clean(request.form.get('display_name', '').strip())

        errors = []
        if not username or len(username) < 3 or len(username) > 30:
            errors.append('Username must be 3-30 characters.')
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            errors.append('Username can only contain letters, numbers, and underscores.')
        if not email or '@' not in email:
            errors.append('Valid email is required.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if password != confirm:
            errors.append('Passwords do not match.')

        if not errors:
            db = g.db
            existing = db.execute(
                'SELECT id FROM users WHERE username = ? OR email = ?',
                (username, email)
            ).fetchone()
            if existing:
                errors.append('Username or email already taken.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('auth/register.html',
                                   username=username, email=email,
                                   display_name=display_name)

        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        if not display_name:
            display_name = username

        db = g.db
        db.execute(
            '''INSERT INTO users (username, email, password_hash, display_name)
               VALUES (?, ?, ?, ?)''',
            (username, email, password_hash, display_name)
        )
        db.commit()

        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        session['user_id'] = user['id']
        session.permanent = True
        flash('Welcome to Chirp! 🐦', 'success')
        return redirect(url_for('feed.home'))

    return render_template('auth/register.html')


# ── Login ────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if g.user:
        return redirect(url_for('feed.home'))

    if request.method == 'POST':
        login_id = request.form.get('login', '').strip()
        password = request.form.get('password', '')

        db = g.db
        user = db.execute(
            'SELECT * FROM users WHERE username = ? OR email = ?',
            (login_id, login_id.lower())
        ).fetchone()

        if user and bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
            if user['is_suspended']:
                flash('Your account has been suspended.', 'error')
                return render_template('auth/login.html')

            session['user_id'] = user['id']
            session.permanent = True

            next_url = request.args.get('next', url_for('feed.home'))
            flash(f'Welcome back, {user["display_name"]}!', 'success')
            return redirect(next_url)
        else:
            flash('Invalid username/email or password.', 'error')

    return render_template('auth/login.html')


# ── Logout ───────────────────────────────────────────────────────────

@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


# ── Profile ──────────────────────────────────────────────────────────

@auth_bp.route('/user/<username>')
def profile(username):
    db = g.db
    user = db.execute('''
        SELECT u.*, corp.profile_pic as corp_profile_pic
        FROM users u
        LEFT JOIN users corp ON u.affiliated_with = corp.id
        WHERE u.username = ?
    ''', (username,)).fetchone()
    if not user:
        abort(404)

    # Get counts
    post_count = db.execute(
        'SELECT COUNT(*) as c FROM posts WHERE user_id = ? AND is_deleted = 0 AND parent_id IS NULL AND repost_id IS NULL',
        (user['id'],)
    ).fetchone()['c']

    follower_count = db.execute(
        'SELECT COUNT(*) as c FROM follows WHERE following_id = ?',
        (user['id'],)
    ).fetchone()['c']

    following_count = db.execute(
        'SELECT COUNT(*) as c FROM follows WHERE follower_id = ?',
        (user['id'],)
    ).fetchone()['c']

    # Check if current user follows this user
    is_following = False
    is_own_profile = False
    if g.user:
        is_own_profile = g.user['id'] == user['id']
        if not is_own_profile:
            f = db.execute(
                'SELECT id FROM follows WHERE follower_id = ? AND following_id = ?',
                (g.user['id'], user['id'])
            ).fetchone()
            is_following = f is not None

    # Get user's posts
    posts = db.execute('''
        SELECT p.*, u.username, u.display_name, u.profile_pic, u.is_verified,
               u.is_corp_verified, u.affiliated_with,
               corp.profile_pic as corp_profile_pic,
               (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as like_count,
               (SELECT COUNT(*) FROM posts WHERE parent_id = p.id AND is_deleted = 0) as reply_count,
               (SELECT COUNT(*) FROM posts WHERE repost_id = p.id) as repost_count
        FROM posts p
        JOIN users u ON p.user_id = u.id
        LEFT JOIN users corp ON u.affiliated_with = corp.id
        WHERE p.user_id = ? AND p.is_deleted = 0
        ORDER BY p.is_pinned DESC, p.created_at DESC
        LIMIT 50
    ''', (user['id'],)).fetchall()

    # Enrich posts with current user's interactions
    enriched_posts = []
    for post in posts:
        post_dict = dict(post)
        if g.user:
            liked = db.execute(
                'SELECT id FROM likes WHERE user_id = ? AND post_id = ?',
                (g.user['id'], post['id'])
            ).fetchone()
            post_dict['is_liked'] = liked is not None
            bookmarked = db.execute(
                'SELECT id FROM bookmarks WHERE user_id = ? AND post_id = ?',
                (g.user['id'], post['id'])
            ).fetchone()
            post_dict['is_bookmarked'] = bookmarked is not None
        else:
            post_dict['is_liked'] = False
            post_dict['is_bookmarked'] = False
        enriched_posts.append(post_dict)

    return render_template('auth/profile.html',
                           profile_user=user,
                           posts=enriched_posts,
                           post_count=post_count,
                           follower_count=follower_count,
                           following_count=following_count,
                           is_following=is_following,
                           is_own_profile=is_own_profile)


# ── Edit Profile ─────────────────────────────────────────────────────

@auth_bp.route('/settings/profile', methods=['GET', 'POST'])
def edit_profile():
    if not g.user:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        display_name = bleach.clean(request.form.get('display_name', '').strip())
        bio = bleach.clean(request.form.get('bio', '').strip())[:280]
        location = bleach.clean(request.form.get('location', '').strip())
        website = bleach.clean(request.form.get('website', '').strip())

        db = g.db
        updates = {
            'display_name': display_name or g.user['username'],
            'bio': bio,
            'location': location,
            'website': website,
        }

        # Handle profile pic upload
        if 'profile_pic' in request.files:
            pic_path = save_upload(request.files['profile_pic'], 'avatars')
            if pic_path:
                updates['profile_pic'] = pic_path

        # Handle banner upload
        if 'banner_pic' in request.files:
            banner_path = save_upload(request.files['banner_pic'], 'banners')
            if banner_path:
                updates['banner_pic'] = banner_path

        set_clause = ', '.join(f'{k} = ?' for k in updates)
        values = list(updates.values()) + [g.user['id']]
        db.execute(f'UPDATE users SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?', values)
        db.commit()

        flash('Profile updated!', 'success')
        return redirect(url_for('auth.profile', username=g.user['username']))

    return render_template('auth/edit_profile.html')


# ── Settings ─────────────────────────────────────────────────────────

@auth_bp.route('/settings', methods=['GET', 'POST'])
def settings():
    if not g.user:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        db = g.db
        action = request.form.get('action', '')

        if action == 'theme':
            theme = request.form.get('theme', 'auto')
            valid_themes = ['auto', 'light', 'dark', 'light-medium-contrast', 'light-high-contrast',
                            'dark-medium-contrast', 'dark-high-contrast', 'amoled']
            if theme not in valid_themes:
                theme = 'auto'
            accent = request.form.get('accent_color', '#6750A4')
            db.execute('UPDATE users SET theme = ?, accent_color = ? WHERE id = ?',
                       (theme, accent, g.user['id']))
            db.commit()
            flash('Theme updated!', 'success')

        elif action == 'privacy':
            is_private = 1 if request.form.get('is_private') else 0
            db.execute('UPDATE users SET is_private = ? WHERE id = ?',
                       (is_private, g.user['id']))
            db.commit()
            flash('Privacy settings updated!', 'success')

        elif action == 'password':
            current = request.form.get('current_password', '')
            new_pass = request.form.get('new_password', '')
            confirm = request.form.get('confirm_password', '')

            if not bcrypt.checkpw(current.encode(), g.user['password_hash'].encode()):
                flash('Current password is incorrect.', 'error')
            elif len(new_pass) < 8:
                flash('New password must be at least 8 characters.', 'error')
            elif new_pass != confirm:
                flash('New passwords do not match.', 'error')
            else:
                new_hash = bcrypt.hashpw(new_pass.encode(), bcrypt.gensalt()).decode()
                db.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                           (new_hash, g.user['id']))
                db.commit()
                flash('Password changed successfully!', 'success')

        return redirect(url_for('auth.settings'))

    return render_template('auth/settings.html')


# ── Follow / Unfollow ────────────────────────────────────────────────

@auth_bp.route('/follow/<int:user_id>', methods=['POST'])
def follow(user_id):
    if not g.user:
        return redirect(url_for('auth.login'))
    if g.user['id'] == user_id:
        abort(400)

    db = g.db
    target = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not target:
        abort(404)

    # Check if blocked
    blocked = db.execute(
        'SELECT id FROM blocks WHERE (blocker_id = ? AND blocked_id = ?) OR (blocker_id = ? AND blocked_id = ?)',
        (g.user['id'], user_id, user_id, g.user['id'])
    ).fetchone()
    if blocked:
        flash('Unable to follow this user.', 'error')
        return redirect(url_for('auth.profile', username=target['username']))

    existing = db.execute(
        'SELECT id FROM follows WHERE follower_id = ? AND following_id = ?',
        (g.user['id'], user_id)
    ).fetchone()

    if existing:
        db.execute('DELETE FROM follows WHERE follower_id = ? AND following_id = ?',
                    (g.user['id'], user_id))
        db.commit()
        flash(f'Unfollowed @{target["username"]}', 'info')
    else:
        db.execute('INSERT INTO follows (follower_id, following_id) VALUES (?, ?)',
                    (g.user['id'], user_id))
        # Create notification
        db.execute(
            'INSERT INTO notifications (user_id, actor_id, type) VALUES (?, ?, ?)',
            (user_id, g.user['id'], 'follow')
        )
        db.commit()
        flash(f'Following @{target["username"]}!', 'success')

    return redirect(url_for('auth.profile', username=target['username']))


# ── Block / Mute ─────────────────────────────────────────────────────

@auth_bp.route('/block/<int:user_id>', methods=['POST'])
def block_user(user_id):
    if not g.user or g.user['id'] == user_id:
        abort(400)

    db = g.db
    existing = db.execute(
        'SELECT id FROM blocks WHERE blocker_id = ? AND blocked_id = ?',
        (g.user['id'], user_id)
    ).fetchone()

    if existing:
        db.execute('DELETE FROM blocks WHERE blocker_id = ? AND blocked_id = ?',
                    (g.user['id'], user_id))
    else:
        db.execute('INSERT INTO blocks (blocker_id, blocked_id) VALUES (?, ?)',
                    (g.user['id'], user_id))
        # Remove any follows between users
        db.execute('DELETE FROM follows WHERE (follower_id = ? AND following_id = ?) OR (follower_id = ? AND following_id = ?)',
                    (g.user['id'], user_id, user_id, g.user['id']))
    db.commit()

    return redirect(request.referrer or url_for('feed.home'))


@auth_bp.route('/mute/<int:user_id>', methods=['POST'])
def mute_user(user_id):
    if not g.user or g.user['id'] == user_id:
        abort(400)

    db = g.db
    existing = db.execute(
        'SELECT id FROM mutes WHERE muter_id = ? AND muted_id = ?',
        (g.user['id'], user_id)
    ).fetchone()

    if existing:
        db.execute('DELETE FROM mutes WHERE muter_id = ? AND muted_id = ?',
                    (g.user['id'], user_id))
    else:
        db.execute('INSERT INTO mutes (muter_id, muted_id) VALUES (?, ?)',
                    (g.user['id'], user_id))
    db.commit()

    return redirect(request.referrer or url_for('feed.home'))


# ── Affiliation ──────────────────────────────────────────────────────

@auth_bp.route('/affiliate/<int:user_id>', methods=['POST'])
def affiliate_user(user_id):
    if not g.user or g.user['id'] == user_id:
        abort(400)

    # Only corp-verified users can affiliate others
    if not g.user['is_corp_verified']:
        flash('Only corporation verified users can affiliate others.', 'error')
        return redirect(request.referrer or url_for('feed.home'))

    db = g.db
    target = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not target:
        abort(404)

    if target['affiliated_with'] == g.user['id']:
        # Remove affiliation
        db.execute('UPDATE users SET affiliated_with = NULL WHERE id = ?', (user_id,))
        db.commit()
        flash(f'Removed affiliation for @{target["username"]}', 'info')
    else:
        # Add affiliation and make the user verified
        db.execute('UPDATE users SET affiliated_with = ?, is_verified = 1 WHERE id = ?',
                    (g.user['id'], user_id))
        db.commit()
        flash(f'@{target["username"]} is now affiliated with you!', 'success')

    return redirect(url_for('auth.profile', username=target['username']))
