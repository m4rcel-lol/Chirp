"""Tests for Chirp application - critical functions."""
import os
import sys
import json
import tempfile
import unittest

# Add app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app'))

os.environ['DATABASE_PATH'] = ':memory:'
os.environ['SECRET_KEY'] = 'test-secret-key'


class ChirpTestCase(unittest.TestCase):
    """Base test case with app setup."""

    def setUp(self):
        import database
        database._memory_db = None  # Reset shared in-memory DB

        from main import app
        from database import init_db, get_db

        self.app = app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

        with self.app.app_context():
            init_db()

    def _get_csrf_token(self, response):
        """Extract CSRF token from response."""
        data = response.data.decode()
        idx = data.find('name="csrf_token" value="')
        if idx == -1:
            return ''
        start = idx + len('name="csrf_token" value="')
        end = data.find('"', start)
        return data[start:end]

    def _register_user(self, username='testuser', email='test@test.com',
                       password='password123'):
        """Register a test user and return the session client."""
        # Get CSRF token
        resp = self.client.get('/register')
        csrf = self._get_csrf_token(resp)

        return self.client.post('/register', data={
            'csrf_token': csrf,
            'username': username,
            'email': email,
            'password': password,
            'confirm_password': password,
            'display_name': username.title(),
        }, follow_redirects=True)

    def _login_user(self, login_id='testuser', password='password123'):
        """Login a test user."""
        resp = self.client.get('/login')
        csrf = self._get_csrf_token(resp)

        return self.client.post('/login', data={
            'csrf_token': csrf,
            'login': login_id,
            'password': password,
        }, follow_redirects=True)

    def _get_csrf_from_page(self, path='/home'):
        """Get a CSRF token from any page."""
        resp = self.client.get(path)
        return self._get_csrf_token(resp)


class TestDatabaseInit(ChirpTestCase):
    """Test database initialization."""

    def test_tables_created(self):
        """All required tables should be created."""
        with self.app.app_context():
            from database import get_db
            db = get_db()
            tables = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t['name'] for t in tables]

            required = ['users', 'posts', 'follows', 'likes', 'bookmarks',
                        'notifications', 'community_notes', 'staff_notes',
                        'announcements', 'reports', 'sessions', 'hashtags',
                        'conversations', 'messages', 'site_settings',
                        'audit_log', 'polls', 'blocks', 'mutes']
            for table in required:
                self.assertIn(table, table_names, f"Missing table: {table}")

    def test_default_settings(self):
        """Default site settings should be inserted."""
        with self.app.app_context():
            from database import get_db
            db = get_db()
            setting = db.execute(
                "SELECT value FROM site_settings WHERE key = 'site_name'"
            ).fetchone()
            self.assertIsNotNone(setting)
            self.assertEqual(setting['value'], 'Chirp')


class TestAuth(ChirpTestCase):
    """Test authentication system."""

    def test_login_page_renders(self):
        resp = self.client.get('/login')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Welcome back', resp.data)

    def test_register_page_renders(self):
        resp = self.client.get('/register')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Create your account', resp.data)

    def test_registration_success(self):
        resp = self._register_user()
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Welcome to Chirp', resp.data)

    def test_registration_duplicate_username(self):
        self._register_user()
        # Logout
        csrf = self._get_csrf_from_page()
        self.client.post('/logout', data={'csrf_token': csrf})
        # Try to register same username
        resp = self._register_user()
        self.assertIn(b'already taken', resp.data)

    def test_registration_short_password(self):
        resp = self.client.get('/register')
        csrf = self._get_csrf_token(resp)
        resp = self.client.post('/register', data={
            'csrf_token': csrf,
            'username': 'newuser',
            'email': 'new@test.com',
            'password': 'short',
            'confirm_password': 'short',
        }, follow_redirects=True)
        self.assertIn(b'at least 8 characters', resp.data)

    def test_login_success(self):
        self._register_user()
        csrf = self._get_csrf_from_page()
        self.client.post('/logout', data={'csrf_token': csrf})
        resp = self._login_user()
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Welcome back', resp.data)

    def test_login_wrong_password(self):
        self._register_user()
        csrf = self._get_csrf_from_page()
        self.client.post('/logout', data={'csrf_token': csrf})
        resp = self._login_user(password='wrongpassword')
        self.assertIn(b'Invalid', resp.data)

    def test_profile_page(self):
        self._register_user()
        resp = self.client.get('/user/testuser')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Testuser', resp.data)

    def test_profile_404(self):
        resp = self.client.get('/user/nonexistent')
        self.assertEqual(resp.status_code, 404)


class TestPosts(ChirpTestCase):
    """Test post/chirp system."""

    def test_compose_page(self):
        self._register_user()
        resp = self.client.get('/compose')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'New Chirp', resp.data)

    def test_create_post(self):
        self._register_user()
        csrf = self._get_csrf_from_page('/compose')
        resp = self.client.post('/compose', data={
            'csrf_token': csrf,
            'content': 'Hello world! #test',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Chirp posted', resp.data)

    def test_create_post_too_long(self):
        self._register_user()
        csrf = self._get_csrf_from_page('/compose')
        resp = self.client.post('/compose', data={
            'csrf_token': csrf,
            'content': 'x' * 501,
        }, follow_redirects=True)
        self.assertIn(b'1-500 characters', resp.data)

    def test_view_post(self):
        self._register_user()
        csrf = self._get_csrf_from_page('/compose')
        self.client.post('/compose', data={
            'csrf_token': csrf,
            'content': 'Test post content',
        }, follow_redirects=True)
        resp = self.client.get('/post/1')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Test post content', resp.data)

    def test_like_post(self):
        self._register_user()
        csrf = self._get_csrf_from_page('/compose')
        self.client.post('/compose', data={
            'csrf_token': csrf,
            'content': 'Likeable post',
        }, follow_redirects=True)

        csrf = self._get_csrf_from_page()
        resp = self.client.post('/post/1/like', data={
            'csrf_token': csrf,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

    def test_bookmark_post(self):
        self._register_user()
        csrf = self._get_csrf_from_page('/compose')
        self.client.post('/compose', data={
            'csrf_token': csrf,
            'content': 'Bookmarkable post',
        }, follow_redirects=True)

        csrf = self._get_csrf_from_page()
        resp = self.client.post('/post/1/bookmark', data={
            'csrf_token': csrf,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

    def test_delete_post(self):
        self._register_user()
        csrf = self._get_csrf_from_page('/compose')
        self.client.post('/compose', data={
            'csrf_token': csrf,
            'content': 'Post to delete',
        }, follow_redirects=True)

        csrf = self._get_csrf_from_page()
        resp = self.client.post('/post/1/delete', data={
            'csrf_token': csrf,
        }, follow_redirects=True)
        self.assertIn(b'Post deleted', resp.data)

    def test_reply_to_post(self):
        self._register_user()
        csrf = self._get_csrf_from_page('/compose')
        self.client.post('/compose', data={
            'csrf_token': csrf,
            'content': 'Original post',
        }, follow_redirects=True)

        csrf = self._get_csrf_from_page('/post/1')
        resp = self.client.post('/post/1/reply', data={
            'csrf_token': csrf,
            'content': 'This is a reply',
        }, follow_redirects=True)
        self.assertIn(b'Reply posted', resp.data)

    def test_hashtag_extraction(self):
        self._register_user()
        csrf = self._get_csrf_from_page('/compose')
        self.client.post('/compose', data={
            'csrf_token': csrf,
            'content': 'Testing #hashtags and #chirp',
        }, follow_redirects=True)

        with self.app.app_context():
            from database import get_db
            db = get_db()
            tags = db.execute('SELECT tag FROM hashtags').fetchall()
            tag_names = [t['tag'] for t in tags]
            self.assertIn('hashtags', tag_names)
            self.assertIn('chirp', tag_names)


class TestFeed(ChirpTestCase):
    """Test feed and discovery."""

    def test_home_feed(self):
        self._register_user()
        resp = self.client.get('/home')
        self.assertEqual(resp.status_code, 200)

    def test_explore_page(self):
        self._register_user()
        resp = self.client.get('/explore')
        self.assertEqual(resp.status_code, 200)

    def test_search_page(self):
        resp = self.client.get('/search')
        self.assertEqual(resp.status_code, 200)

    def test_search_posts(self):
        self._register_user()
        csrf = self._get_csrf_from_page('/compose')
        self.client.post('/compose', data={
            'csrf_token': csrf,
            'content': 'Searchable unique content xyz',
        }, follow_redirects=True)

        resp = self.client.get('/search?q=xyz&type=posts')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Searchable unique content', resp.data)

    def test_search_users(self):
        self._register_user()
        resp = self.client.get('/search?q=testuser&type=users')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'testuser', resp.data)

    def test_bookmarks_page(self):
        self._register_user()
        resp = self.client.get('/bookmarks')
        self.assertEqual(resp.status_code, 200)

    def test_hashtag_page(self):
        self._register_user()
        resp = self.client.get('/hashtag/test')
        self.assertEqual(resp.status_code, 200)


class TestFollow(ChirpTestCase):
    """Test follow/unfollow system."""

    def test_follow_user(self):
        self._register_user()
        # Create second user
        csrf = self._get_csrf_from_page()
        self.client.post('/logout', data={'csrf_token': csrf})

        self._register_user('user2', 'user2@test.com')

        csrf = self._get_csrf_from_page()
        resp = self.client.post('/follow/1', data={
            'csrf_token': csrf,
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Following', resp.data)


class TestSetup(ChirpTestCase):
    """Test setup wizard."""

    def test_setup_page_accessible(self):
        resp = self.client.get('/setup')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Create Admin Account', resp.data)

    def test_setup_creates_admin(self):
        resp = self.client.get('/setup')
        csrf = self._get_csrf_token(resp)

        resp = self.client.post('/setup', data={
            'csrf_token': csrf,
            'step': '1',
            'username': 'admin',
            'email': 'admin@test.com',
            'password': 'adminpass123',
        }, follow_redirects=True)
        self.assertIn(b'Step 2', resp.data)

    def test_setup_blocked_after_admin_exists(self):
        # Create admin via setup
        resp = self.client.get('/setup')
        csrf = self._get_csrf_token(resp)
        self.client.post('/setup', data={
            'csrf_token': csrf,
            'step': '1',
            'username': 'admin',
            'email': 'admin@test.com',
            'password': 'adminpass123',
        })

        # Try to access setup again
        resp = self.client.get('/setup', follow_redirects=True)
        self.assertIn(b'Setup already complete', resp.data)


class TestAPI(ChirpTestCase):
    """Test REST API endpoints."""

    def test_api_get_post(self):
        self._register_user()
        csrf = self._get_csrf_from_page('/compose')
        self.client.post('/compose', data={
            'csrf_token': csrf,
            'content': 'API test post',
        })

        resp = self.client.get('/api/v1/posts/1')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data['content'], 'API test post')

    def test_api_search(self):
        resp = self.client.get('/api/v1/search?q=test&type=users')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('results', data)

    def test_api_trending(self):
        resp = self.client.get('/api/v1/trending')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('trending', data)

    def test_api_post_not_found(self):
        resp = self.client.get('/api/v1/posts/9999')
        self.assertEqual(resp.status_code, 404)


class TestSecurity(ChirpTestCase):
    """Test security features."""

    def test_csrf_protection(self):
        """POST without CSRF token should be rejected."""
        resp = self.client.post('/login', data={
            'login': 'test',
            'password': 'test',
        })
        self.assertEqual(resp.status_code, 403)

    def test_security_headers(self):
        resp = self.client.get('/login')
        self.assertEqual(resp.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(resp.headers.get('X-Frame-Options'), 'DENY')
        self.assertEqual(resp.headers.get('X-XSS-Protection'), '1; mode=block')

    def test_xss_prevention(self):
        """HTML in user input should be sanitized."""
        self._register_user()
        csrf = self._get_csrf_from_page('/compose')
        self.client.post('/compose', data={
            'csrf_token': csrf,
            'content': '<script>alert("xss")</script>Hello',
        }, follow_redirects=True)

        resp = self.client.get('/post/1')
        self.assertNotIn(b'<script>', resp.data)

    def test_login_required_redirect(self):
        """Accessing protected page without login should redirect."""
        resp = self.client.get('/home')
        self.assertEqual(resp.status_code, 302)


class TestNotifications(ChirpTestCase):
    """Test notification system."""

    def test_notifications_page(self):
        self._register_user()
        resp = self.client.get('/notifications/')
        self.assertEqual(resp.status_code, 200)

    def test_notification_count_api(self):
        self._register_user()
        resp = self.client.get('/notifications/count')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn('count', data)


class TestMessages(ChirpTestCase):
    """Test direct messaging."""

    def test_inbox_page(self):
        self._register_user()
        resp = self.client.get('/messages/')
        self.assertEqual(resp.status_code, 200)

    def test_new_message_page(self):
        self._register_user()
        resp = self.client.get('/messages/new')
        self.assertEqual(resp.status_code, 200)


class TestErrorPages(ChirpTestCase):
    """Test error handling."""

    def test_404_page(self):
        resp = self.client.get('/nonexistent-page')
        self.assertEqual(resp.status_code, 404)

    def test_api_404(self):
        resp = self.client.get('/api/v1/nonexistent')
        self.assertEqual(resp.status_code, 404)
        data = json.loads(resp.data)
        self.assertIn('error', data)


if __name__ == '__main__':
    unittest.main()
