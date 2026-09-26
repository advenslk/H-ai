from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import PurePosixPath
from typing import Any

import discord
from google.genai import types
from PIL import Image

MAX_EMOJI_BYTES = 256 * 1024
MAX_EMOJI_SIDE = 128
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}

# Archive/source inspection limits prevent ZIP bombs and accidental secret exposure.
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 500
MAX_FILE_BYTES = 512 * 1024
MAX_CONTEXT_CHARS = 80_000
MAX_ARCHIVE_IMAGE_BYTES = 8 * 1024 * 1024
MAX_SELECTED_EMOJIS = 5000
MAX_GEMINI_EMOJI_PREVIEWS = 24
SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".md", ".txt", ".html", ".css", ".scss", ".java", ".kt",
    ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".sql", ".sh", ".bat",
}
SECRET_NAMES = {".env", ".env.local", ".env.production", ".env.development"}
SECRET_PATTERNS = ("token", "password", "passwd", "secret", "api_key", "apikey", "private_key")

def emoji_tool_specs() -> list[dict[str, Any]]:
    return _emoji_tool_specs()


def _emoji_tool_specs() -> list[dict[str, Any]]:
    return [{
        "type": "function",
        "name": "list_custom_emojis",
        "description": "List every custom emoji currently available in the current Discord server, including name, ID, animated status, and mention.",
        "parameters": {"type": "object", "properties": {}},
    }, {
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
                "attachment_index": {"type": "integer", "description": "0-based attachment index."},
                "name": {"type": "string", "description": "Short lowercase snake_case emoji name."},
                "x": {"type": "integer", "description": "Crop left, normalized 0-1000. Omit for full image."},
                "y": {"type": "integer", "description": "Crop top, normalized 0-1000. Omit for full image."},
                "width": {"type": "integer", "description": "Crop width, normalized 0-1000. Omit for full image."},
                "height": {"type": "integer", "description": "Crop height, normalized 0-1000. Omit for full image."},
                "archive_path": {"type": "string", "description": "Image path inside an uploaded ZIP. Use when the emoji asset is stored in the archive."},
            },
            "required": ["name"],
        },
    }, {
        "type": "function",
        "name": "remove_custom_emojis",
        "description": (
            "Delete custom emojis from the current Discord server. Use all=true when the user asks "
            "to remove/delete every or all server emoji, including natural-language and multilingual requests. "
            "Perform one bulk removal operation with all=true rather than calling this tool separately for each emoji. "
            "Use names or emoji_ids for specific emojis. The bot needs Manage Expressions permission. "
            "This is a destructive server action and should only be executed after the user's confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "all": {"type": "boolean", "description": "Remove every custom emoji in the current server."},
                "names": {"type": "array", "items": {"type": "string"}, "description": "Exact custom emoji names to remove."},
                "emoji_ids": {"type": "array", "items": {"type": "string"}, "description": "Exact Discord custom emoji IDs to remove."},
                "reason": {"type": "string", "description": "Optional audit-log reason."},
            },
        },
    }]

async def execute_list_emojis_tool(message) -> dict[str, Any]:
    """Return the current server's custom emoji inventory."""
    guild = getattr(message, "guild", None)
    if guild is None:
        raise ValueError("Custom emojis can only be listed inside a Discord server.")
    emojis = list(getattr(guild, "emojis", []) or [])
    return {
        "ok": True,
        "action": "list_custom_emojis",
        "count": len(emojis),
        "emojis": [
            {"id": str(emoji.id), "name": emoji.name, "animated": bool(emoji.animated), "mention": str(emoji)}
            for emoji in emojis
        ],
    }


async def execute_remove_emojis_tool(message, args: dict[str, Any]) -> dict[str, Any]:
    """Delete selected custom emojis from the current server."""
    guild = getattr(message, "guild", None)
    if guild is None:
        raise ValueError("Custom emojis can only be removed inside a Discord server.")

    all_emojis = list(getattr(guild, "emojis", []) or [])
    remove_all = bool(args.get("all", False))
    names = {str(name).strip().casefold() for name in (args.get("names") or []) if str(name).strip()}
    emoji_ids = {str(value).strip() for value in (args.get("emoji_ids") or []) if str(value).strip()}

    if not remove_all and not names and not emoji_ids:
        raise ValueError("Specify all=true, emoji names, or emoji IDs to remove.")

    selected = all_emojis if remove_all else [
        emoji for emoji in all_emojis
        if emoji.name.casefold() in names or str(emoji.id) in emoji_ids
    ]
    if not selected:
        return {"ok": True, "action": "remove_custom_emojis", "removed": 0, "not_found": sorted(names | emoji_ids)}

    me = getattr(guild, "me", None)
    if me is not None and not me.guild_permissions.manage_emojis_and_stickers:
        raise ValueError("The bot needs Manage Expressions permission to remove custom emojis.")

    removed, failed = [], []
    for emoji in selected:
        try:
            await emoji.delete(reason=args.get("reason") or "Removed by Helzer at the user's request")
            removed.append({"id": str(emoji.id), "name": emoji.name})
        except (discord.Forbidden, discord.HTTPException) as exc:
            failed.append({"id": str(emoji.id), "name": emoji.name, "error": str(exc)})

    return {
        "ok": not failed,
        "action": "remove_custom_emojis",
        "removed": len(removed),
        "removed_emojis": removed,
        "failed": len(failed),
        "failed_emojis": failed,
    }


def _clean_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value).strip().lower()).strip("_")
    return (name or "helzer_emoji")[:32]

def _crop_image(data: bytes, mime_type: str, args: dict[str, Any]) -> bytes:
    if mime_type == "image/gif" and any(k in args for k in ("x", "y", "width", "height")):
        raise ValueError("GIF emoji cropping is not supported yet. Upload each GIF emoji separately.")
    try:
        image = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise ValueError("The attachment is not a valid image.") from exc
    if getattr(image, "is_animated", False) and mime_type == "image/gif":
        if len(data) > MAX_EMOJI_BYTES:
            raise ValueError("This GIF is larger than Discord's emoji upload limit.")
        return data
    image = image.convert("RGBA")
    if any(k in args for k in ("x", "y", "width", "height")):
        x = max(0, min(1000, int(args.get("x", 0))))
        y = max(0, min(1000, int(args.get("y", 0))))
        w = max(1, min(1000 - x, int(args.get("width", 1000 - x))))
        h = max(1, min(1000 - y, int(args.get("height", 1000 - y))))
        left, top = round(image.width*x/1000), round(image.height*y/1000)
        right = max(left+1, round(image.width*(x+w)/1000))
        bottom = max(top+1, round(image.height*(y+h)/1000))
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

def _safe_source_name(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        return False
    lower = path.name.lower()
    if lower in SECRET_NAMES or any(p in lower for p in SECRET_PATTERNS):
        return False
    return path.suffix.lower() in SOURCE_EXTENSIONS

def inspect_archive(data: bytes, filename: str) -> dict[str, Any]:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("That archive is too large. Maximum supported ZIP size is 100 MB.")
    if not filename.lower().endswith(".zip"):
        raise ValueError("Only .zip project archives are supported.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("The uploaded ZIP file is invalid or corrupted.") from exc

    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_FILES:
        raise ValueError(f"ZIP contains too many files. Maximum is {MAX_ARCHIVE_FILES}.")
    total = 0
    image_total = 0
    files: list[dict[str, str]] = []
    images: list[dict[str, Any]] = []
    seen_image_digests: set[str] = set()
    image_candidates = 0
    image_duplicates = 0
    context_parts: list[str] = []
    for info in infos:
        if info.is_dir():
            continue
        normalized = PurePosixPath(info.filename.replace("\\", "/"))
        suffix = normalized.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            if info.file_size <= MAX_ARCHIVE_IMAGE_BYTES and not normalized.is_absolute() and ".." not in normalized.parts:
                mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}[suffix]
                raw_image = archive.read(info)
                image_total += len(raw_image)
                if image_total > MAX_EXTRACTED_BYTES:
                    raise ValueError("ZIP image assets exceed the 512 MB inspection limit.")
                image_candidates += 1
                digest = _image_digest(raw_image)
                if digest in seen_image_digests:
                    image_duplicates += 1
                elif len(images) < MAX_SELECTED_EMOJIS:
                    seen_image_digests.add(digest)
                    images.append({"path": info.filename, "data": raw_image, "size": info.file_size, "mime": mime, "digest": digest})
                else:
                    seen_image_digests.add(digest)
            continue
        if not _safe_source_name(info.filename):
            continue
        if info.file_size > MAX_FILE_BYTES:
            continue
        total += info.file_size
        if total > MAX_EXTRACTED_BYTES:
            raise ValueError("ZIP expands beyond the 512 MB inspection limit.")
        raw = archive.read(info)
        text = raw.decode("utf-8", errors="replace")
        files.append({"path": info.filename, "content": text})
        context_parts.append(f"\n===== FILE: {info.filename} =====\n{text}")
        if sum(len(x) for x in context_parts) >= MAX_CONTEXT_CHARS:
            break

    return {
        "filename": filename,
        "files": files,
        "file_count": len(files),
        "images": images,
        "image_count": len(images),
        "image_duplicates_removed": image_duplicates,
        "image_candidates": image_candidates,
        "context": "".join(context_parts)[:MAX_CONTEXT_CHARS],
    }

def _image_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _select_unique_images(images: list[dict[str, Any]], limit: int = MAX_SELECTED_EMOJIS) -> list[dict[str, Any]]:
    """Keep unique image assets, capped for safe processing of huge ZIPs."""
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for image in images:
        digest = _image_digest(image["data"])
        if digest in seen:
            continue
        seen.add(digest)
        item = dict(image)
        item["digest"] = digest
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def archive_parts(attachments) -> list[Any]:
    parts: list[Any] = []
    for index, attachment in enumerate(attachments or []):
        filename = getattr(attachment, "filename", "") or ""
        if not filename.lower().endswith(".zip"):
            continue
        # This helper is called after Discord attachment bytes are fetched.
        data = getattr(attachment, "_helzer_bytes", None)
        if data is None:
            continue
        try:
            result = inspect_archive(data, filename)
        except ValueError as exc:
            parts.append(types.Part.from_text(text=f"ZIP attachment {index} could not be inspected: {exc}"))
            continue
        parts.append(types.Part.from_text(text=(
            f"ZIP attachment {index}: {filename}\n"
            f"Inspected {result['file_count']} source files. "
            "Secrets, binaries, unsafe paths, and oversized files were excluded.\n"
            + result["context"]
        )))
    return parts

async def attachment_parts(attachments) -> list[Any]:
    parts: list[Any] = []
    for index, attachment in enumerate(attachments or []):
        mime = (getattr(attachment, "content_type", None) or "").split(";")[0].lower()
        filename = getattr(attachment, "filename", "") or ""
        if mime in IMAGE_MIME_TYPES:
            data = await attachment.read()
            parts.append(types.Part.from_text(text=f"Attachment {index}: {filename or 'image'}"))
            parts.append(types.Part.from_bytes(data=data, mime_type=mime))
        elif filename.lower().endswith(".zip"):
            data = await attachment.read()
            try:
                result = inspect_archive(data, filename)
                parts.append(types.Part.from_text(text=(
                    f"ZIP attachment {index}: {filename}\n"
                    f"Inspected {result['file_count']} source files. Found {result['image_candidates']} image candidates; "
                    f"selected {result['image_count']} unique emoji assets and removed {result['image_duplicates_removed']} exact duplicates. "
                    "Only a small visual preview set is sent to the vision model for speed. "
                    "Use an exact selected asset path with archive_path when adding an emoji.\n"
                    + result["context"]
                    + "\nSELECTED EMOJI PATHS (up to 5000):\n"
                    + "\n".join(image["path"] for image in result["images"])
                )))
                for image in result["images"][:MAX_GEMINI_EMOJI_PREVIEWS]:
                    parts.append(types.Part.from_text(text=f"ZIP emoji preview: {image['path']}"))
                    parts.append(types.Part.from_bytes(data=image["data"], mime_type=image["mime"]))
            except ValueError as exc:
                parts.append(types.Part.from_text(text=f"ZIP attachment {index}: {exc}"))
    return parts

async def _resolve_emoji_source(message, args: dict[str, Any]) -> tuple[bytes, str, str]:
    attachments = list(getattr(message, "attachments", []) or [])
    archive_path = str(args.get("archive_path", "")).strip()
    if archive_path:
        for attachment in attachments:
            filename = getattr(attachment, "filename", "") or ""
            if not filename.lower().endswith(".zip"):
                continue
            result = inspect_archive(await attachment.read(), filename)
            for image in result["images"]:
                if image["path"] == archive_path:
                    return image["data"], image["mime"], f"{filename}:{archive_path}"
        raise ValueError(f"Image '{archive_path}' was not found inside the uploaded ZIP.")
    index = int(args.get("attachment_index", -1))
    if index < 0 or index >= len(attachments):
        raise ValueError("The requested attachment index is not available.")
    attachment = attachments[index]
    mime = (getattr(attachment, "content_type", None) or "").split(";")[0].lower()
    if mime not in IMAGE_MIME_TYPES:
        raise ValueError("That attachment is not a supported image. Use PNG, JPG, WEBP, or GIF.")
    return await attachment.read(), mime, getattr(attachment, "filename", "image")

async def execute_emoji_tool(message, args: dict[str, Any]) -> dict[str, Any]:
    guild = getattr(message, "guild", None)
    if guild is None:
        raise ValueError("Custom emojis can only be added inside a Discord server.")
    me = guild.me
    if me is None:
        raise ValueError("The bot member is unavailable in this server.")
    if not me.guild_permissions.manage_emojis_and_stickers:
        raise ValueError("The bot needs Manage Expressions permission to add custom emojis.")
    data, mime, source_name = await _resolve_emoji_source(message, args)
    processed = _crop_image(data, mime, args)
    name = _clean_name(args.get("name", "helzer_emoji"))

    existing = discord.utils.get(guild.emojis, name=name)
    if existing is not None:
        return {
            "ok": True,
            "action": "add_custom_emoji",
            "duplicate": True,
            "name": existing.name,
            "id": str(existing.id),
            "mention": str(existing),
            "source": source_name,
        }

    emoji_limit = getattr(guild, "emoji_limit", None)
    if emoji_limit is not None and len(guild.emojis) >= emoji_limit:
        raise ValueError(
            f"This server has reached its custom emoji capacity ({emoji_limit}). "
            "Free space or use a server with more emoji slots before adding another."
        )

    try:
        emoji = await guild.create_custom_emoji(
            name=name, image=processed, reason="Added by Helzer at the user's request"
        )
    except discord.HTTPException as exc:
        raise ValueError(
            f"Discord rejected the emoji upload (HTTP {exc.status}, code {getattr(exc, 'code', 'unknown')}). "
            "The server may have reached its emoji limit or the image may be invalid."
        ) from exc
    return {"ok": True, "action": "add_custom_emoji", "name": emoji.name, "id": str(emoji.id),
            "mention": str(emoji), "source": source_name}        "description": "Delete custom emojis from the current Discord server. Use all=true when the user asks to remove/delete every/all server emoji, including natural-language or multilingual requests. Perform one bulk removal operation with all=true rather than calling this tool separately for each emoji. Use names or emoji_ids for specific emojis. The bot needs Manage Expressions permission. This is a destructive server action and should only be executed after the user's confirmation.",

        "parameters": {
            "type": "object",
            "properties": {
                "attachment_index": {"type": "integer", "description": "0-based attachment index."},
                "name": {"type": "string", "description": "Short lowercase snake_case emoji name."},
                "x": {"type": "integer", "description": "Crop left, normalized 0-1000. Omit for full image."},
                "y": {"type": "integer", "description": "Crop top, normalized 0-1000. Omit for full image."},
                "width": {"type": "integer", "description": "Crop width, normalized 0-1000. Omit for full image."},
                "height": {"type": "integer", "description": "Crop height, normalized 0-1000. Omit for full image."},
                "archive_path": {"type": "string", "description": "Image path inside an uploaded ZIP. Use when the emoji asset is stored in the archive."},
            },
            "required": ["name"],
        },
    }, {
        "type": "function",
        "name": "remove_custom_emojis",
        "description": (
            "Delete custom emojis from the current Discord server. Use all=true when the user asks "
            "to remove/delete all server emojis. Use names or emoji_ids for specific emojis. This is "
            "a destructive server action and should only be executed after the user's confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "all": {"type": "boolean", "description": "Remove every custom emoji in the current server."},
                "names": {"type": "array", "items": {"type": "string"}, "description": "Exact custom emoji names to remove."},
                "emoji_ids": {"type": "array", "items": {"type": "string"}, "description": "Exact Discord custom emoji IDs to remove."},
                "reason": {"type": "string", "description": "Optional audit-log reason."},
            },
        },
    }]

async def execute_list_emojis_tool(message) -> dict[str, Any]:
    """Return the current server's custom emoji inventory."""
    guild = getattr(message, "guild", None)
    if guild is None:
        raise ValueError("Custom emojis can only be listed inside a Discord server.")
    emojis = list(getattr(guild, "emojis", []) or [])
    return {
        "ok": True,
        "action": "list_custom_emojis",
        "count": len(emojis),
        "emojis": [
            {"id": str(emoji.id), "name": emoji.name, "animated": bool(emoji.animated), "mention": str(emoji)}
            for emoji in emojis
        ],
    }


async def execute_remove_emojis_tool(message, args: dict[str, Any]) -> dict[str, Any]:
    """Delete selected custom emojis from the current server."""
    guild = getattr(message, "guild", None)
    if guild is None:
        raise ValueError("Custom emojis can only be removed inside a Discord server.")

    all_emojis = list(getattr(guild, "emojis", []) or [])
    remove_all = bool(args.get("all", False))
    names = {str(name).strip().casefold() for name in (args.get("names") or []) if str(name).strip()}
    emoji_ids = {str(value).strip() for value in (args.get("emoji_ids") or []) if str(value).strip()}

    if not remove_all and not names and not emoji_ids:
        raise ValueError("Specify all=true, emoji names, or emoji IDs to remove.")

    selected = all_emojis if remove_all else [
        emoji for emoji in all_emojis
        if emoji.name.casefold() in names or str(emoji.id) in emoji_ids
    ]
    if not selected:
        return {"ok": True, "action": "remove_custom_emojis", "removed": 0, "not_found": sorted(names | emoji_ids)}

    me = getattr(guild, "me", None)
    if me is not None and not me.guild_permissions.manage_emojis_and_stickers:
        raise ValueError("The bot needs Manage Expressions permission to remove custom emojis.")

    removed, failed = [], []
    for emoji in selected:
        try:
            await emoji.delete(reason=args.get("reason") or "Removed by Helzer at the user's request")
            removed.append({"id": str(emoji.id), "name": emoji.name})
        except (discord.Forbidden, discord.HTTPException) as exc:
            failed.append({"id": str(emoji.id), "name": emoji.name, "error": str(exc)})

    return {
        "ok": not failed,
        "action": "remove_custom_emojis",
        "removed": len(removed),
        "removed_emojis": removed,
        "failed": len(failed),
        "failed_emojis": failed,
    }


def _clean_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value).strip().lower()).strip("_")
    return (name or "helzer_emoji")[:32]

def _crop_image(data: bytes, mime_type: str, args: dict[str, Any]) -> bytes:
    if mime_type == "image/gif" and any(k in args for k in ("x", "y", "width", "height")):
        raise ValueError("GIF emoji cropping is not supported yet. Upload each GIF emoji separately.")
    try:
        image = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise ValueError("The attachment is not a valid image.") from exc
    if getattr(image, "is_animated", False) and mime_type == "image/gif":
        if len(data) > MAX_EMOJI_BYTES:
            raise ValueError("This GIF is larger than Discord's emoji upload limit.")
        return data
    image = image.convert("RGBA")
    if any(k in args for k in ("x", "y", "width", "height")):
        x = max(0, min(1000, int(args.get("x", 0))))
        y = max(0, min(1000, int(args.get("y", 0))))
        w = max(1, min(1000 - x, int(args.get("width", 1000 - x))))
        h = max(1, min(1000 - y, int(args.get("height", 1000 - y))))
        left, top = round(image.width*x/1000), round(image.height*y/1000)
        right = max(left+1, round(image.width*(x+w)/1000))
        bottom = max(top+1, round(image.height*(y+h)/1000))
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

def _safe_source_name(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        return False
    lower = path.name.lower()
    if lower in SECRET_NAMES or any(p in lower for p in SECRET_PATTERNS):
        return False
    return path.suffix.lower() in SOURCE_EXTENSIONS

def inspect_archive(data: bytes, filename: str) -> dict[str, Any]:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("That archive is too large. Maximum supported ZIP size is 100 MB.")
    if not filename.lower().endswith(".zip"):
        raise ValueError("Only .zip project archives are supported.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("The uploaded ZIP file is invalid or corrupted.") from exc

    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_FILES:
        raise ValueError(f"ZIP contains too many files. Maximum is {MAX_ARCHIVE_FILES}.")
    total = 0
    image_total = 0
    files: list[dict[str, str]] = []
    images: list[dict[str, Any]] = []
    seen_image_digests: set[str] = set()
    image_candidates = 0
    image_duplicates = 0
    context_parts: list[str] = []
    for info in infos:
        if info.is_dir():
            continue
        normalized = PurePosixPath(info.filename.replace("\\", "/"))
        suffix = normalized.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            if info.file_size <= MAX_ARCHIVE_IMAGE_BYTES and not normalized.is_absolute() and ".." not in normalized.parts:
                mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}[suffix]
                raw_image = archive.read(info)
                image_total += len(raw_image)
                if image_total > MAX_EXTRACTED_BYTES:
                    raise ValueError("ZIP image assets exceed the 512 MB inspection limit.")
                image_candidates += 1
                digest = _image_digest(raw_image)
                if digest in seen_image_digests:
                    image_duplicates += 1
                elif len(images) < MAX_SELECTED_EMOJIS:
                    seen_image_digests.add(digest)
                    images.append({"path": info.filename, "data": raw_image, "size": info.file_size, "mime": mime, "digest": digest})
                else:
                    seen_image_digests.add(digest)
            continue
        if not _safe_source_name(info.filename):
            continue
        if info.file_size > MAX_FILE_BYTES:
            continue
        total += info.file_size
        if total > MAX_EXTRACTED_BYTES:
            raise ValueError("ZIP expands beyond the 512 MB inspection limit.")
        raw = archive.read(info)
        text = raw.decode("utf-8", errors="replace")
        files.append({"path": info.filename, "content": text})
        context_parts.append(f"\n===== FILE: {info.filename} =====\n{text}")
        if sum(len(x) for x in context_parts) >= MAX_CONTEXT_CHARS:
            break

    return {
        "filename": filename,
        "files": files,
        "file_count": len(files),
        "images": images,
        "image_count": len(images),
        "image_duplicates_removed": image_duplicates,
        "image_candidates": image_candidates,
        "context": "".join(context_parts)[:MAX_CONTEXT_CHARS],
    }

def _image_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _select_unique_images(images: list[dict[str, Any]], limit: int = MAX_SELECTED_EMOJIS) -> list[dict[str, Any]]:
    """Keep unique image assets, capped for safe processing of huge ZIPs."""
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for image in images:
        digest = _image_digest(image["data"])
        if digest in seen:
            continue
        seen.add(digest)
        item = dict(image)
        item["digest"] = digest
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def archive_parts(attachments) -> list[Any]:
    parts: list[Any] = []
    for index, attachment in enumerate(attachments or []):
        filename = getattr(attachment, "filename", "") or ""
        if not filename.lower().endswith(".zip"):
            continue
        # This helper is called after Discord attachment bytes are fetched.
        data = getattr(attachment, "_helzer_bytes", None)
        if data is None:
            continue
        try:
            result = inspect_archive(data, filename)
        except ValueError as exc:
            parts.append(types.Part.from_text(text=f"ZIP attachment {index} could not be inspected: {exc}"))
            continue
        parts.append(types.Part.from_text(text=(
            f"ZIP attachment {index}: {filename}\n"
            f"Inspected {result['file_count']} source files. "
            "Secrets, binaries, unsafe paths, and oversized files were excluded.\n"
            + result["context"]
        )))
    return parts

async def attachment_parts(attachments) -> list[Any]:
    parts: list[Any] = []
    for index, attachment in enumerate(attachments or []):
        mime = (getattr(attachment, "content_type", None) or "").split(";")[0].lower()
        filename = getattr(attachment, "filename", "") or ""
        if mime in IMAGE_MIME_TYPES:
            data = await attachment.read()
            parts.append(types.Part.from_text(text=f"Attachment {index}: {filename or 'image'}"))
            parts.append(types.Part.from_bytes(data=data, mime_type=mime))
        elif filename.lower().endswith(".zip"):
            data = await attachment.read()
            try:
                result = inspect_archive(data, filename)
                parts.append(types.Part.from_text(text=(
                    f"ZIP attachment {index}: {filename}\n"
                    f"Inspected {result['file_count']} source files. Found {result['image_candidates']} image candidates; "
                    f"selected {result['image_count']} unique emoji assets and removed {result['image_duplicates_removed']} exact duplicates. "
                    "Only a small visual preview set is sent to the vision model for speed. "
                    "Use an exact selected asset path with archive_path when adding an emoji.\n"
                    + result["context"]
                    + "\nSELECTED EMOJI PATHS (up to 5000):\n"
                    + "\n".join(image["path"] for image in result["images"])
                )))
                for image in result["images"][:MAX_GEMINI_EMOJI_PREVIEWS]:
                    parts.append(types.Part.from_text(text=f"ZIP emoji preview: {image['path']}"))
                    parts.append(types.Part.from_bytes(data=image["data"], mime_type=image["mime"]))
            except ValueError as exc:
                parts.append(types.Part.from_text(text=f"ZIP attachment {index}: {exc}"))
    return parts

async def _resolve_emoji_source(message, args: dict[str, Any]) -> tuple[bytes, str, str]:
    attachments = list(getattr(message, "attachments", []) or [])
    archive_path = str(args.get("archive_path", "")).strip()
    if archive_path:
        for attachment in attachments:
            filename = getattr(attachment, "filename", "") or ""
            if not filename.lower().endswith(".zip"):
                continue
            result = inspect_archive(await attachment.read(), filename)
            for image in result["images"]:
                if image["path"] == archive_path:
                    return image["data"], image["mime"], f"{filename}:{archive_path}"
        raise ValueError(f"Image '{archive_path}' was not found inside the uploaded ZIP.")
    index = int(args.get("attachment_index", -1))
    if index < 0 or index >= len(attachments):
        raise ValueError("The requested attachment index is not available.")
    attachment = attachments[index]
    mime = (getattr(attachment, "content_type", None) or "").split(";")[0].lower()
    if mime not in IMAGE_MIME_TYPES:
        raise ValueError("That attachment is not a supported image. Use PNG, JPG, WEBP, or GIF.")
    return await attachment.read(), mime, getattr(attachment, "filename", "image")

async def execute_emoji_tool(message, args: dict[str, Any]) -> dict[str, Any]:
    guild = getattr(message, "guild", None)
    if guild is None:
        raise ValueError("Custom emojis can only be added inside a Discord server.")
    me = guild.me
    if me is None:
        raise ValueError("The bot member is unavailable in this server.")
    if not me.guild_permissions.manage_emojis_and_stickers:
        raise ValueError("The bot needs Manage Expressions permission to add custom emojis.")
    data, mime, source_name = await _resolve_emoji_source(message, args)
    processed = _crop_image(data, mime, args)
    name = _clean_name(args.get("name", "helzer_emoji"))

    existing = discord.utils.get(guild.emojis, name=name)
    if existing is not None:
        return {
            "ok": True,
            "action": "add_custom_emoji",
            "duplicate": True,
            "name": existing.name,
            "id": str(existing.id),
            "mention": str(existing),
            "source": source_name,
        }

    emoji_limit = getattr(guild, "emoji_limit", None)
    if emoji_limit is not None and len(guild.emojis) >= emoji_limit:
        raise ValueError(
            f"This server has reached its custom emoji capacity ({emoji_limit}). "
            "Free space or use a server with more emoji slots before adding another."
        )

    try:
        emoji = await guild.create_custom_emoji(
            name=name, image=processed, reason="Added by Helzer at the user's request"
        )
    except discord.HTTPException as exc:
        raise ValueError(
            f"Discord rejected the emoji upload (HTTP {exc.status}, code {getattr(exc, 'code', 'unknown')}). "
            "The server may have reached its emoji limit or the image may be invalid."
        ) from exc
    return {"ok": True, "action": "add_custom_emoji", "name": emoji.name, "id": str(emoji.id),
            "mention": str(emoji), "source": source_name}
