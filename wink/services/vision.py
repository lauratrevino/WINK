"""Claude-vision helpers: describe photos/images, slide images and video frames.

Used by services/documents.py so WINK can actually *see* uploaded images,
PowerPoint pictures and videos instead of only OCR-ing text. Every function
fails soft (returns "" or a short placeholder) so a vision problem never
breaks an upload.
"""
import base64
import io
import logging
import os
import subprocess
import tempfile

from .. import config
from ..errors import log_error

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageOps
    _PIL = True
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except Exception:  # HEIC just won't open; other formats unaffected
        pass
except ImportError:
    _PIL = False

_IMAGE_PROMPT = (
    "You are helping a student's study assistant read an uploaded image. "
    "First transcribe ALL readable text exactly (headings, bullets, tables, "
    "handwriting, labels), then describe what the image shows (diagrams, "
    "charts, photos, equations, people or objects relevant to learning). "
    "Be factual and complete; do not guess at things you cannot see."
)


def _client():
    from ..extensions import anthropic_client
    return anthropic_client


def vision_available():
    return bool(_PIL and _client())


def _prepare_image(img):
    """PIL image -> base64 JPEG/PNG that fits the API's size limits."""
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, "white")
        try:
            bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
            img = bg
        except Exception:
            img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    img.thumbnail((config.VISION_MAX_EDGE_PX, config.VISION_MAX_EDGE_PX))
    quality = 88
    while True:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        if buf.tell() <= config.VISION_MAX_IMAGE_BYTES or quality <= 40:
            return base64.standard_b64encode(buf.getvalue()).decode()
        quality -= 12


def _image_block(img):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": _prepare_image(img)}}


def _ask(content, max_tokens=2000):
    resp = _client().messages.create(
        model=config.VISION_MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def describe_pil_image(img, label="image"):
    if not vision_available():
        return ""
    try:
        return _ask([_image_block(img), {"type": "text", "text": _IMAGE_PROMPT}])
    except Exception as e:
        log_error("services.vision.describe_image", e, label=label)
        return ""


def describe_image_bytes(data, label="image"):
    if not _PIL:
        return ""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            if img.size[0] * img.size[1] < 5000:  # icons/bullets, not content
                return ""
            return describe_pil_image(img, label)
    except Exception as e:
        log_error("services.vision.open_image_bytes", e, label=label)
        return ""


def describe_image_file(path, label="image"):
    if not _PIL:
        return ""
    try:
        with Image.open(path) as img:
            img.load()
            return describe_pil_image(img, label)
    except Exception as e:
        log_error("services.vision.open_image_file", e, label=label)
        return ""


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def describe_video_file(path, label="video"):
    """Sample frames with ffmpeg and describe them in one vision call.
    Audio is not transcribed."""
    summary = f"[Video file: {label}]"
    if not vision_available():
        return summary
    try:
        probe = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", path], 60)
        duration = float(probe.stdout.strip() or 0)
    except Exception as e:
        log_error("services.vision.ffprobe", e, label=label)
        return summary + " (could not read this video)"
    n = max(1, min(config.VIDEO_MAX_FRAMES, int(duration // 5) + 1))
    stamps = [duration * (i + 0.5) / n for i in range(n)] if duration > 0 else [0]
    blocks, used = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(stamps):
            out = os.path.join(tmp, f"f{i}.jpg")
            try:
                _run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                      "-vf", f"scale='min({config.VISION_MAX_EDGE_PX},iw)':-2", "-y", out], 120)
                if os.path.exists(out):
                    with Image.open(out) as im:
                        im.load()
                        blocks.append({"type": "text", "text": f"Frame at {int(t//60)}:{int(t%60):02d}"})
                        blocks.append(_image_block(im))
                        used.append(t)
            except Exception as e:
                log_error("services.vision.ffmpeg_frame", e, label=label, t=t)
    if not blocks:
        return summary + " (no frames could be read)"
    blocks.append({"type": "text", "text": (
        f"These are {len(used)} evenly spaced frames from a {int(duration//60)}:{int(duration%60):02d} "
        "video a student uploaded. Transcribe any on-screen text (slides, captions, code, "
        "equations) and describe what happens, in order, with approximate timestamps. "
        "Be factual and complete.")})
    try:
        body = _ask(blocks, max_tokens=3000)
    except Exception as e:
        log_error("services.vision.describe_video", e, label=label)
        return summary + " (could not be analyzed)"
    return (f"{summary} ({int(duration//60)}:{int(duration%60):02d} long; described from "
            f"{len(used)} sampled frames. Spoken audio is not transcribed.)\n{body}")
