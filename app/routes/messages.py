"""Direct messaging routes."""
import json
import bleach
from flask import (
    Blueprint, request, redirect, url_for,
    render_template, flash, g, abort, jsonify
)

messages_bp = Blueprint('messages', __name__, url_prefix='/messages')


# ── Conversations List ───────────────────────────────────────────────

@messages_bp.route('/')
def inbox():
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    conversations = db.execute('''
        SELECT c.*, cm.last_read_at,
               (SELECT content FROM messages WHERE conversation_id = c.id ORDER BY created_at DESC LIMIT 1) as last_message,
               (SELECT created_at FROM messages WHERE conversation_id = c.id ORDER BY created_at DESC LIMIT 1) as last_message_at,
               (SELECT COUNT(*) FROM messages m2 WHERE m2.conversation_id = c.id
                AND m2.created_at > COALESCE(cm.last_read_at, '1970-01-01')
                AND m2.sender_id != ?) as unread_count
        FROM conversations c
        JOIN conversation_members cm ON c.id = cm.conversation_id
        WHERE cm.user_id = ?
        ORDER BY last_message_at DESC
    ''', (g.user['id'], g.user['id'])).fetchall()

    # Get other members for each conversation
    conv_list = []
    for conv in conversations:
        members = db.execute('''
            SELECT u.id, u.username, u.display_name, u.profile_pic, u.is_verified,
                   u.is_corp_verified, u.affiliated_with,
                   corp.profile_pic as corp_profile_pic
            FROM conversation_members cm
            JOIN users u ON cm.user_id = u.id
            LEFT JOIN users corp ON u.affiliated_with = corp.id
            WHERE cm.conversation_id = ? AND cm.user_id != ?
        ''', (conv['id'], g.user['id'])).fetchall()
        conv_dict = dict(conv)
        conv_dict['members'] = [dict(m) for m in members]
        conv_list.append(conv_dict)

    return render_template('messages/inbox.html', conversations=conv_list)


# ── View Conversation ────────────────────────────────────────────────

@messages_bp.route('/<int:conv_id>')
def conversation(conv_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db

    # Verify membership
    member = db.execute(
        'SELECT * FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
        (conv_id, g.user['id'])
    ).fetchone()
    if not member:
        abort(404)

    messages = db.execute('''
        SELECT m.*, u.username, u.display_name, u.profile_pic, u.is_verified,
               u.is_corp_verified, u.affiliated_with,
               corp.profile_pic as corp_profile_pic
        FROM messages m
        JOIN users u ON m.sender_id = u.id
        LEFT JOIN users corp ON u.affiliated_with = corp.id
        WHERE m.conversation_id = ? AND m.is_deleted = 0
        ORDER BY m.created_at ASC
    ''', (conv_id,)).fetchall()

    # Mark as read
    db.execute(
        'UPDATE conversation_members SET last_read_at = CURRENT_TIMESTAMP WHERE conversation_id = ? AND user_id = ?',
        (conv_id, g.user['id'])
    )
    db.commit()

    # Get other members
    members = db.execute('''
        SELECT u.id, u.username, u.display_name, u.profile_pic, u.is_verified,
               u.is_corp_verified, u.affiliated_with,
               corp.profile_pic as corp_profile_pic
        FROM conversation_members cm
        JOIN users u ON cm.user_id = u.id
        LEFT JOIN users corp ON u.affiliated_with = corp.id
        WHERE cm.conversation_id = ?
    ''', (conv_id,)).fetchall()

    return render_template('messages/conversation.html',
                           messages=messages, conv_id=conv_id, members=members)


# ── Send Message ─────────────────────────────────────────────────────

@messages_bp.route('/<int:conv_id>/send', methods=['POST'])
def send_message(conv_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db

    # Verify membership
    member = db.execute(
        'SELECT * FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
        (conv_id, g.user['id'])
    ).fetchone()
    if not member:
        abort(404)

    content = bleach.clean(request.form.get('content', '').strip())
    if not content:
        return redirect(url_for('messages.conversation', conv_id=conv_id))

    db.execute(
        'INSERT INTO messages (conversation_id, sender_id, content) VALUES (?, ?, ?)',
        (conv_id, g.user['id'], content)
    )
    db.execute(
        'UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        (conv_id,)
    )

    # Notify other members
    other_members = db.execute(
        'SELECT user_id FROM conversation_members WHERE conversation_id = ? AND user_id != ?',
        (conv_id, g.user['id'])
    ).fetchall()
    for om in other_members:
        db.execute(
            'INSERT INTO notifications (user_id, actor_id, type, message) VALUES (?, ?, ?, ?)',
            (om['user_id'], g.user['id'], 'message', 'sent you a message')
        )
    db.commit()

    return redirect(url_for('messages.conversation', conv_id=conv_id))


# ── New Conversation ─────────────────────────────────────────────────

@messages_bp.route('/new', methods=['GET', 'POST'])
def new_conversation():
    if not g.user:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        content = bleach.clean(request.form.get('content', '').strip())

        db = g.db
        target = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if not target:
            flash('User not found.', 'error')
            return render_template('messages/new.html')

        if target['id'] == g.user['id']:
            flash("You can't message yourself.", 'error')
            return render_template('messages/new.html')

        # Check if conversation exists
        existing = db.execute('''
            SELECT c.id FROM conversations c
            JOIN conversation_members cm1 ON c.id = cm1.conversation_id AND cm1.user_id = ?
            JOIN conversation_members cm2 ON c.id = cm2.conversation_id AND cm2.user_id = ?
            WHERE c.is_group = 0
        ''', (g.user['id'], target['id'])).fetchone()

        if existing:
            if content:
                db.execute(
                    'INSERT INTO messages (conversation_id, sender_id, content) VALUES (?, ?, ?)',
                    (existing['id'], g.user['id'], content)
                )
                db.commit()
            return redirect(url_for('messages.conversation', conv_id=existing['id']))

        # Create new conversation
        db.execute('INSERT INTO conversations (is_group) VALUES (0)')
        conv_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.execute('INSERT INTO conversation_members (conversation_id, user_id) VALUES (?, ?)',
                   (conv_id, g.user['id']))
        db.execute('INSERT INTO conversation_members (conversation_id, user_id) VALUES (?, ?)',
                   (conv_id, target['id']))

        if content:
            db.execute(
                'INSERT INTO messages (conversation_id, sender_id, content) VALUES (?, ?, ?)',
                (conv_id, g.user['id'], content)
            )
        db.commit()

        return redirect(url_for('messages.conversation', conv_id=conv_id))

    return render_template('messages/new.html')
