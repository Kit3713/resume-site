import os

from flask import current_app, send_from_directory, abort


def serve_photo(storage_name):
    """Serve a photo file from the configured storage directory."""
    photo_dir = current_app.config['PHOTO_STORAGE']
    if not os.path.isabs(photo_dir):
        photo_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            photo_dir,
        )
    file_path = os.path.join(photo_dir, storage_name)
    if not os.path.exists(file_path):
        abort(404)
    return send_from_directory(photo_dir, storage_name)
