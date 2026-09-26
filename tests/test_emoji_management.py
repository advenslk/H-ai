from helzer.emoji_tools import emoji_tool_specs
from helzer.tools import tool_specs


def test_list_custom_emojis_tool_is_exposed():
    spec = next(item for item in emoji_tool_specs() if item["name"] == "list_custom_emojis")
    assert "emoji" in spec["description"].lower()
    assert any(item["name"] == "list_custom_emojis" for item in tool_specs())


def test_remove_custom_emojis_is_available_without_admin_but_requires_manage_expressions():
    spec = next(item for item in emoji_tool_specs() if item["name"] == "remove_custom_emojis")
    assert "manage expressions" in spec["description"].lower()
