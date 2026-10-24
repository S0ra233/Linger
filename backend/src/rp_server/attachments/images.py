"""模型图片转换使用 Pillow；限制沿用自有 Harness。"""
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_EDGE = 1280
MAX_REQUEST_IMAGES = 8
MAX_REQUEST_BYTES = 20 * 1024 * 1024


def prepare_image(source: bytes) -> tuple[bytes, int, int]:
    if not source or len(source) > MAX_SOURCE_BYTES:
        raise ValueError("图片不能为空或超过 20 MiB。")
    try:
        with Image.open(BytesIO(source)) as original:
            if original.format not in {"PNG", "JPEG", "GIF", "WEBP"}:
                raise ValueError("只接收实际为 PNG、JPEG、GIF 或 WebP 的图片。")
            if original.width * original.height > MAX_PIXELS:
                raise ValueError("图片超过 2400 万像素。")
            original.seek(0)
            image = ImageOps.exif_transpose(original).convert("RGBA" if "A" in original.getbands() or "transparency" in original.info else "RGB")
            image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            with BytesIO() as output:
                image.save(output, format="PNG")
                prepared = output.getvalue()
            width, height = image.size
            image.close()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("图片无法解码或尺寸过大。") from exc
    if len(prepared) > MAX_IMAGE_BYTES:
        raise ValueError("处理后的图片超过 8 MiB。")
    return prepared, width, height
