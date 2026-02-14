"""Post (Chirp) routes - create, edit, delete, like, bookmark, repost."""
import os
import re
import json
import uuid
from datetime import datetime, timedelta

import bleach
from flask import (
    Blueprint, request, redirect, url_for,
    render_template, flash, g, abort, jsonify
)
from werkzeug.utils import secure_filename

posts_bp = Blueprint('posts', __name__)

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
ALLOWED_IMAGE_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_IMAGES = int(os.environ.get('MAX_IMAGES_PER_POST', 4))


def extract_hashtags(content):
    """Extract hashtags from post content."""
    return list(set(re.findall(r'#(\w+)', content)))


def extract_mentions(content):
    """Extract @mentions from post content."""
    return list(set(re.findall(r'@(\w+)', content)))


def save_media(files):
    """Save uploaded media files and return paths."""
    paths = []
    for f in files:
        if f and f.filename:
            ext = f.filename.rsplit('.', 1)[1].lower() if '.' in f.filename else ''
            if ext in ALLOWED_IMAGE_EXT:
                filename = f"{uuid.uuid4().hex}.{ext}"
                path = os.path.join(UPLOAD_DIR, 'media')
                os.makedirs(path, exist_ok=True)
                f.save(os.path.join(path, filename))
                paths.append(f"/uploads/media/{filename}")
                if len(paths) >= MAX_IMAGES:
                    break
    return paths


def enrich_post(post, db, current_user=None):
    """Add interaction data to a post dict."""
    p = dict(post)
    if current_user:
        liked = db.execute(
            'SELECT id FROM likes WHERE user_id = ? AND post_id = ?',
            (current_user['id'], post['id'])
        ).fetchone()
        p['is_liked'] = liked is not None
        bookmarked = db.execute(
            'SELECT id FROM bookmarks WHERE user_id = ? AND post_id = ?',
            (current_user['id'], post['id'])
        ).fetchone()
        p['is_bookmarked'] = bookmarked is not None
    else:
        p['is_liked'] = False
        p['is_bookmarked'] = False

    # Get community notes
    notes = db.execute('''
        SELECT cn.*, u.username, u.display_name, u.is_verified
        FROM community_notes cn
        JOIN users u ON cn.author_id = u.id
        WHERE cn.post_id = ? AND cn.status = 'approved'
        ORDER BY cn.helpful_count DESC LIMIT 3
    ''', (post['id'],)).fetchall()
    p['community_notes'] = [dict(n) for n in notes]

    # Get staff notes
    staff = db.execute('''
        SELECT sn.*, u.username, u.display_name
        FROM staff_notes sn
        JOIN users u ON sn.author_id = u.id
        WHERE sn.post_id = ?
        ORDER BY sn.created_at DESC LIMIT 3
    ''', (post['id'],)).fetchall()
    p['staff_notes'] = [dict(n) for n in staff]

    # Get quote post if exists
    if post['quote_id']:
        quoted = db.execute('''
            SELECT p.*, u.username, u.display_name, u.profile_pic, u.is_verified
            FROM posts p JOIN users u ON p.user_id = u.id
            WHERE p.id = ? AND p.is_deleted = 0
        ''', (post['quote_id'],)).fetchone()
        p['quoted_post'] = dict(quoted) if quoted else None
    else:
        p['quoted_post'] = None

    return p


# ── Create Post ──────────────────────────────────────────────────────

@posts_bp.route('/compose', methods=['GET', 'POST'])
def compose():
    if not g.user:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        if not content or len(content) > 500:
            flash('Post must be 1-500 characters.', 'error')
            return render_template('posts/compose.html')

        content = bleach.clean(content)
        db = g.db

        # Handle media uploads
        media = []
        if 'media' in request.files:
            files = request.files.getlist('media')
            media = save_media(files)

        # Handle poll
        poll_id = None
        if request.form.get('poll_option_0'):
            options = []
            for i in range(4):
                opt = request.form.get(f'poll_option_{i}', '').strip()
                if opt:
                    options.append(opt)
            if len(options) >= 2:
                duration_hours = int(request.form.get('poll_duration', 24))
                expires = datetime.utcnow() + timedelta(hours=duration_hours)
                # Create post first, then poll
                pass  # handled below

        # Quote post
        quote_id = request.form.get('quote_id', type=int)

        db.execute(
            '''INSERT INTO posts (user_id, content, media, quote_id)
               VALUES (?, ?, ?, ?)''',
            (g.user['id'], content, json.dumps(media), quote_id)
        )
        db.commit()

        post_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

        # Create poll if needed
        if request.form.get('poll_option_0'):
            options = []
            for i in range(4):
                opt = request.form.get(f'poll_option_{i}', '').strip()
                if opt:
                    options.append(opt)
            if len(options) >= 2:
                duration_hours = int(request.form.get('poll_duration', 24))
                expires = datetime.utcnow() + timedelta(hours=duration_hours)
                db.execute(
                    'INSERT INTO polls (post_id, options, expires_at) VALUES (?, ?, ?)',
                    (post_id, json.dumps(options), expires.isoformat())
                )
                db.commit()
                poll_row = db.execute('SELECT last_insert_rowid()').fetchone()[0]
                db.execute('UPDATE posts SET poll_id = ? WHERE id = ?', (poll_row, post_id))
                db.commit()

        # Process hashtags
        tags = extract_hashtags(content)
        for tag in tags:
            db.execute('INSERT OR IGNORE INTO hashtags (tag) VALUES (?)', (tag,))
            hashtag = db.execute('SELECT id FROM hashtags WHERE tag = ?', (tag,)).fetchone()
            db.execute('INSERT OR IGNORE INTO post_hashtags (post_id, hashtag_id) VALUES (?, ?)',
                       (post_id, hashtag['id']))
            db.execute('UPDATE hashtags SET post_count = post_count + 1 WHERE id = ?',
                       (hashtag['id'],))
        db.commit()

        # Process mentions -> notifications
        mentions = extract_mentions(content)
        for mention in mentions:
            mentioned_user = db.execute(
                'SELECT id FROM users WHERE username = ?', (mention,)
            ).fetchone()
            if mentioned_user and mentioned_user['id'] != g.user['id']:
                db.execute(
                    'INSERT INTO notifications (user_id, actor_id, type, post_id) VALUES (?, ?, ?, ?)',
                    (mentioned_user['id'], g.user['id'], 'mention', post_id)
                )
        db.commit()

        flash('Chirp posted! 🐦', 'success')
        return redirect(url_for('feed.home'))

    quote_id = request.args.get('quote', type=int)
    quoted_post = None
    if quote_id:
        db = g.db
        quoted_post = db.execute('''
            SELECT p.*, u.username, u.display_name, u.profile_pic, u.is_verified
            FROM posts p JOIN users u ON p.user_id = u.id
            WHERE p.id = ? AND p.is_deleted = 0
        ''', (quote_id,)).fetchone()

    return render_template('posts/compose.html', quoted_post=quoted_post)


# ── View Single Post ─────────────────────────────────────────────────

@posts_bp.route('/post/<int:post_id>')
def view_post(post_id):
    db = g.db
    post = db.execute('''
        SELECT p.*, u.username, u.display_name, u.profile_pic, u.is_verified,
               (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as like_count,
               (SELECT COUNT(*) FROM posts WHERE parent_id = p.id AND is_deleted = 0) as reply_count,
               (SELECT COUNT(*) FROM posts WHERE repost_id = p.id) as repost_count
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.id = ? AND p.is_deleted = 0
    ''', (post_id,)).fetchone()

    if not post:
        abort(404)

    post = enrich_post(post, db, g.user)

    # Get poll data
    poll = None
    if post.get('poll_id'):
        poll = db.execute('SELECT * FROM polls WHERE id = ?', (post['poll_id'],)).fetchone()
        if poll:
            poll = dict(poll)
            poll['options'] = json.loads(poll['options'])
            poll['total_votes'] = db.execute(
                'SELECT COUNT(*) as c FROM poll_votes WHERE poll_id = ?', (poll['id'],)
            ).fetchone()['c']
            poll['vote_counts'] = []
            for i in range(len(poll['options'])):
                c = db.execute(
                    'SELECT COUNT(*) as c FROM poll_votes WHERE poll_id = ? AND option_index = ?',
                    (poll['id'], i)
                ).fetchone()['c']
                poll['vote_counts'].append(c)
            poll['user_voted'] = None
            if g.user:
                vote = db.execute(
                    'SELECT option_index FROM poll_votes WHERE poll_id = ? AND user_id = ?',
                    (poll['id'], g.user['id'])
                ).fetchone()
                if vote:
                    poll['user_voted'] = vote['option_index']

    # Get replies
    replies = db.execute('''
        SELECT p.*, u.username, u.display_name, u.profile_pic, u.is_verified,
               (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as like_count,
               (SELECT COUNT(*) FROM posts WHERE parent_id = p.id AND is_deleted = 0) as reply_count,
               (SELECT COUNT(*) FROM posts WHERE repost_id = p.id) as repost_count
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.parent_id = ? AND p.is_deleted = 0
        ORDER BY p.created_at ASC
    ''', (post_id,)).fetchall()

    enriched_replies = [enrich_post(r, db, g.user) for r in replies]

    return render_template('posts/view.html', post=post, replies=enriched_replies, poll=poll)


# ── Reply ────────────────────────────────────────────────────────────

@posts_bp.route('/post/<int:post_id>/reply', methods=['POST'])
def reply(post_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    parent = db.execute('SELECT * FROM posts WHERE id = ? AND is_deleted = 0', (post_id,)).fetchone()
    if not parent:
        abort(404)

    content = bleach.clean(request.form.get('content', '').strip())
    if not content or len(content) > 500:
        flash('Reply must be 1-500 characters.', 'error')
        return redirect(url_for('posts.view_post', post_id=post_id))

    media = []
    if 'media' in request.files:
        files = request.files.getlist('media')
        media = save_media(files)

    db.execute(
        'INSERT INTO posts (user_id, content, parent_id, media) VALUES (?, ?, ?, ?)',
        (g.user['id'], content, post_id, json.dumps(media))
    )

    # Notification
    if parent['user_id'] != g.user['id']:
        db.execute(
            'INSERT INTO notifications (user_id, actor_id, type, post_id) VALUES (?, ?, ?, ?)',
            (parent['user_id'], g.user['id'], 'reply', post_id)
        )
    db.commit()

    # Process mentions
    mentions = extract_mentions(content)
    reply_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    for mention in mentions:
        mentioned_user = db.execute('SELECT id FROM users WHERE username = ?', (mention,)).fetchone()
        if mentioned_user and mentioned_user['id'] != g.user['id']:
            db.execute(
                'INSERT INTO notifications (user_id, actor_id, type, post_id) VALUES (?, ?, ?, ?)',
                (mentioned_user['id'], g.user['id'], 'mention', reply_id)
            )
    db.commit()

    flash('Reply posted!', 'success')
    return redirect(url_for('posts.view_post', post_id=post_id))


# ── Edit Post ────────────────────────────────────────────────────────

@posts_bp.route('/post/<int:post_id>/edit', methods=['POST'])
def edit_post(post_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    post = db.execute('SELECT * FROM posts WHERE id = ? AND user_id = ?',
                       (post_id, g.user['id'])).fetchone()
    if not post:
        abort(404)

    # Check 30-minute edit window
    created = datetime.fromisoformat(post['created_at'])
    if datetime.utcnow() - created > timedelta(minutes=30):
        flash('Posts can only be edited within 30 minutes.', 'error')
        return redirect(url_for('posts.view_post', post_id=post_id))

    new_content = bleach.clean(request.form.get('content', '').strip())
    if not new_content or len(new_content) > 500:
        flash('Post must be 1-500 characters.', 'error')
        return redirect(url_for('posts.view_post', post_id=post_id))

    # Save edit history
    history = json.loads(post['edit_history'] or '[]')
    history.append({
        'content': post['content'],
        'edited_at': datetime.utcnow().isoformat()
    })

    db.execute(
        '''UPDATE posts SET content = ?, is_edited = 1, edit_history = ?,
           edited_at = CURRENT_TIMESTAMP WHERE id = ?''',
        (new_content, json.dumps(history), post_id)
    )
    db.commit()

    flash('Post updated!', 'success')
    return redirect(url_for('posts.view_post', post_id=post_id))


# ── Delete Post ──────────────────────────────────────────────────────

@posts_bp.route('/post/<int:post_id>/delete', methods=['POST'])
def delete_post(post_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    post = db.execute('SELECT * FROM posts WHERE id = ?', (post_id,)).fetchone()
    if not post:
        abort(404)

    # Allow owner or admin to delete
    if post['user_id'] != g.user['id'] and not g.user['is_admin']:
        abort(403)

    db.execute('UPDATE posts SET is_deleted = 1 WHERE id = ?', (post_id,))
    db.commit()

    flash('Post deleted.', 'info')
    return redirect(url_for('feed.home'))


# ── Like / Unlike ────────────────────────────────────────────────────

@posts_bp.route('/post/<int:post_id>/like', methods=['POST'])
def like_post(post_id):
    if not g.user:
        if request.headers.get('Accept') == 'application/json':
            return jsonify({'error': 'Login required'}), 401
        return redirect(url_for('auth.login'))

    db = g.db
    post = db.execute('SELECT * FROM posts WHERE id = ? AND is_deleted = 0', (post_id,)).fetchone()
    if not post:
        abort(404)

    existing = db.execute(
        'SELECT id FROM likes WHERE user_id = ? AND post_id = ?',
        (g.user['id'], post_id)
    ).fetchone()

    if existing:
        db.execute('DELETE FROM likes WHERE user_id = ? AND post_id = ?',
                    (g.user['id'], post_id))
        liked = False
    else:
        db.execute('INSERT INTO likes (user_id, post_id) VALUES (?, ?)',
                    (g.user['id'], post_id))
        if post['user_id'] != g.user['id']:
            db.execute(
                'INSERT INTO notifications (user_id, actor_id, type, post_id) VALUES (?, ?, ?, ?)',
                (post['user_id'], g.user['id'], 'like', post_id)
            )
        liked = True
    db.commit()

    like_count = db.execute(
        'SELECT COUNT(*) as c FROM likes WHERE post_id = ?', (post_id,)
    ).fetchone()['c']

    if request.headers.get('Accept') == 'application/json':
        return jsonify({'liked': liked, 'count': like_count})

    return redirect(request.referrer or url_for('feed.home'))


# ── Bookmark ─────────────────────────────────────────────────────────

@posts_bp.route('/post/<int:post_id>/bookmark', methods=['POST'])
def bookmark_post(post_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    existing = db.execute(
        'SELECT id FROM bookmarks WHERE user_id = ? AND post_id = ?',
        (g.user['id'], post_id)
    ).fetchone()

    if existing:
        db.execute('DELETE FROM bookmarks WHERE user_id = ? AND post_id = ?',
                    (g.user['id'], post_id))
        bookmarked = False
    else:
        db.execute('INSERT INTO bookmarks (user_id, post_id) VALUES (?, ?)',
                    (g.user['id'], post_id))
        bookmarked = True
    db.commit()

    if request.headers.get('Accept') == 'application/json':
        return jsonify({'bookmarked': bookmarked})

    return redirect(request.referrer or url_for('feed.home'))


# ── Repost (Rechirp) ────────────────────────────────────────────────

@posts_bp.route('/post/<int:post_id>/repost', methods=['POST'])
def repost(post_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    original = db.execute('SELECT * FROM posts WHERE id = ? AND is_deleted = 0', (post_id,)).fetchone()
    if not original:
        abort(404)

    # Check if already reposted
    existing = db.execute(
        'SELECT id FROM posts WHERE user_id = ? AND repost_id = ?',
        (g.user['id'], post_id)
    ).fetchone()

    if existing:
        db.execute('DELETE FROM posts WHERE id = ?', (existing['id'],))
        db.commit()
        flash('Rechirp removed.', 'info')
    else:
        db.execute(
            'INSERT INTO posts (user_id, content, repost_id) VALUES (?, ?, ?)',
            (g.user['id'], '', post_id)
        )
        if original['user_id'] != g.user['id']:
            db.execute(
                'INSERT INTO notifications (user_id, actor_id, type, post_id) VALUES (?, ?, ?, ?)',
                (original['user_id'], g.user['id'], 'repost', post_id)
            )
        db.commit()
        flash('Rechirped! 🔁', 'success')

    return redirect(request.referrer or url_for('feed.home'))


# ── Pin Post ─────────────────────────────────────────────────────────

@posts_bp.route('/post/<int:post_id>/pin', methods=['POST'])
def pin_post(post_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    post = db.execute('SELECT * FROM posts WHERE id = ? AND user_id = ?',
                       (post_id, g.user['id'])).fetchone()
    if not post:
        abort(404)

    # Unpin all other posts first
    db.execute('UPDATE posts SET is_pinned = 0 WHERE user_id = ?', (g.user['id'],))
    if not post['is_pinned']:
        db.execute('UPDATE posts SET is_pinned = 1 WHERE id = ?', (post_id,))
    db.commit()

    return redirect(request.referrer or url_for('auth.profile', username=g.user['username']))


# ── Poll Vote ────────────────────────────────────────────────────────

@posts_bp.route('/poll/<int:poll_id>/vote', methods=['POST'])
def vote_poll(poll_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    poll = db.execute('SELECT * FROM polls WHERE id = ?', (poll_id,)).fetchone()
    if not poll:
        abort(404)

    option_index = request.form.get('option', type=int)
    options = json.loads(poll['options'])
    if option_index is None or option_index < 0 or option_index >= len(options):
        abort(400)

    # Check if already voted
    existing = db.execute(
        'SELECT id FROM poll_votes WHERE poll_id = ? AND user_id = ?',
        (poll_id, g.user['id'])
    ).fetchone()
    if existing:
        flash('You already voted.', 'info')
    else:
        db.execute(
            'INSERT INTO poll_votes (poll_id, user_id, option_index) VALUES (?, ?, ?)',
            (poll_id, g.user['id'], option_index)
        )
        db.commit()
        flash('Vote cast!', 'success')

    return redirect(url_for('posts.view_post', post_id=poll['post_id']))


# ── Community Notes ──────────────────────────────────────────────────

@posts_bp.route('/post/<int:post_id>/community-note', methods=['POST'])
def add_community_note(post_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    post = db.execute('SELECT * FROM posts WHERE id = ? AND is_deleted = 0', (post_id,)).fetchone()
    if not post:
        abort(404)

    content = bleach.clean(request.form.get('content', '').strip())[:280]
    source1 = bleach.clean(request.form.get('source1', '').strip())
    source2 = bleach.clean(request.form.get('source2', '').strip())
    source3 = bleach.clean(request.form.get('source3', '').strip())
    category = request.form.get('category', 'missing_context')

    if not content:
        flash('Note content is required.', 'error')
        return redirect(url_for('posts.view_post', post_id=post_id))

    sources = [s for s in [source1, source2, source3] if s]
    if not sources:
        flash('At least one source link is required.', 'error')
        return redirect(url_for('posts.view_post', post_id=post_id))

    valid_categories = ['misleading', 'missing_context', 'satire', 'disputed', 'other']
    if category not in valid_categories:
        category = 'missing_context'

    db.execute(
        '''INSERT INTO community_notes (post_id, author_id, content, sources, category)
           VALUES (?, ?, ?, ?, ?)''',
        (post_id, g.user['id'], content, json.dumps(sources), category)
    )
    db.commit()

    flash('Community note submitted for review.', 'success')
    return redirect(url_for('posts.view_post', post_id=post_id))


@posts_bp.route('/community-note/<int:note_id>/rate', methods=['POST'])
def rate_community_note(note_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    note = db.execute('SELECT * FROM community_notes WHERE id = ?', (note_id,)).fetchone()
    if not note:
        abort(404)

    rating = request.form.get('rating', '')
    if rating not in ('helpful', 'not_helpful'):
        abort(400)

    # Upsert rating
    existing = db.execute(
        'SELECT id, rating FROM community_note_ratings WHERE note_id = ? AND user_id = ?',
        (note_id, g.user['id'])
    ).fetchone()

    if existing:
        if existing['rating'] != rating:
            db.execute('UPDATE community_note_ratings SET rating = ? WHERE id = ?',
                       (rating, existing['id']))
    else:
        db.execute(
            'INSERT INTO community_note_ratings (note_id, user_id, rating) VALUES (?, ?, ?)',
            (note_id, g.user['id'], rating)
        )

    # Update counts
    helpful = db.execute(
        "SELECT COUNT(*) as c FROM community_note_ratings WHERE note_id = ? AND rating = 'helpful'",
        (note_id,)
    ).fetchone()['c']
    not_helpful = db.execute(
        "SELECT COUNT(*) as c FROM community_note_ratings WHERE note_id = ? AND rating = 'not_helpful'",
        (note_id,)
    ).fetchone()['c']

    db.execute(
        'UPDATE community_notes SET helpful_count = ?, not_helpful_count = ? WHERE id = ?',
        (helpful, not_helpful, note_id)
    )

    # Auto-approve if 3+ helpful ratings
    if helpful >= 3:
        db.execute("UPDATE community_notes SET status = 'approved' WHERE id = ?", (note_id,))

    db.commit()
    return redirect(url_for('posts.view_post', post_id=note['post_id']))


# ── Report Post ──────────────────────────────────────────────────────

@posts_bp.route('/post/<int:post_id>/report', methods=['POST'])
def report_post(post_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    reason = bleach.clean(request.form.get('reason', '').strip())
    details = bleach.clean(request.form.get('details', '').strip())

    if not reason:
        flash('Please select a reason for reporting.', 'error')
        return redirect(url_for('posts.view_post', post_id=post_id))

    post = db.execute('SELECT * FROM posts WHERE id = ?', (post_id,)).fetchone()
    if not post:
        abort(404)

    db.execute(
        '''INSERT INTO reports (reporter_id, reported_post_id, reported_user_id, reason, details)
           VALUES (?, ?, ?, ?, ?)''',
        (g.user['id'], post_id, post['user_id'], reason, details)
    )
    db.commit()

    flash('Report submitted. Thank you.', 'success')
    return redirect(url_for('posts.view_post', post_id=post_id))
