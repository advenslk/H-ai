from __future__ import annotations

import io
import re
from typing import Any

import discord
from google.genai import types
from PIL import Image

MAX_EMOJI_BYTES = 256 * 1024
MAX_EMOJI_SIDE = 128
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}

def emoji_tool_specs() -> list[dict[str, Any]]:
    return [{
        "type": "function",
        "name": "add_custom_emoji",
        "description": (
            "Create a custom emoji in the current Discord server from an attached image. "
            "Use this when the user explicitly asks to add emojis from an uploaded image/file. "
            "The image is identified by attachment_index (0-based). If the file contains one emoji, "
            "use the full image. If it is an emoji sheet, use normalized crop coordinates x,y,width,height "
            "from 0 to 1000 to isolate one emoji. Choose a short lowercase snake_case Discord emoji name "
            "that matches the user's code/context when possible."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "attachment_index": {
                    "type": "integer",
                    "description": "0-based index of the image attachment in the current Discord message."
                },
                "name": {
                    "type": "string",
                    "description": "Short Discord custom emoji name, preferably lowercase snake_case."
                },
                "x": {"type": "integer", "description": "Normalized crop left coordinate, 0-1000. Omit for full image."},
                "y": {"type": "integer", "description": "Normalized crop top coordinate, 0-1000. Omit for full image."},
                "width": {"type": "integer", "description": "Normalized crop width, 0-1000. Omit for full image."},
                "height": {"type": "integer", "description": "Normalized crop height, 0-1000. Omit for full image."},
            },
            "required": ["attachment_index", "name"],
        },
    }]

def _clean_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value).strip().lower()).strip("_")
    return (name or "helzer_emoji")[:32]

def _crop_image(data: bytes, mime_type: str, args: dict[str, Any]) -> bytes:
    if mime_type == "image/gif" and any(k in args for k in ("x", "y", "width", "height")):
        raise ValueError("GIF emoji cropping is not supported yet. Upload each GIF emoji as a separate file.")

    try:
        image = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise ValueError("The attachment is not a valid image.") from exc

    animated = getattr(image, "is_animated", False)
    if animated and mime_type == "image/gif":
        return data

    image = image.convert("RGBA")
    has_crop = any(k in args for k in ("x", "y", "width", "height"))
    if has_crop:
        x = max(0, min(1000, int(args.get("x", 0))))
        y = max(0, min(1000, int(args.get("y", 0))))
        w = max(1, min(1000 - x, int(args.get("width", 1000 - x))))
        h = max(1, min(1000 - y, int(args.get("height", 1000 - y))))
        left = round(image.width * x / 1000)
        top = round(image.height * y / 1000)
        right = max(left + 1, round(image.width * (x + w) / 1000))
        bottom = max(top + 1, round(image.height * (y + h) / 1000))
        image = image.crop((left, top, right, bottom))

    image.thumbnail((MAX_EMOJI_SIDE, MAX_EMOJI_SIDE), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    result = output.getvalue()

    if len(result) > MAX_EMOJI_BYTES:
        image = image.convert("P", palette=Image.Palette.ADAPTIVE, colors=128)
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        result = output.getvalue()

    if len(result) > MAX_EMOJI_BYTES:
        raise ValueError("The processed emoji image is still larger than Discord's upload limit.")

    return result

async def attachment_parts(attachments) -> list[Any]:
    parts: list[Any] = []
    for index, attachment in enumerate(attachments or []):
        mime = (getattr(attachment, "content_type", None) or "").split(";")[0].lower()
        if mime not in IMAGE_MIME_TYPES:
            continue
        data = await attachment.read()
        parts.append(types.Part.from_text(text=f"Attachment {index}: {getattr(attachment, 'filename', 'image')}"))
        parts.append(types.Part.from_bytes(data=data, mime_type=mime))
    return parts

async def execute_emoji_tool(message, args: dict[str, Any]) -> dict[str, Any]:
    guild = getattr(message, "guild", None)
    if guild is None:
        raise ValueError("Custom emojis can only be added inside a Discord server.")

    me = guild.me
    if me is None:
        raise ValueError("The bot member is unavailable in this server.")
    if not me.guild_permissions.manage_emojis_and_stickers:
        raise ValueError("The bot needs Manage Expressions permission to add custom emojis.")

    attachments = list(getattr(message, "attachments", []) or [])
    index = int(args.get("attachment_index", -1))
    if index < 0 or index >= len(attachments):
        raise ValueError("The requested attachment index is not available.")

    attachment = attachments[index]
    mime = (getattr(attachment, "content_type", None) or "").split(";")[0].lower()
    if mime not in IMAGE_MIME_TYPES:
        raise ValueError("That attachment is not a supported image. Use PNG, JPG, WEBP, or GIF.")

    data = await attachment.read()
    processed = _crop_image(data, mime, args)
    name = _clean_name(args.get("name", "helzer_emoji"))

    try:
        emoji = await guild.create_custom_emoji(
            name=name,
            image=processed,
            reason="Added by Helzer at the user's request",
        )
    except discord.HTTPException as exc:
        raise ValueError(
            f"Discord rejected the emoji upload (HTTP {exc.status}, code {getattr(exc, 'code', 'unknown')}). "
            "The server may have reached its emoji limit or the image may be invalid."
        ) from exc

    return {
        "ok": True,
        "action": "add_custom_emoji",
        "name": emoji.name,
        "id": str(emoji.id),
        "mention": str(emoji),
        "attachment_index": index,
    }
