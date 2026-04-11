from flask import Blueprint, render_template, request, redirect, url_for, flash

from app import get_db
from app.models import create_review, mark_token_used
from app.services.tokens import validate_token

review_bp = Blueprint('review', __name__, template_folder='../templates')


@review_bp.route('/review/<token>', methods=['GET', 'POST'])
def review_form(token):
    db = get_db()
    token_row, error = validate_token(db, token)

    if error:
        return render_template('public/review.html', error=error, token_data=token_row)

    if request.method == 'POST':
        # Re-validate to prevent race condition
        token_row, error = validate_token(db, token)
        if error:
            return render_template('public/review.html', error=error, token_data=token_row)

        reviewer_name = request.form.get('reviewer_name', '').strip()
        reviewer_title = request.form.get('reviewer_title', '').strip()
        relationship = request.form.get('relationship', '').strip()
        message = request.form.get('message', '').strip()
        rating_str = request.form.get('rating', '')
        rating = int(rating_str) if rating_str and rating_str.isdigit() and 1 <= int(rating_str) <= 5 else None

        if not reviewer_name or not message:
            flash('Name and message are required.', 'error')
            return render_template('public/review.html', error=None, token_data=token_row)

        create_review(
            db,
            token_id=token_row['id'],
            reviewer_name=reviewer_name,
            reviewer_title=reviewer_title,
            relationship=relationship,
            message=message,
            rating=rating,
            review_type=token_row['type'],
        )
        mark_token_used(db, token_row['id'])

        flash('Thank you! Your review has been submitted for approval.', 'success')
        return redirect(url_for('public.index'))

    return render_template('public/review.html', error=None, token_data=token_row)
