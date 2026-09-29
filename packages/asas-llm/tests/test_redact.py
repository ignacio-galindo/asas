"""Inline media stays out of traces; text and structure stay in."""

from asas_llm import redact_messages


def test_every_inline_payload_shape_is_omitted_and_the_input_untouched():
    messages = [
        {"role": "system", "content": "be brief"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "input_audio", "input_audio": {"data": "A" * 100, "format": "wav"}},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "B" * 50}},
                {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "C" * 20}},
                {"type": "file", "file": {"filename": "cv.pdf", "file_data": "D" * 30}},
            ],
        },
    ]
    out = redact_messages(messages)
    parts = out[1]["content"]
    assert out[0] == messages[0] and parts[0] == messages[1]["content"][0]
    assert parts[1]["input_audio"] == {"data": "<omitted 100 chars>", "format": "wav"}
    assert parts[2]["image_url"]["url"].startswith("data:image/png;base64,<omitted")
    assert parts[3]["image_url"]["url"] == "https://example.com/cat.png"
    assert parts[4]["source"]["data"] == "<omitted 20 chars>"
    assert parts[5]["file"] == {"filename": "cv.pdf", "file_data": "<omitted 30 chars>"}
    assert messages[1]["content"][1]["input_audio"]["data"] == "A" * 100
