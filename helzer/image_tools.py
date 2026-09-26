from __future__ import annotations

import os
import tempfile
from typing import Any

from google.genai import types

IMAGE_MODEL = os.getenv("HELZER_IMAGE_MODEL", "gemini-3.1-flash-image")
ALLOWED_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"}
ALLOWED_SIZES = {"512", "1K", "2K", "4K"}

def image_tool_specs() -> list[dict[str, Any]]:
    return [{
        "type": "function",
        "name": "generate_image",
        "description": (
            "Generate a polished professional image from a design brief. Use for logos, branding, "
            "posters, banners, social graphics, illustrations, icons, stickers, product visuals, "
            "thumbnails, and other visual design work. Act like a senior graphic designer and art "
            "director: improve composition, hierarchy, typography, spacing, lighting, materials, "
            "color harmony, and visual consistency. Turn natural language into strong art direction."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Desired visual/design brief."},
                "aspect_ratio": {"type": "string", "description": "1:1, 16:9, 9:16, 4:5, 3:2, etc."},
                "image_size": {"type": "string", "description": "512, 1K, 2K, or 4K."},
            },
            "required": ["prompt"],
        },
    }]

def _designer_prompt(prompt: str) -> str:
    return f"""Act as a senior graphic designer and art director.
Create the requested visual from this brief:

{prompt}

Design intelligently rather than literally. Establish a clear focal point, strong composition,
professional visual hierarchy, intentional spacing, coherent color/material choices, polished
lighting and depth, and a finished presentation-ready look. For branding, make the mark clean,
scalable and memorable. For requested text, reproduce it accurately with suitable typography.
Avoid clutter, generic stock-art appearance, accidental watermarks, malformed text, and weak
composition. Treat this as a professional design deliverable."""

async def execute_image_tool(message, args: dict[str, Any], gemini_client) -> dict[str, Any]:
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("An image description is required.")
    ratio = str(args.get("aspect_ratio", "1:1")).strip()
    size = str(args.get("image_size", "1K")).strip().upper()
    if ratio not in ALLOWED_RATIOS:
        ratio = "1:1"
    if size not in ALLOWED_SIZES:
        size = "1K"

    response = await gemini_client.aio.models.generate_content(
        model=IMAGE_MODEL,
        contents=_designer_prompt(prompt),
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            response_format={"image": {"aspect_ratio": ratio, "image_size": size}},
        ),
    )
    image = None
    for part in getattr(response, "parts", []) or []:
        if getattr(part, "inline_data", None):
            image = part.as_image()
            if image is not None:
                break
    if image is None:
        raise ValueError("The image model returned no image. Check HELZER_IMAGE_MODEL and Gemini API access.")

    fd, path = tempfile.mkstemp(prefix="helzer_generated_", suffix=".png")
    os.close(fd)
    image.save(path, format="PNG")
    return {
        "ok": True,
        "action": "generate_image",
        "path": path,
        "filename": "helzer-generated.png",
        "aspect_ratio": ratio,
        "image_size": size,
    }
