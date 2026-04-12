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

from flask import Blueprint, render_template, request, redirect, url_for, flash

from app import get_db
from app.models import create_review, mark_token_used
from app.services.tokens import validate_token

review_bp = Blueprint('review', __name__, template_folder='../templates')


@review_bp.route('/review/<token>', methods=['GET', 'POST'])
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
        rating = int(rating_str) if rating_str and rating_str.isdigit() and 1 <= int(rating_str) <= 5 else None

        # Validate required fields
        if not reviewer_name or not message:
            flash('Name and message are required.', 'error')
            return render_template('public/review.html', error=None, token_data=token_row)

        # Create the review with 'pending' status (awaiting admin approval)
        create_review(
            db,
            token_id=token_row['id'],
            reviewer_name=reviewer_name,
            reviewer_title=reviewer_title,
            relationship=relationship,
            message=message,
            rating=rating,
            review_type=token_row['type'],  # Inherited from the token ('recommendation' or 'client_review')
        )

        # Mark the token as used so it cannot be resubmitted
        mark_token_used(db, token_row['id'])

        flash('Thank you! Your review has been submitted for approval.', 'success')
        return redirect(url_for('public.index'))

    # GET request — render the form with token data for pre-filling
    return render_template('public/review.html', error=None, token_data=token_row)
