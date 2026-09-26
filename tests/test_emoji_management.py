from helzer.emoji_tools import emoji_tool_specs
from helzer.tools import tool_specs
from helzer.image_tools import image_tool_specs


def test_list_custom_emojis_tool_is_exposed():
    spec = next(item for item in emoji_tool_specs() if item["name"] == "list_custom_emojis")
    assert "emoji" in spec["description"].lower()
    names = [item["name"] for item in tool_specs() + emoji_tool_specs() + image_tool_specs()]
    assert "list_custom_emojis" in names
    assert names.count("list_custom_emojis") == 1


def test_remove_custom_emojis_is_available_without_admin_but_requires_manage_expressions():
    spec = next(item for item in emoji_tool_specs() if item["name"] == "remove_custom_emojis")
    assert "manage expressions" in spec["description"].lower()


def test_multilingual_requests_are_not_filtered_by_english_tool_keywords():
    from helzer.agent import HelzerAgent
    assert HelzerAgent._needs_tools("remove all custom emojis from this server") is True
    assert HelzerAgent._needs_tools("supprimer tous les emojis du serveur") is True
