from helzer.emoji_tools import emoji_tool_specs
from helzer.tools import HIGH_RISK, tool_specs


def test_remove_custom_emojis_tool_is_exposed_as_destructive_action():
    spec = next(item for item in emoji_tool_specs() if item["name"] == "remove_custom_emojis")
    properties = spec["parameters"]["properties"]
    assert {"all", "names", "emoji_ids"} <= set(properties)
    assert "remove" in spec["description"].lower()
    assert "delete" in spec["description"].lower()
    assert "remove_custom_emojis" in HIGH_RISK
    from helzer.image_tools import image_tool_specs
    names = [item["name"] for item in tool_specs() + emoji_tool_specs() + image_tool_specs()]
    assert "remove_custom_emojis" in names
    assert names.count("remove_custom_emojis") == 1
