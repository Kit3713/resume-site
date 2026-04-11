from flask import request, g


def track_page_view():
    """Log page view to database. Registered as before_request handler."""
    if request.method != 'GET':
        return
    path = request.path
    if path.startswith(('/static/', '/admin', '/photos/', '/favicon')):
        return

    try:
        from app import get_db
        db = get_db()
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if client_ip and ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()
        db.execute(
            'INSERT INTO page_views (path, referrer, user_agent, ip_address) VALUES (?, ?, ?, ?)',
            (path, request.referrer or '', request.user_agent.string, client_ip or ''),
        )
        db.commit()
    except Exception:
        pass
