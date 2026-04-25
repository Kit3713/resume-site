"""
Integration Tests — Phase 7.5

End-to-end flows that span multiple routes and verify the system works
as a whole, not just individual endpoints:
- Full review flow: generate token → visit link → submit → admin approves → public display
- Full contact flow: submit form → saved to DB → appears in admin dashboard
- Settings changes reflect immediately in public templates
- Sitemap includes active pages and excludes hidden content
- File upload validation (magic bytes, size limits, null bytes)
- Session timeout enforcement
"""

import io
from datetime import UTC

# ============================================================
# REVIEW FLOW (token → submit → approve → display)
# ============================================================


def test_full_review_flow(auth_client, app):
    """End-to-end: generate token → submit review → approve → appears on testimonials."""
    # Step 1: Admin generates a token
    response = auth_client.post(
        '/admin/tokens/generate',
        data={
            'name': 'Integration Tester',
            'type': 'recommendation',
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    # Step 2: Retrieve the generated token from the database
    with app.app_context():
        from app.db import get_db

        db = get_db()
        token_row = db.execute(
            "SELECT token FROM review_tokens WHERE name = 'Integration Tester'"
        ).fetchone()
        assert token_row is not None
        token_string = token_row['token']

    # Step 3: Visit the review form (public client, not auth)
    public_client = app.test_client()
    response = public_client.get(f'/review/{token_string}')
    assert response.status_code == 200
    assert b'Leave a Recommendation' in response.data

    # Step 4: Submit the review
    response = public_client.post(
        f'/review/{token_string}',
        data={
            'reviewer_name': 'Integration Tester',
            'reviewer_title': 'QA Engineer',
            'relationship': 'Colleague',
            'message': 'This is an integration test review.',
            'rating': '5',
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    # Step 5: Verify the token is marked as used
    with app.app_context():
        db = get_db()
        token_row = db.execute(
            "SELECT used FROM review_tokens WHERE name = 'Integration Tester'"
        ).fetchone()
        assert token_row['used'] == 1

    # Step 6: Verify the review is pending
    with app.app_context():
        db = get_db()
        review = db.execute(
            "SELECT * FROM reviews WHERE reviewer_name = 'Integration Tester'"
        ).fetchone()
        assert review is not None
        assert review['status'] == 'pending'
        assert review['rating'] == 5
        review_id = review['id']

    # Step 7: Admin approves as featured
    response = auth_client.post(
        f'/admin/reviews/{review_id}/update',
        data={
            'action': 'approve',
            'display_tier': 'featured',
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    # Step 8: Verify the review appears on the public testimonials page
    response = public_client.get('/testimonials')
    assert response.status_code == 200
    assert b'Integration Tester' in response.data
    assert b'This is an integration test review.' in response.data


def test_used_token_cannot_resubmit(auth_client, app):
    """A used token should show 'Already Submitted' and not accept new reviews."""
    # Generate and use a token
    auth_client.post(
        '/admin/tokens/generate',
        data={
            'name': 'One-Time User',
            'type': 'recommendation',
        },
    )

    with app.app_context():
        from app.db import get_db

        db = get_db()
        token_row = db.execute(
            "SELECT token FROM review_tokens WHERE name = 'One-Time User'"
        ).fetchone()
        token_string = token_row['token']

    public_client = app.test_client()
    public_client.post(
        f'/review/{token_string}',
        data={
            'reviewer_name': 'One-Time User',
            'message': 'First submission.',
        },
    )

    # Try to resubmit with the same token
    response = public_client.get(f'/review/{token_string}')
    assert b'Already Submitted' in response.data


def test_review_token_concurrent_submission_rejected(auth_client, app):
    """Phase 27.2 (#26): two concurrent POSTs of the same token must
    not both produce a review. Atomic BEGIN IMMEDIATE + re-validate
    inside the transaction ensures exactly one wins.

    This test runs the two submissions sequentially (test-client
    concurrency is cooperative, not real threads) but the contract
    is still observable: the first POST creates a row and marks the
    token used; the second POST re-validates inside its own
    transaction, sees the token is used, and rolls back without
    creating a second review row.
    """
    auth_client.post(
        '/admin/tokens/generate',
        data={'name': 'Racing User', 'type': 'client_review'},
    )

    with app.app_context():
        from app.db import get_db

        db = get_db()
        token_row = db.execute(
            "SELECT token FROM review_tokens WHERE name = 'Racing User'"
        ).fetchone()
        token_string = token_row['token']

    client_a = app.test_client()
    client_b = app.test_client()
    payload = {'reviewer_name': 'A', 'message': 'First!'}

    resp_a = client_a.post(f'/review/{token_string}', data=payload)
    resp_b = client_b.post(
        f'/review/{token_string}', data={'reviewer_name': 'B', 'message': 'Second?'}
    )

    # A submits successfully (redirect to thank-you or 200 with confirmation).
    assert resp_a.status_code in (200, 302)
    # B must NOT produce a second review. Whether the page shows
    # "Already Submitted" (200) or redirects differently, the DB
    # must have exactly one review for this token.
    assert resp_b.status_code == 200

    with app.app_context():
        db = get_db()
        count = db.execute(
            'SELECT COUNT(*) FROM reviews WHERE token_id = '
            "(SELECT id FROM review_tokens WHERE name = 'Racing User')"
        ).fetchone()[0]
        assert count == 1, f'expected exactly 1 review, got {count}'


# ============================================================
# CONTACT FLOW (submit → DB → admin dashboard)
# ============================================================


def test_full_contact_flow(client, auth_client, app):
    """End-to-end: submit contact form → saved to DB → shows in admin dashboard."""
    # Step 1: Submit the contact form
    response = client.post(
        '/contact',
        data={
            'name': 'Jane Doe',
            'email': 'jane@example.com',
            'message': 'Integration test contact message.',
            'website': '',  # Empty honeypot
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    # Step 2: Verify it was saved to the database
    with app.app_context():
        from app.db import get_db

        db = get_db()
        submission = db.execute(
            "SELECT * FROM contact_submissions WHERE email = 'jane@example.com'"
        ).fetchone()
        assert submission is not None
        assert submission['name'] == 'Jane Doe'
        assert submission['is_spam'] == 0

    # Step 3: Verify it shows in the admin dashboard
    response = auth_client.get('/admin/')
    assert response.status_code == 200
    assert b'Jane Doe' in response.data


def test_honeypot_flags_spam(client, app):
    """Contact submissions with a filled honeypot should be flagged as spam."""
    client.post(
        '/contact',
        data={
            'name': 'Bot',
            'email': 'bot@spam.com',
            'message': 'Buy cheap stuff!',
            'website': 'http://spam.com',  # Honeypot filled = spam
        },
        follow_redirects=True,
    )

    with app.app_context():
        from app.db import get_db

        db = get_db()
        submission = db.execute(
            "SELECT * FROM contact_submissions WHERE email = 'bot@spam.com'"
        ).fetchone()
        assert submission is not None
        assert submission['is_spam'] == 1


# ============================================================
# SETTINGS REFLECT IN PUBLIC TEMPLATES
# ============================================================


def test_settings_changes_reflect_in_templates(auth_client, app):
    """Changing site_title in admin settings should immediately show on the public site."""
    # Change the site title
    auth_client.post(
        '/admin/settings',
        data={
            'site_title': 'My Custom Portfolio',
        },
        follow_redirects=False,
    )

    # Check the public landing page reflects the change
    public_client = app.test_client()
    response = public_client.get('/')
    assert b'My Custom Portfolio' in response.data


# ============================================================
# SITEMAP
# ============================================================


def test_sitemap_includes_standard_pages(client):
    """Sitemap should include all standard public pages."""
    response = client.get('/sitemap.xml')
    assert response.status_code == 200
    assert response.content_type == 'application/xml'

    data = response.data.decode()
    for path in [
        '/',
        '/portfolio',
        '/services',
        '/projects',
        '/testimonials',
        '/certifications',
        '/contact',
    ]:
        assert path in data


def test_sitemap_excludes_admin(client):
    """Sitemap must not include admin routes."""
    response = client.get('/sitemap.xml')
    data = response.data.decode()
    assert '/admin' not in data


# ============================================================
# FILE UPLOAD VALIDATION
# ============================================================


def _make_jpeg_bytes():
    """Create minimal valid JPEG bytes."""
    return b'\xff\xd8\xff\xe0' + b'\x00' * 100


def _make_png_bytes():
    """Create a valid PNG using Pillow."""
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new('RGB', (10, 10), color='red')
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf.read()


def test_upload_rejects_exe_disguised_as_jpg(auth_client):
    """An executable disguised with a .jpg extension should be rejected (magic bytes mismatch)."""
    fake_jpg = io.BytesIO(b'MZ\x90\x00' + b'\x00' * 100)  # PE/EXE magic bytes
    response = auth_client.post(
        '/admin/photos/upload',
        data={
            'photo': (fake_jpg, 'malware.jpg'),
            'title': 'Evil',
        },
        content_type='multipart/form-data',
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b'does not match' in response.data


def test_upload_rejects_null_byte_filename(auth_client):
    """Filenames containing null bytes must be rejected."""
    valid_png = io.BytesIO(_make_png_bytes())
    response = auth_client.post(
        '/admin/photos/upload',
        data={
            'photo': (valid_png, 'image\x00.php.png'),
            'title': 'Null byte',
        },
        content_type='multipart/form-data',
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b'Invalid filename' in response.data


def test_upload_accepts_valid_png(auth_client, app):
    """A valid PNG file should be accepted and saved."""
    valid_png = io.BytesIO(_make_png_bytes())
    response = auth_client.post(
        '/admin/photos/upload',
        data={
            'photo': (valid_png, 'test_photo.png'),
            'title': 'Valid Photo',
            'display_tier': 'grid',
        },
        content_type='multipart/form-data',
        follow_redirects=True,
    )
    assert response.status_code == 200
    # Should show success flash, not an error
    assert b'uploaded successfully' in response.data


# ============================================================
# SESSION TIMEOUT
# ============================================================


def test_session_timeout_redirects_to_login(auth_client):
    """An expired session should redirect to the login page."""
    from datetime import datetime, timedelta

    # Backdate last_activity on the canonical authenticated session so the
    # timeout guard fires. ``auth_client`` already seeds _user_id / _fresh.
    with auth_client.session_transaction() as sess:
        old_time = datetime.now(UTC) - timedelta(hours=2)
        sess['_last_activity'] = old_time.isoformat()

    response = auth_client.get('/admin/', follow_redirects=False)
    assert response.status_code == 302
    assert '/admin/login' in response.headers['Location']


def test_active_session_not_expired(auth_client):
    """A recently active session should not be expired."""
    response = auth_client.get('/admin/')
    assert response.status_code == 200
