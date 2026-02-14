"""REST API routes with rate limiting."""
import json
import secrets
import time
from functools import wraps
from flask import (
    Blueprint, request, jsonify, g, abort
)

api_bp = Blueprint('api', __name__, url_prefix='/api/v1')

# Simple in-memory rate limiter
_rate_limits = {}
RATE_LIMIT = 60  # requests per minute


def rate_limit(f):
    """Rate limiting decorator."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.remote_addr
        now = time.time()

        if key not in _rate_limits:
            _rate_limits[key] = []

        # Clean old entries
        _rate_limits[key] = [t for t in _rate_limits[key] if now - t < 60]

        if len(_rate_limits[key]) >= RATE_LIMIT:
            return jsonify({'error': 'Rate limit exceeded'}), 429

        _rate_limits[key].append(now)
        return f(*args, **kwargs)
    return decorated


def api_auth_required(f):
    """Require API authentication via session or API key."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.user:
            return f(*args, **kwargs)
        return jsonify({'error': 'Authentication required'}), 401
    return decorated


# ── Timeline ─────────────────────────────────────────────────────────

@api_bp.route('/timeline')
@rate_limit
@api_auth_required
def timeline():
    db = g.db
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    offset = (page - 1) * per_page

    posts = db.execute('''
        SELECT p.id, p.content, p.media, p.created_at, p.is_edited,
               u.username, u.display_name, u.profile_pic, u.is_verified,
               (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as like_count,
               (SELECT COUNT(*) FROM posts r WHERE r.parent_id = p.id AND r.is_deleted = 0) as reply_count,
               (SELECT COUNT(*) FROM posts rp WHERE rp.repost_id = p.id) as repost_count
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.is_deleted = 0
          AND (p.user_id = ? OR p.user_id IN (SELECT following_id FROM follows WHERE follower_id = ?))
        ORDER BY p.created_at DESC
        LIMIT ? OFFSET ?
    ''', (g.user['id'], g.user['id'], per_page, offset)).fetchall()

    return jsonify({
        'posts': [dict(p) for p in posts],
        'page': page,
        'per_page': per_page
    })


# ── Get Post ─────────────────────────────────────────────────────────

@api_bp.route('/posts/<int:post_id>')
@rate_limit
def get_post(post_id):
    db = g.db
    post = db.execute('''
        SELECT p.id, p.content, p.media, p.created_at, p.is_edited, p.parent_id,
               u.username, u.display_name, u.profile_pic, u.is_verified,
               (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as like_count,
               (SELECT COUNT(*) FROM posts r WHERE r.parent_id = p.id AND r.is_deleted = 0) as reply_count,
               (SELECT COUNT(*) FROM posts rp WHERE rp.repost_id = p.id) as repost_count
        FROM posts p JOIN users u ON p.user_id = u.id
        WHERE p.id = ? AND p.is_deleted = 0
    ''', (post_id,)).fetchone()

    if not post:
        return jsonify({'error': 'Post not found'}), 404

    return jsonify(dict(post))


# ── Create Post ──────────────────────────────────────────────────────

@api_bp.route('/posts', methods=['POST'])
@rate_limit
@api_auth_required
def create_post():
    data = request.get_json()
    if not data or not data.get('content'):
        return jsonify({'error': 'Content is required'}), 400

    content = data['content'].strip()
    if len(content) > 500:
        return jsonify({'error': 'Content exceeds 500 characters'}), 400

    import bleach
    content = bleach.clean(content)

    db = g.db
    db.execute(
        'INSERT INTO posts (user_id, content) VALUES (?, ?)',
        (g.user['id'], content)
    )
    db.commit()
    post_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]

    return jsonify({'id': post_id, 'content': content}), 201


# ── Like Post ────────────────────────────────────────────────────────

@api_bp.route('/posts/<int:post_id>/like', methods=['POST'])
@rate_limit
@api_auth_required
def api_like(post_id):
    db = g.db
    existing = db.execute(
        'SELECT id FROM likes WHERE user_id = ? AND post_id = ?',
        (g.user['id'], post_id)
    ).fetchone()

    if existing:
        db.execute('DELETE FROM likes WHERE id = ?', (existing['id'],))
        liked = False
    else:
        db.execute('INSERT INTO likes (user_id, post_id) VALUES (?, ?)',
                   (g.user['id'], post_id))
        liked = True
    db.commit()

    count = db.execute(
        'SELECT COUNT(*) as c FROM likes WHERE post_id = ?', (post_id,)
    ).fetchone()['c']

    return jsonify({'liked': liked, 'count': count})


# ── User Profile ─────────────────────────────────────────────────────

@api_bp.route('/users/<username>')
@rate_limit
def get_user(username):
    db = g.db
    user = db.execute('''
        SELECT id, username, display_name, bio, location, website,
               profile_pic, banner_pic, is_verified, created_at
        FROM users WHERE username = ? AND is_suspended = 0
    ''', (username,)).fetchone()

    if not user:
        return jsonify({'error': 'User not found'}), 404

    user_dict = dict(user)
    user_dict['follower_count'] = db.execute(
        'SELECT COUNT(*) as c FROM follows WHERE following_id = ?', (user['id'],)
    ).fetchone()['c']
    user_dict['following_count'] = db.execute(
        'SELECT COUNT(*) as c FROM follows WHERE follower_id = ?', (user['id'],)
    ).fetchone()['c']

    return jsonify(user_dict)


# ── Search ───────────────────────────────────────────────────────────

@api_bp.route('/search')
@rate_limit
def api_search():
    query = request.args.get('q', '').strip()
    search_type = request.args.get('type', 'posts')

    if not query:
        return jsonify({'results': []})

    db = g.db

    if search_type == 'users':
        results = db.execute('''
            SELECT id, username, display_name, bio, profile_pic, is_verified
            FROM users WHERE (username LIKE ? OR display_name LIKE ?) AND is_suspended = 0
            LIMIT 20
        ''', (f'%{query}%', f'%{query}%')).fetchall()
    else:
        results = db.execute('''
            SELECT p.id, p.content, p.created_at, u.username, u.display_name
            FROM posts p JOIN users u ON p.user_id = u.id
            WHERE p.content LIKE ? AND p.is_deleted = 0
            ORDER BY p.created_at DESC LIMIT 20
        ''', (f'%{query}%',)).fetchall()

    return jsonify({'results': [dict(r) for r in results]})


# ── Trending ─────────────────────────────────────────────────────────

@api_bp.route('/trending')
@rate_limit
def trending():
    db = g.db
    tags = db.execute('''
        SELECT h.tag, COUNT(ph.post_id) as count
        FROM hashtags h
        JOIN post_hashtags ph ON h.id = ph.hashtag_id
        JOIN posts p ON ph.post_id = p.id
        WHERE p.created_at > datetime('now', '-7 days') AND p.is_deleted = 0
        GROUP BY h.id
        ORDER BY count DESC LIMIT 10
    ''').fetchall()

    return jsonify({'trending': [dict(t) for t in tags]})
