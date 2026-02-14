"""Administration panel routes."""
import json
import bleach
from datetime import datetime
from flask import (
    Blueprint, request, redirect, url_for,
    render_template, flash, g, abort
)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(f):
    """Require admin or moderator access."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not g.user:
            return redirect(url_for('auth.login'))
        if not (g.user['is_admin'] or g.user['is_moderator']):
            abort(403)
        return f(*args, **kwargs)
    return decorated


def audit_log(db, admin_id, action, target_type=None, target_id=None, details=''):
    """Record admin action in audit log."""
    db.execute(
        'INSERT INTO audit_log (admin_id, action, target_type, target_id, details) VALUES (?, ?, ?, ?, ?)',
        (admin_id, action, target_type, target_id, details)
    )


# ── Dashboard ────────────────────────────────────────────────────────

@admin_bp.route('/')
@admin_required
def dashboard():
    db = g.db
    stats = {
        'total_users': db.execute('SELECT COUNT(*) as c FROM users').fetchone()['c'],
        'total_posts': db.execute('SELECT COUNT(*) as c FROM posts WHERE is_deleted = 0').fetchone()['c'],
        'posts_today': db.execute(
            "SELECT COUNT(*) as c FROM posts WHERE created_at > datetime('now', '-24 hours') AND is_deleted = 0"
        ).fetchone()['c'],
        'pending_reports': db.execute(
            "SELECT COUNT(*) as c FROM reports WHERE status = 'pending'"
        ).fetchone()['c'],
        'new_users_today': db.execute(
            "SELECT COUNT(*) as c FROM users WHERE created_at > datetime('now', '-24 hours')"
        ).fetchone()['c'],
    }

    recent_users = db.execute(
        'SELECT * FROM users ORDER BY created_at DESC LIMIT 10'
    ).fetchall()

    recent_reports = db.execute('''
        SELECT r.*, u.username as reporter_name
        FROM reports r
        JOIN users u ON r.reporter_id = u.id
        WHERE r.status = 'pending'
        ORDER BY r.created_at DESC LIMIT 10
    ''').fetchall()

    return render_template('admin/dashboard.html', stats=stats,
                           recent_users=recent_users, recent_reports=recent_reports)


# ── User Management ──────────────────────────────────────────────────

@admin_bp.route('/users')
@admin_required
def users():
    db = g.db
    query = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 25
    offset = (page - 1) * per_page

    if query:
        users_list = db.execute('''
            SELECT u.*, (SELECT COUNT(*) FROM posts WHERE user_id = u.id AND is_deleted = 0) as post_count
            FROM users u
            WHERE u.username LIKE ? OR u.email LIKE ? OR u.display_name LIKE ?
            ORDER BY u.created_at DESC LIMIT ? OFFSET ?
        ''', (f'%{query}%', f'%{query}%', f'%{query}%', per_page, offset)).fetchall()
    else:
        users_list = db.execute('''
            SELECT u.*, (SELECT COUNT(*) FROM posts WHERE user_id = u.id AND is_deleted = 0) as post_count
            FROM users u ORDER BY u.created_at DESC LIMIT ? OFFSET ?
        ''', (per_page, offset)).fetchall()

    return render_template('admin/users.html', users=users_list, query=query, page=page)


@admin_bp.route('/users/<int:user_id>')
@admin_required
def user_detail(user_id):
    db = g.db
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        abort(404)

    post_count = db.execute(
        'SELECT COUNT(*) as c FROM posts WHERE user_id = ? AND is_deleted = 0', (user_id,)
    ).fetchone()['c']

    recent_posts = db.execute('''
        SELECT * FROM posts WHERE user_id = ? AND is_deleted = 0
        ORDER BY created_at DESC LIMIT 20
    ''', (user_id,)).fetchall()

    return render_template('admin/user_detail.html', user=user,
                           post_count=post_count, recent_posts=recent_posts)


@admin_bp.route('/users/<int:user_id>/action', methods=['POST'])
@admin_required
def user_action(user_id):
    if not g.user['is_admin']:
        abort(403)

    db = g.db
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        abort(404)

    action = request.form.get('action', '')

    if action == 'verify':
        new_val = 0 if user['is_verified'] else 1
        db.execute('UPDATE users SET is_verified = ? WHERE id = ?', (new_val, user_id))
        audit_log(db, g.user['id'], 'toggle_verify', 'user', user_id)
        flash(f"{'Verified' if new_val else 'Unverified'} @{user['username']}", 'success')

    elif action == 'corp_verify':
        new_val = 0 if user['is_corp_verified'] else 1
        db.execute('UPDATE users SET is_corp_verified = ?, is_verified = 1 WHERE id = ?', (new_val, user_id))
        if not new_val:
            # Remove all affiliations when corp verification is revoked
            db.execute('UPDATE users SET affiliated_with = NULL WHERE affiliated_with = ?', (user_id,))
        audit_log(db, g.user['id'], 'toggle_corp_verify', 'user', user_id)
        flash(f"{'Granted' if new_val else 'Revoked'} corporation verification for @{user['username']}", 'success')

    elif action == 'suspend':
        reason = bleach.clean(request.form.get('reason', ''))
        db.execute('UPDATE users SET is_suspended = 1, suspend_reason = ? WHERE id = ?',
                   (reason, user_id))
        audit_log(db, g.user['id'], 'suspend_user', 'user', user_id, reason)
        flash(f"Suspended @{user['username']}", 'success')

    elif action == 'unsuspend':
        db.execute('UPDATE users SET is_suspended = 0, suspend_reason = ? WHERE id = ?',
                   ('', user_id))
        audit_log(db, g.user['id'], 'unsuspend_user', 'user', user_id)
        flash(f"Unsuspended @{user['username']}", 'success')

    elif action == 'make_mod':
        new_val = 0 if user['is_moderator'] else 1
        db.execute('UPDATE users SET is_moderator = ? WHERE id = ?', (new_val, user_id))
        audit_log(db, g.user['id'], 'toggle_moderator', 'user', user_id)
        flash(f"{'Granted' if new_val else 'Revoked'} moderator for @{user['username']}", 'success')

    elif action == 'delete':
        db.execute('DELETE FROM users WHERE id = ?', (user_id,))
        audit_log(db, g.user['id'], 'delete_user', 'user', user_id)
        flash(f"Deleted @{user['username']}", 'success')
        db.commit()
        return redirect(url_for('admin.users'))

    db.commit()
    return redirect(url_for('admin.user_detail', user_id=user_id))


# ── Reports ──────────────────────────────────────────────────────────

@admin_bp.route('/reports')
@admin_required
def reports():
    db = g.db
    status_filter = request.args.get('status', 'pending')
    reports_list = db.execute('''
        SELECT r.*, 
               reporter.username as reporter_name,
               reported_user.username as reported_username,
               p.content as post_content
        FROM reports r
        JOIN users reporter ON r.reporter_id = reporter.id
        LEFT JOIN users reported_user ON r.reported_user_id = reported_user.id
        LEFT JOIN posts p ON r.reported_post_id = p.id
        WHERE r.status = ?
        ORDER BY r.created_at DESC
    ''', (status_filter,)).fetchall()

    return render_template('admin/reports.html', reports=reports_list, status=status_filter)


@admin_bp.route('/reports/<int:report_id>/action', methods=['POST'])
@admin_required
def report_action(report_id):
    db = g.db
    report = db.execute('SELECT * FROM reports WHERE id = ?', (report_id,)).fetchone()
    if not report:
        abort(404)

    action = request.form.get('action', '')

    if action == 'resolve':
        db.execute(
            'UPDATE reports SET status = ?, resolved_by = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?',
            ('resolved', g.user['id'], report_id)
        )
        audit_log(db, g.user['id'], 'resolve_report', 'report', report_id)

    elif action == 'dismiss':
        db.execute(
            'UPDATE reports SET status = ?, resolved_by = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?',
            ('dismissed', g.user['id'], report_id)
        )
        audit_log(db, g.user['id'], 'dismiss_report', 'report', report_id)

    elif action == 'delete_post':
        if report['reported_post_id']:
            db.execute('UPDATE posts SET is_deleted = 1 WHERE id = ?', (report['reported_post_id'],))
        db.execute(
            'UPDATE reports SET status = ?, resolved_by = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?',
            ('resolved', g.user['id'], report_id)
        )
        audit_log(db, g.user['id'], 'delete_reported_post', 'report', report_id)

    db.commit()
    flash('Report handled.', 'success')
    return redirect(url_for('admin.reports'))


# ── Staff Notes ──────────────────────────────────────────────────────

@admin_bp.route('/staff-notes')
@admin_required
def staff_notes():
    db = g.db
    notes = db.execute('''
        SELECT sn.*, u.username as author_name, p.content as post_content
        FROM staff_notes sn
        JOIN users u ON sn.author_id = u.id
        JOIN posts p ON sn.post_id = p.id
        ORDER BY sn.created_at DESC
    ''').fetchall()
    return render_template('admin/staff_notes.html', notes=notes)


@admin_bp.route('/post/<int:post_id>/staff-note', methods=['POST'])
@admin_required
def add_staff_note(post_id):
    db = g.db
    content = bleach.clean(request.form.get('content', '').strip())
    note_type = request.form.get('note_type', 'info')

    valid_types = ['info', 'warning', 'misleading', 'investigation', 'violation']
    if note_type not in valid_types:
        note_type = 'info'

    if not content:
        flash('Note content is required.', 'error')
        return redirect(url_for('posts.view_post', post_id=post_id))

    db.execute(
        'INSERT INTO staff_notes (post_id, author_id, content, note_type) VALUES (?, ?, ?, ?)',
        (post_id, g.user['id'], content, note_type)
    )
    audit_log(db, g.user['id'], 'add_staff_note', 'post', post_id)
    db.commit()

    flash('Staff note added.', 'success')
    return redirect(url_for('posts.view_post', post_id=post_id))


# ── Community Notes Oversight ────────────────────────────────────────

@admin_bp.route('/community-notes')
@admin_required
def community_notes():
    db = g.db
    notes = db.execute('''
        SELECT cn.*, u.username as author_name, p.content as post_content
        FROM community_notes cn
        JOIN users u ON cn.author_id = u.id
        JOIN posts p ON cn.post_id = p.id
        ORDER BY cn.created_at DESC
    ''').fetchall()
    return render_template('admin/community_notes.html', notes=notes)


@admin_bp.route('/community-notes/<int:note_id>/action', methods=['POST'])
@admin_required
def community_note_action(note_id):
    db = g.db
    action = request.form.get('action', '')

    if action == 'approve':
        db.execute("UPDATE community_notes SET status = 'approved' WHERE id = ?", (note_id,))
    elif action == 'reject':
        db.execute("UPDATE community_notes SET status = 'rejected' WHERE id = ?", (note_id,))
    elif action == 'delete':
        db.execute('DELETE FROM community_notes WHERE id = ?', (note_id,))

    audit_log(db, g.user['id'], f'community_note_{action}', 'community_note', note_id)
    db.commit()

    flash('Community note updated.', 'success')
    return redirect(url_for('admin.community_notes'))


# ── Announcements ────────────────────────────────────────────────────

@admin_bp.route('/announcements')
@admin_required
def announcements():
    db = g.db
    announcements_list = db.execute('''
        SELECT a.*, u.username as author_name
        FROM announcements a
        JOIN users u ON a.author_id = u.id
        ORDER BY a.created_at DESC
    ''').fetchall()
    return render_template('admin/announcements.html', announcements=announcements_list)


@admin_bp.route('/announcements/create', methods=['GET', 'POST'])
@admin_required
def create_announcement():
    if request.method == 'POST':
        title = bleach.clean(request.form.get('title', '').strip())
        content = bleach.clean(request.form.get('content', '').strip())
        ann_type = request.form.get('type', 'banner')
        target = request.form.get('target', 'all')

        if not title or not content:
            flash('Title and content are required.', 'error')
            return render_template('admin/create_announcement.html')

        db = g.db
        db.execute(
            '''INSERT INTO announcements (author_id, title, content, type, target)
               VALUES (?, ?, ?, ?, ?)''',
            (g.user['id'], title, content, ann_type, target)
        )
        audit_log(db, g.user['id'], 'create_announcement', 'announcement', None, title)
        db.commit()

        flash('Announcement created!', 'success')
        return redirect(url_for('admin.announcements'))

    return render_template('admin/create_announcement.html')


@admin_bp.route('/announcements/<int:ann_id>/toggle', methods=['POST'])
@admin_required
def toggle_announcement(ann_id):
    db = g.db
    ann = db.execute('SELECT * FROM announcements WHERE id = ?', (ann_id,)).fetchone()
    if not ann:
        abort(404)

    new_val = 0 if ann['is_active'] else 1
    db.execute('UPDATE announcements SET is_active = ? WHERE id = ?', (new_val, ann_id))
    audit_log(db, g.user['id'], 'toggle_announcement', 'announcement', ann_id)
    db.commit()

    return redirect(url_for('admin.announcements'))


@admin_bp.route('/announcements/<int:ann_id>/delete', methods=['POST'])
@admin_required
def delete_announcement(ann_id):
    db = g.db
    db.execute('DELETE FROM announcements WHERE id = ?', (ann_id,))
    audit_log(db, g.user['id'], 'delete_announcement', 'announcement', ann_id)
    db.commit()

    flash('Announcement deleted.', 'success')
    return redirect(url_for('admin.announcements'))


# ── Site Settings ────────────────────────────────────────────────────

@admin_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def site_settings():
    if not g.user['is_admin']:
        abort(403)

    db = g.db

    if request.method == 'POST':
        settings_to_update = ['site_name', 'site_description', 'registration_mode', 'theme_color']
        for key in settings_to_update:
            val = bleach.clean(request.form.get(key, '').strip())
            if val:
                db.execute(
                    'INSERT OR REPLACE INTO site_settings (key, value) VALUES (?, ?)',
                    (key, val)
                )
        audit_log(db, g.user['id'], 'update_site_settings')
        db.commit()
        flash('Settings saved!', 'success')
        return redirect(url_for('admin.site_settings'))

    settings = {}
    rows = db.execute('SELECT * FROM site_settings').fetchall()
    for row in rows:
        settings[row['key']] = row['value']

    return render_template('admin/settings.html', settings=settings)


# ── Audit Log ────────────────────────────────────────────────────────

@admin_bp.route('/audit-log')
@admin_required
def audit_log_view():
    if not g.user['is_admin']:
        abort(403)

    db = g.db
    logs = db.execute('''
        SELECT al.*, u.username as admin_name
        FROM audit_log al
        JOIN users u ON al.admin_id = u.id
        ORDER BY al.created_at DESC
        LIMIT 100
    ''').fetchall()

    return render_template('admin/audit_log.html', logs=logs)
