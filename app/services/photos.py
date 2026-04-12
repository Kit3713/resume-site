"""
Photo Processing and Serving Service

Handles two responsibilities:
1. Upload processing: Saves uploaded images, generates optimized versions
   using Pillow (resizing images larger than 2000px), and returns metadata.
2. File serving: Serves photo files from the configured storage directory
   via Flask's send_from_directory for proper caching and content types.

Photos are stored with UUID-based filenames to avoid collisions and path
traversal issues. The original upload filename is preserved in the database
for reference but never used for file system access.

Storage layout:
  photos/
    <uuid>.jpg       — Optimized image (resized if > 2000px)
    <uuid>.png       — PNG images are kept as PNG but optimized
    <uuid>.webp      — WebP images optimized at 85% quality
"""

import os
import uuid

from PIL import Image
from flask import current_app, send_from_directory, abort


def process_upload(file_storage):
    """Process an uploaded photo: save to disk and optimize for web display.

    The upload workflow:
    1. Validate the file extension (jpg, png, gif, webp only).
    2. Generate a UUID-based storage filename to prevent collisions.
    3. Save the original file to the photo storage directory.
    4. If the image exceeds 2000px on any dimension, resize it down
       using Lanczos resampling (highest quality downscale algorithm).
    5. Return metadata dict for database insertion.

    Args:
        file_storage: A Werkzeug FileStorage object from request.files.

    Returns:
        dict: Photo metadata (storage_name, filename, mime_type, width,
              height, file_size), or None if the file type is invalid.
    """
    filename = file_storage.filename or 'upload.jpg'
    ext = os.path.splitext(filename)[1].lower() or '.jpg'

    # Whitelist of allowed image extensions
    if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
        return None

    # Generate a unique storage filename (UUID prevents collisions and path traversal)
    storage_name = f"{uuid.uuid4().hex}{ext}"

    # Resolve the photo storage directory (absolute or relative to project root)
    photo_dir = current_app.config['PHOTO_STORAGE']
    if not os.path.isabs(photo_dir):
        photo_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            photo_dir,
        )
    os.makedirs(photo_dir, exist_ok=True)

    file_path = os.path.join(photo_dir, storage_name)

    # Save the uploaded file to disk
    file_storage.save(file_path)
    file_size = os.path.getsize(file_path)

    # Optimize the image with Pillow
    try:
        with Image.open(file_path) as img:
            width, height = img.size

            # Downscale oversized images while maintaining aspect ratio
            max_dim = 2000
            if width > max_dim or height > max_dim:
                img.thumbnail((max_dim, max_dim), Image.LANCZOS)

                # Re-save with format-specific optimization
                if ext in ('.jpg', '.jpeg'):
                    img.save(file_path, 'JPEG', quality=85, optimize=True)
                elif ext == '.png':
                    img.save(file_path, 'PNG', optimize=True)
                elif ext == '.webp':
                    img.save(file_path, 'WebP', quality=85)
                else:
                    img.save(file_path)

                # Update dimensions and file size after optimization
                width, height = img.size
                file_size = os.path.getsize(file_path)
    except Exception:
        # If Pillow can't process the image, keep the original as-is
        width, height = None, None

    # Map file extensions to MIME types
    mime_map = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
        '.gif': 'image/gif', '.webp': 'image/webp',
    }

    return {
        'storage_name': storage_name,
        'filename': filename,
        'mime_type': mime_map.get(ext, 'image/jpeg'),
        'width': width,
        'height': height,
        'file_size': file_size,
    }


def serve_photo(storage_name):
    """Serve a photo file from the configured storage directory.

    Uses Flask's send_from_directory for proper Content-Type headers,
    cache control, and security (prevents path traversal).

    Args:
        storage_name: The UUID-based filename stored in the photos table.

    Returns:
        Response: The file response, or aborts with 404 if not found.
    """
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


def delete_photo_file(storage_name):
    """Delete a photo file from the storage directory.

    Called by the admin photo delete route after removing the database record.
    Silently handles the case where the file doesn't exist (already deleted
    or never successfully saved).

    Args:
        storage_name: The UUID-based filename to delete.
    """
    photo_dir = current_app.config['PHOTO_STORAGE']
    if not os.path.isabs(photo_dir):
        photo_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            photo_dir,
        )
    file_path = os.path.join(photo_dir, storage_name)
    if os.path.exists(file_path):
        os.remove(file_path)
