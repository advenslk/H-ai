from helzer.prompts import build_system_prompt


def test_default_language_policy_is_english_and_multilingual():
    prompt = build_system_prompt("Asia/Colombo", "Test Server")
    assert "English is Helzer's primary/default language." in prompt
    assert "any language supported by the underlying AI model" in prompt
    assert "Romanized Sinhala (Singlish)" in prompt
    assert "same language as the user" in prompt
    assert "Explicit language requests" in prompt
