import os
import uuid

from PIL import Image
from flask import current_app


def process_upload(file_storage):
    """Process an uploaded photo: save original, generate optimized version.

    Args:
        file_storage: Werkzeug FileStorage object from the upload.

    Returns:
        dict with metadata: storage_name, filename, mime_type, width, height, file_size
        or None if processing fails.
    """
    filename = file_storage.filename or 'upload.jpg'
    ext = os.path.splitext(filename)[1].lower() or '.jpg'
    if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
        return None

    storage_name = f"{uuid.uuid4().hex}{ext}"
    photo_dir = current_app.config['PHOTO_STORAGE']
    if not os.path.isabs(photo_dir):
        photo_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            photo_dir,
        )
    os.makedirs(photo_dir, exist_ok=True)

    file_path = os.path.join(photo_dir, storage_name)

    # Save original
    file_storage.save(file_path)
    file_size = os.path.getsize(file_path)

    # Get dimensions and optimize
    try:
        with Image.open(file_path) as img:
            width, height = img.size

            # If image is larger than 2000px on any side, resize for web
            max_dim = 2000
            if width > max_dim or height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.LANCZOS)
                if ext in ('.jpg', '.jpeg'):
                    img.save(file_path, 'JPEG', quality=85, optimize=True)
                elif ext == '.png':
                    img.save(file_path, 'PNG', optimize=True)
                elif ext == '.webp':
                    img.save(file_path, 'WebP', quality=85)
                else:
                    img.save(file_path)
                width, height = img.size
                file_size = os.path.getsize(file_path)
    except Exception:
        # If Pillow can't process it, keep the original
        width, height = None, None

    mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                '.gif': 'image/gif', '.webp': 'image/webp'}

    return {
        'storage_name': storage_name,
        'filename': filename,
        'mime_type': mime_map.get(ext, 'image/jpeg'),
        'width': width,
        'height': height,
        'file_size': file_size,
    }


def delete_photo_file(storage_name):
    """Delete a photo file from storage."""
    photo_dir = current_app.config['PHOTO_STORAGE']
    if not os.path.isabs(photo_dir):
        photo_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            photo_dir,
        )
    file_path = os.path.join(photo_dir, storage_name)
    if os.path.exists(file_path):
        os.remove(file_path)
