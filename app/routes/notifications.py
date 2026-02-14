"""Notification routes."""
from flask import (
    Blueprint, request, redirect, url_for,
    render_template, flash, g, abort, jsonify
)

notifications_bp = Blueprint('notifications', __name__, url_prefix='/notifications')


@notifications_bp.route('/')
def index():
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    notifications = db.execute('''
        SELECT n.*, u.username as actor_name, u.display_name as actor_display,
               u.profile_pic as actor_pic, u.is_verified as actor_verified,
               u.is_corp_verified as actor_corp_verified, u.affiliated_with as actor_affiliated_with,
               corp.profile_pic as actor_corp_profile_pic,
               p.content as post_content
        FROM notifications n
        LEFT JOIN users u ON n.actor_id = u.id
        LEFT JOIN users corp ON u.affiliated_with = corp.id
        LEFT JOIN posts p ON n.post_id = p.id
        WHERE n.user_id = ?
        ORDER BY n.created_at DESC
        LIMIT 50
    ''', (g.user['id'],)).fetchall()

    # Mark all as read
    db.execute('UPDATE notifications SET is_read = 1 WHERE user_id = ?', (g.user['id'],))
    db.commit()

    return render_template('notifications/index.html', notifications=notifications)


@notifications_bp.route('/mark-read', methods=['POST'])
def mark_all_read():
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    db.execute('UPDATE notifications SET is_read = 1 WHERE user_id = ?', (g.user['id'],))
    db.commit()

    if request.headers.get('Accept') == 'application/json':
        return jsonify({'success': True})
    return redirect(url_for('notifications.index'))


@notifications_bp.route('/count')
def unread_count():
    if not g.user:
        return jsonify({'count': 0})

    db = g.db
    count = db.execute(
        'SELECT COUNT(*) as c FROM notifications WHERE user_id = ? AND is_read = 0',
        (g.user['id'],)
    ).fetchone()['c']

    return jsonify({'count': count})
