/* Chirp - Client-side JavaScript */

// Auto-dismiss flash messages after 5 seconds
document.addEventListener('DOMContentLoaded', () => {
    const flashes = document.querySelectorAll('.snackbar');
    flashes.forEach(flash => {
        setTimeout(() => {
            flash.style.opacity = '0';
            flash.style.transform = 'translateY(20px)';
            setTimeout(() => flash.remove(), 300);
        }, 5000);
    });

    // Auto-resize textareas
    document.querySelectorAll('textarea').forEach(textarea => {
        textarea.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = this.scrollHeight + 'px';
        });
    });
});

// Share post
function sharePost(postId) {
    const url = window.location.origin + '/post/' + postId;
    if (navigator.share) {
        navigator.share({ title: 'Chirp', url: url });
    } else if (navigator.clipboard) {
        navigator.clipboard.writeText(url).then(() => {
            showSnackbar('Link copied to clipboard!');
        });
    }
}

// Show snackbar notification
function showSnackbar(message) {
    const container = document.querySelector('.flash-messages') ||
        (() => {
            const div = document.createElement('div');
            div.className = 'flash-messages';
            document.body.appendChild(div);
            return div;
        })();

    const snackbar = document.createElement('div');
    snackbar.className = 'snackbar';
    snackbar.innerHTML = `<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg> ${message}`;
    snackbar.onclick = () => snackbar.remove();
    container.appendChild(snackbar);

    setTimeout(() => {
        snackbar.style.opacity = '0';
        setTimeout(() => snackbar.remove(), 300);
    }, 3000);
}

// Poll notification count updates (every 30s)
function updateNotificationCount() {
    fetch('/notifications/count')
        .then(r => r.json())
        .then(data => {
            const badges = document.querySelectorAll('.nav-item .badge');
            // Update sidebar badge
            const notifLink = document.querySelector('a[href*="notifications"] .badge');
            if (data.count > 0) {
                if (notifLink) {
                    notifLink.textContent = data.count;
                }
            } else if (notifLink) {
                notifLink.style.display = 'none';
            }
        })
        .catch(() => {});
}

// Start polling for notifications
setInterval(updateNotificationCount, 30000);

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    // Don't handle shortcuts when typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    switch(e.key) {
        case 'n':
            e.preventDefault();
            window.location.href = '/compose';
            break;
        case '/':
            e.preventDefault();
            window.location.href = '/search';
            break;
    }
});
