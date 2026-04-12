"""
Photo Processing and Serving Service

Handles two responsibilities:
1. Upload processing: Validates, saves, and optimizes uploaded images
   using Pillow (resizing images larger than 2000px).
2. File serving: Serves photo files from the configured storage directory
   via Flask's send_from_directory for proper caching and content types.

Security:
- File extension whitelist (.jpg, .jpeg, .png, .gif, .webp only).
- Magic byte validation: verifies the file's actual content matches its
  claimed extension, preventing disguised executables.
- Null byte rejection: filenames containing null bytes are rejected to
  prevent null byte injection attacks.
- File size limit: enforced before writing to disk (configurable via
  max_upload_size in config.yaml, default 10 MB).
- UUID-based storage filenames prevent path traversal.

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


# Magic byte signatures for each allowed image format.
# These are the first N bytes of a valid file of that type.
_MAGIC_BYTES = {
    '.jpg':  [b'\xff\xd8\xff'],
    '.jpeg': [b'\xff\xd8\xff'],
    '.png':  [b'\x89PNG\r\n\x1a\n'],
    '.gif':  [b'GIF87a', b'GIF89a'],
    '.webp': [b'RIFF'],  # Full check: RIFF....WEBP (bytes 8-11 = "WEBP")
}

_DEFAULT_MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


def _get_photo_dir():
    """Resolve the photo storage directory to an absolute path."""
    photo_dir = current_app.config['PHOTO_STORAGE']
    if not os.path.isabs(photo_dir):
        photo_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            photo_dir,
        )
    return photo_dir


def _validate_magic_bytes(file_storage, ext):
    """Verify the file's magic bytes match its claimed extension.

    Reads the first 12 bytes without consuming the stream (seeks back
    to the start after reading). Returns True if the bytes match the
    expected signature for the given extension, False otherwise.
    """
    signatures = _MAGIC_BYTES.get(ext)
    if not signatures:
        return False

    header = file_storage.read(12)
    file_storage.seek(0)

    if not header:
        return False

    for sig in signatures:
        if header[:len(sig)] == sig:
            # Extra check for WebP: bytes 8-12 must be "WEBP"
            if ext == '.webp' and header[8:12] != b'WEBP':
                return False
            return True

    return False


def _check_file_size(file_storage):
    """Check the uploaded file's size against the configured limit.

    Seeks to the end to determine size, then seeks back to the start.
    Returns (size_bytes, error_message). error_message is None if OK.
    """
    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)

    max_size = current_app.config.get('MAX_UPLOAD_SIZE', _DEFAULT_MAX_UPLOAD_SIZE)
    if isinstance(max_size, str):
        max_size = int(max_size)

    if size > max_size:
        max_mb = max_size / (1024 * 1024)
        return size, f"File exceeds maximum upload size ({max_mb:.0f} MB)."

    return size, None


def process_upload(file_storage):
    """Process an uploaded photo: validate, save to disk, and optimize.

    The upload workflow:
    1. Reject filenames containing null bytes.
    2. Validate the file extension (jpg, png, gif, webp only).
    3. Verify magic bytes match the claimed extension.
    4. Check file size against the configured limit.
    5. Generate a UUID-based storage filename to prevent collisions.
    6. Save to the photo storage directory.
    7. If the image exceeds 2000px on any dimension, resize it down.
    8. Return metadata dict for database insertion.

    Args:
        file_storage: A Werkzeug FileStorage object from request.files.

    Returns:
        dict with keys: storage_name, filename, mime_type, width, height,
        file_size — on success.
        None — if the file type is invalid.
        str — if there's a specific error message (size limit, magic bytes).
    """
    filename = file_storage.filename or 'upload.jpg'

    # Reject filenames with null bytes (null byte injection attack)
    if '\x00' in filename:
        return 'Invalid filename.'

    ext = os.path.splitext(filename)[1].lower() or '.jpg'

    # Whitelist of allowed image extensions
    if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
        return None

    # Verify magic bytes match the claimed file type
    if not _validate_magic_bytes(file_storage, ext):
        return 'File content does not match its extension.'

    # Check file size before writing to disk
    reported_size, size_error = _check_file_size(file_storage)
    if size_error:
        return size_error

    # Generate a unique storage filename (UUID prevents collisions and path traversal)
    storage_name = f"{uuid.uuid4().hex}{ext}"

    photo_dir = _get_photo_dir()
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
    photo_dir = _get_photo_dir()
    if not os.path.exists(os.path.join(photo_dir, storage_name)):
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
    file_path = os.path.join(_get_photo_dir(), storage_name)
    if os.path.exists(file_path):
        os.remove(file_path)
