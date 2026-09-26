from helzer.emoji_tools import emoji_tool_specs
from helzer.tools import HIGH_RISK, tool_specs


def test_remove_custom_emojis_tool_is_exposed_as_destructive_action():
    spec = next(item for item in emoji_tool_specs() if item["name"] == "remove_custom_emojis")
    properties = spec["parameters"]["properties"]
    assert {"all", "names", "emoji_ids"} <= set(properties)
    assert "remove" in spec["description"].lower()
    assert "delete" in spec["description"].lower()
    assert "remove_custom_emojis" in HIGH_RISK
    assert any(item["name"] == "remove_custom_emojis" for item in tool_specs())
