from helzer.emoji_tools import MAX_EMOJI_BYTES, _clean_name, _crop_image, emoji_tool_specs
from PIL import Image
import io


def test_emoji_tool_schema_has_attachment_and_crop_fields():
    spec = emoji_tool_specs()[0]
    properties = spec["parameters"]["properties"]
    assert {"attachment_index", "name", "x", "y", "width", "height"} <= set(properties)
    assert spec["parameters"]["required"] == ["attachment_index", "name"]


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
