from helzer.emoji_tools import MAX_EMOJI_BYTES, _clean_name, _crop_image, emoji_tool_specs
from helzer.image_tools import is_image_generation_request, requested_image_defaults
from PIL import Image
import io


def test_emoji_tool_schema_has_attachment_and_crop_fields():
    spec = emoji_tool_specs()[0]
    properties = spec["parameters"]["properties"]
    assert {"attachment_index", "name", "x", "y", "width", "height", "archive_path"} <= set(properties)
    assert spec["parameters"]["required"] == ["name"]


def test_clean_name_normalizes_ai_generated_names():
    assert _clean_name("  Server Start!  ") == "server_start"
    assert len(_clean_name("a" * 100)) == 32


def test_crop_image_returns_small_discord_ready_png():
    source = Image.new("RGBA", (1000, 500), (255, 0, 0, 255))
    raw = io.BytesIO()
    source.save(raw, format="PNG")

    result = _crop_image(
        raw.getvalue(),
        "image/png",
        {"x": 0, "y": 0, "width": 500, "height": 1000},
    )

    assert len(result) <= MAX_EMOJI_BYTES
    decoded = Image.open(io.BytesIO(result))
    assert decoded.width <= 128
    assert decoded.height <= 128


def test_zip_inspection_reads_source_and_skips_secrets():
    import zipfile
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr("emoji.py", 'EMOJI_START = "▶️"')
        z.writestr(".env", "DISCORD_TOKEN=secret")
        z.writestr("../escape.py", "bad")
        z.writestr("image.png", b"not-source")
    result = __import__("helzer.emoji_tools", fromlist=["inspect_archive"]).inspect_archive(
        raw.getvalue(), "project.zip"
    )
    assert [f["path"] for f in result["files"]] == ["emoji.py"]
    assert "EMOJI_START" in result["context"]


def test_zip_limits_file_count():
    import zipfile
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        for i in range(501):
            z.writestr(f"f{i}.txt", "x")
    import pytest
    with pytest.raises(ValueError, match="too many files"):
        __import__("helzer.emoji_tools", fromlist=["inspect_archive"]).inspect_archive(
            raw.getvalue(), "project.zip"
        )


def test_duplicate_image_assets_are_removed():
    from helzer.emoji_tools import _select_unique_images
    images = [
        {"path": "a.png", "data": b"same", "mime": "image/png"},
        {"path": "b.png", "data": b"same", "mime": "image/png"},
        {"path": "c.png", "data": b"different", "mime": "image/png"},
    ]
    result = _select_unique_images(images, 5000)
    assert [item["path"] for item in result] == ["a.png", "c.png"]


def test_emoji_selection_is_capped_at_5000():
    from helzer.emoji_tools import _select_unique_images
    images = [
        {"path": f"{i}.png", "data": str(i).encode(), "mime": "image/png"}
        for i in range(5001)
    ]
    assert len(_select_unique_images(images, 5000)) == 5000


def test_image_request_detection_routes_advertisement_to_generation():
    prompt = "Helzer make me a premium HelzerX Cloud 16:9 advertisement"
    assert is_image_generation_request(prompt) is True
    assert requested_image_defaults(prompt) == ("16:9", "2K")


def test_image_request_detection_does_not_capture_normal_design_discussion():
    assert is_image_generation_request("What is a good design for my Discord server?") is False
