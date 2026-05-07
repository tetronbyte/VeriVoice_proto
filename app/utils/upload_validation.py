"""Upload validation for audio files."""

from fastapi import HTTPException, UploadFile

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB

ALLOWED_CONTENT_TYPES = {
    "audio/wav", "audio/x-wav", "audio/wave",
    "audio/mpeg", "audio/mp3", "audio/ogg",
    "audio/flac", "audio/webm",
    "application/octet-stream",  # fallback for unknown
}

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".webm", ".m4a"}


async def validate_audio_upload(upload: UploadFile) -> bytes:
    """Read and validate an audio upload. Returns raw bytes.

    Checks:
      - File size ≤ 10 MB
      - Content-type or extension is a recognised audio format
    """
    raw = await upload.read()
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Audio file too large ({len(raw)} bytes); maximum is {MAX_AUDIO_BYTES}",
        )

    # Accept if content-type matches OR if extension matches
    ct_ok = upload.content_type and upload.content_type in ALLOWED_CONTENT_TYPES
    ext = ""
    if upload.filename and "." in upload.filename:
        ext = "." + upload.filename.rsplit(".", 1)[-1].lower()
    ext_ok = ext in ALLOWED_EXTENSIONS

    if not ct_ok and not ext_ok:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported audio format: content_type={upload.content_type!r}, filename={upload.filename!r}",
        )
    return raw
