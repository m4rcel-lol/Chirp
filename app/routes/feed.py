"""Feed and discovery routes - home timeline, explore, search, trending."""
import json
from flask import (
    Blueprint, request, redirect, url_for,
    render_template, flash, g, abort
)

from routes.posts import enrich_post

feed_bp = Blueprint('feed', __name__)


def get_feed_posts(db, user, page=1, per_page=20):
    """Get home timeline posts for a user (from people they follow + own)."""
    offset = (page - 1) * per_page
    posts = db.execute('''
        SELECT p.*, u.username, u.display_name, u.profile_pic, u.is_verified,
               (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as like_count,
               (SELECT COUNT(*) FROM posts r WHERE r.parent_id = p.id AND r.is_deleted = 0) as reply_count,
               (SELECT COUNT(*) FROM posts rp WHERE rp.repost_id = p.id) as repost_count
        FROM posts p
        JOIN users u ON p.user_id = u.id
        LEFT JOIN mutes m ON m.muter_id = ? AND m.muted_id = p.user_id
        LEFT JOIN blocks b ON b.blocker_id = ? AND b.blocked_id = p.user_id
        WHERE p.is_deleted = 0
          AND m.muter_id IS NULL
          AND b.blocker_id IS NULL
          AND (
            p.user_id = ?
            OR p.user_id IN (SELECT following_id FROM follows WHERE follower_id = ?)
          )
        ORDER BY p.created_at DESC
        LIMIT ? OFFSET ?
    ''', (user['id'], user['id'], user['id'], user['id'], per_page, offset)).fetchall()

    return [enrich_post(p, db, user) for p in posts]


# ── Home Timeline ────────────────────────────────────────────────────

@feed_bp.route('/home')
def home():
    if not g.user:
        return redirect(url_for('auth.login'))

    page = request.args.get('page', 1, type=int)
    db = g.db
    posts = get_feed_posts(db, g.user, page)

    # Get active announcements
    announcements = db.execute('''
        SELECT a.* FROM announcements a
        LEFT JOIN announcement_dismissals ad
            ON a.id = ad.announcement_id AND ad.user_id = ?
        WHERE a.is_active = 1
          AND ad.announcement_id IS NULL
          AND (a.expires_at IS NULL OR a.expires_at > datetime('now'))
          AND a.publish_at <= datetime('now')
        ORDER BY a.created_at DESC LIMIT 3
    ''', (g.user['id'],)).fetchall()

    return render_template('feed/home.html', posts=posts, page=page,
                           announcements=announcements)


# ── Explore ──────────────────────────────────────────────────────────

@feed_bp.route('/explore')
def explore():
    db = g.db

    # Trending hashtags (last 7 days)
    trending_tags = db.execute('''
        SELECT h.tag, COUNT(ph.post_id) as recent_count
        FROM hashtags h
        JOIN post_hashtags ph ON h.id = ph.hashtag_id
        JOIN posts p ON ph.post_id = p.id
        WHERE p.created_at > datetime('now', '-7 days')
          AND p.is_deleted = 0
        GROUP BY h.id
        ORDER BY recent_count DESC
        LIMIT 10
    ''').fetchall()

    # Trending posts (most liked in last 24h)
    trending_posts = db.execute('''
        SELECT p.*, u.username, u.display_name, u.profile_pic, u.is_verified,
               (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as like_count,
               (SELECT COUNT(*) FROM posts r WHERE r.parent_id = p.id AND r.is_deleted = 0) as reply_count,
               (SELECT COUNT(*) FROM posts rp WHERE rp.repost_id = p.id) as repost_count
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.is_deleted = 0
          AND p.parent_id IS NULL
          AND p.created_at > datetime('now', '-24 hours')
        ORDER BY like_count DESC
        LIMIT 20
    ''').fetchall()

    enriched = [enrich_post(p, db, g.user) for p in trending_posts]

    # Who to follow suggestions
    suggestions = []
    if g.user:
        suggestions = db.execute('''
            SELECT u.*, (SELECT COUNT(*) FROM follows WHERE following_id = u.id) as follower_count
            FROM users u
            WHERE u.id != ?
              AND u.id NOT IN (SELECT following_id FROM follows WHERE follower_id = ?)
              AND u.is_suspended = 0
            ORDER BY follower_count DESC
            LIMIT 5
        ''', (g.user['id'], g.user['id'])).fetchall()

    return render_template('feed/explore.html',
                           trending_tags=trending_tags,
                           posts=enriched,
                           suggestions=suggestions)


# ── Search ───────────────────────────────────────────────────────────

@feed_bp.route('/search')
def search():
    query = request.args.get('q', '').strip()
    search_type = request.args.get('type', 'posts')

    if not query:
        return render_template('feed/search.html', results=[], query='', search_type=search_type)

    db = g.db
    results = []

    if search_type == 'users':
        results = db.execute('''
            SELECT u.*, (SELECT COUNT(*) FROM follows WHERE following_id = u.id) as follower_count
            FROM users u
            WHERE (u.username LIKE ? OR u.display_name LIKE ?)
              AND u.is_suspended = 0
            ORDER BY follower_count DESC
            LIMIT 50
        ''', (f'%{query}%', f'%{query}%')).fetchall()

    elif search_type == 'hashtags':
        results = db.execute('''
            SELECT h.*, COUNT(ph.post_id) as recent_count
            FROM hashtags h
            LEFT JOIN post_hashtags ph ON h.id = ph.hashtag_id
            WHERE h.tag LIKE ?
            GROUP BY h.id
            ORDER BY recent_count DESC
            LIMIT 50
        ''', (f'%{query}%',)).fetchall()

    else:  # posts
        results = db.execute('''
            SELECT p.*, u.username, u.display_name, u.profile_pic, u.is_verified,
                   (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as like_count,
                   (SELECT COUNT(*) FROM posts r WHERE r.parent_id = p.id AND r.is_deleted = 0) as reply_count,
                   (SELECT COUNT(*) FROM posts rp WHERE rp.repost_id = p.id) as repost_count
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.content LIKE ?
              AND p.is_deleted = 0
            ORDER BY p.created_at DESC
            LIMIT 50
        ''', (f'%{query}%',)).fetchall()
        results = [enrich_post(p, db, g.user) for p in results]

    return render_template('feed/search.html', results=results, query=query,
                           search_type=search_type)


# ── Hashtag Page ─────────────────────────────────────────────────────

@feed_bp.route('/hashtag/<tag>')
def hashtag(tag):
    db = g.db
    posts = db.execute('''
        SELECT p.*, u.username, u.display_name, u.profile_pic, u.is_verified,
               (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as like_count,
               (SELECT COUNT(*) FROM posts r WHERE r.parent_id = p.id AND r.is_deleted = 0) as reply_count,
               (SELECT COUNT(*) FROM posts rp WHERE rp.repost_id = p.id) as repost_count
        FROM posts p
        JOIN users u ON p.user_id = u.id
        JOIN post_hashtags ph ON p.id = ph.post_id
        JOIN hashtags h ON ph.hashtag_id = h.id
        WHERE h.tag = ? AND p.is_deleted = 0
        ORDER BY p.created_at DESC
        LIMIT 50
    ''', (tag,)).fetchall()

    enriched = [enrich_post(p, db, g.user) for p in posts]
    return render_template('feed/hashtag.html', tag=tag, posts=enriched)


# ── Bookmarks ────────────────────────────────────────────────────────

@feed_bp.route('/bookmarks')
def bookmarks():
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    posts = db.execute('''
        SELECT p.*, u.username, u.display_name, u.profile_pic, u.is_verified,
               (SELECT COUNT(*) FROM likes WHERE post_id = p.id) as like_count,
               (SELECT COUNT(*) FROM posts r WHERE r.parent_id = p.id AND r.is_deleted = 0) as reply_count,
               (SELECT COUNT(*) FROM posts rp WHERE rp.repost_id = p.id) as repost_count
        FROM bookmarks b
        JOIN posts p ON b.post_id = p.id
        JOIN users u ON p.user_id = u.id
        WHERE b.user_id = ? AND p.is_deleted = 0
        ORDER BY b.created_at DESC
        LIMIT 50
    ''', (g.user['id'],)).fetchall()

    enriched = [enrich_post(p, db, g.user) for p in posts]
    return render_template('feed/bookmarks.html', posts=enriched)


# ── Dismiss Announcement ────────────────────────────────────────────

@feed_bp.route('/announcement/<int:ann_id>/dismiss', methods=['POST'])
def dismiss_announcement(ann_id):
    if not g.user:
        return redirect(url_for('auth.login'))

    db = g.db
    db.execute(
        'INSERT OR IGNORE INTO announcement_dismissals (announcement_id, user_id) VALUES (?, ?)',
        (ann_id, g.user['id'])
    )
    db.commit()
    return redirect(request.referrer or url_for('feed.home'))
