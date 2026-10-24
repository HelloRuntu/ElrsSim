from pathlib import Path
from typing import Tuple


def make_image(size: int = 64):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = max(1, size // 32)
    draw.ellipse(
        (margin, margin, size - margin - 1, size - margin - 1),
        fill=(16, 22, 34, 255),
        outline=(0, 200, 255, 255),
        width=max(1, size // 24),
    )

    center = size // 2
    arm = int(size * 0.24)
    rotor = int(size * 0.11)
    line_width = max(2, size // 20)
    draw.line((center - arm, center, center + arm, center), fill=(0, 200, 255, 255), width=line_width)
    draw.line((center, center - arm, center, center + arm), fill=(0, 200, 255, 255), width=line_width)

    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        cx = center + dx * arm
        cy = center + dy * arm
        draw.ellipse(
            (cx - rotor, cy - rotor, cx + rotor, cy + rotor),
            outline=(235, 245, 255, 255),
            width=max(1, size // 28),
        )

    body = int(size * 0.09)
    draw.ellipse(
        (center - body, center - body, center + body, center + body),
        fill=(0, 200, 255, 255),
    )
    return image


def save_icon(path: str, size: int = 256) -> Path:
    icon_path = Path(path)
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    image = make_image(size)
    sizes: Tuple[Tuple[int, int], ...] = (
        (16, 16),
        (24, 24),
        (32, 32),
        (48, 48),
        (64, 64),
        (128, 128),
        (256, 256),
    )
    image.save(icon_path, format="ICO", sizes=sizes)
    return icon_path
