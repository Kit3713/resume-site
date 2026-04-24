"""
Token-Based Review Submission Route

Handles the invite-only testimonial system. The admin generates a unique
token for each trusted contact, who can then submit a review at
/review/<token>. Submitted reviews are saved with 'pending' status and
must be approved by the admin before appearing on the public site.

Flow:
1. Admin generates token (via admin panel or CLI).
2. Token URL is shared with the contact.
3. Contact visits /review/<token>, sees a form pre-filled with their name.
4. Contact submits their review with optional star rating.
5. Token is marked as used (single-use, cannot be resubmitted).
6. Review appears in the admin panel for approval.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _

from app import limiter
from app.db import get_db
from app.events import Events, emit
from app.models import create_review, mark_token_used
from app.services.tokens import validate_token


class _TokenRaceError(Exception):
    """Raised inside the review-submit transaction when a concurrent
    writer invalidated the token between our initial check and BEGIN.

    Phase 27.2 (#26) — using an exception is the cleanest way to exit
    a ``with db:`` context while still rolling back; a plain ``return``
    inside the block would commit, which is the opposite of what we
    want when we've detected a losing race.
    """

    def __init__(self, error, token_row):
        super().__init__(error)
        self.error = error
        self.token_row = token_row


review_bp = Blueprint('review', __name__, template_folder='../templates')


@review_bp.route('/review/<token>', methods=['GET', 'POST'])
@limiter.limit('5 per minute', methods=['POST'])
def review_form(token):
    """Handle the review submission form.

    GET: Validates the token and renders the review form (or an error
         message for invalid/used/expired tokens).
    POST: Re-validates the token to prevent race conditions, validates
          form data, creates the review, marks the token as used, and
          redirects to the landing page with a success message.
    """
    db = get_db()
    token_row, error = validate_token(db, token)

    # Show appropriate error for invalid, used, or expired tokens
    if error:
        return render_template('public/review.html', error=error, token_data=token_row)

    if request.method == 'POST':
        # Re-validate token to prevent race conditions (double-submission)
        token_row, error = validate_token(db, token)
        if error:
            return render_template('public/review.html', error=error, token_data=token_row)

        # Extract and sanitize form fields
        reviewer_name = request.form.get('reviewer_name', '').strip()
        reviewer_title = request.form.get('reviewer_title', '').strip()
        relationship = request.form.get('relationship', '').strip()
        message = request.form.get('message', '').strip()

        # Parse optional star rating (1-5 integer, or None)
        rating_str = request.form.get('rating', '')
        rating = (
            int(rating_str)
            if rating_str and rating_str.isdigit() and 1 <= int(rating_str) <= 5
            else None
        )

        # Validate required fields
        if not reviewer_name or not message:
            flash(_('Name and message are required.'), 'error')
            return render_template('public/review.html', error=None, token_data=token_row)

        # Phase 27.2 (#26) — atomic review + token-use update.
        # Before this, ``create_review`` and ``mark_token_used`` were
        # two separate statements without a transaction. Two
        # concurrent POSTs of the same token raced each other: both
        # observed "token valid", both called create_review, both
        # called mark_token_used. The result was two reviews for one
        # invitation token. Explicit BEGIN IMMEDIATE / COMMIT /
        # ROLLBACK is used rather than ``with db:`` because
        # ``app.db._InstrumentedConnection`` wraps the raw sqlite3
        # connection and does not surface its context-manager
        # protocol.
        try:
            db.execute('BEGIN IMMEDIATE')
            try:
                # Re-validate inside the transaction — a concurrent
                # writer that landed between our initial check and
                # the BEGIN is now visible.
                token_row, error = validate_token(db, token)
                if error:
                    raise _TokenRaceError(error, token_row)

                review_id = create_review(
                    db,
                    token_id=token_row['id'],
                    reviewer_name=reviewer_name,
                    reviewer_title=reviewer_title,
                    relationship=relationship,
                    message=message,
                    rating=rating,
                    review_type=token_row['type'],  # 'recommendation' or 'client_review'
                )
                mark_token_used(db, token_row['id'])
                db.commit()
            except Exception:
                db.rollback()
                raise
        except _TokenRaceError as race:
            return render_template(
                'public/review.html', error=race.error, token_data=race.token_row
            )

        # Phase 19.1 event bus — fire `review.submitted` so subscribers
        # (admin email notifier, future webhook delivery) can react. The
        # status is implicitly 'pending' — admin still has to approve via
        # the review manager before the review goes public.
        emit(
            Events.REVIEW_SUBMITTED,
            review_id=review_id,
            token_id=token_row['id'],
            review_type=token_row['type'],
            has_rating=rating is not None,
            source='public_token',
        )

        flash(_('Thank you! Your review has been submitted for approval.'), 'success')
        return redirect(url_for('public.index'))

    # GET request — render the form with token data for pre-filling
    return render_template('public/review.html', error=None, token_data=token_row)
