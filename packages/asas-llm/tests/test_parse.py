"""Replies to models, or typed errors."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from asas_llm import EmptyOutputError, StructuredOutputError, extract_text, parse_structured


class Verdict(BaseModel):
    score: int
    reasons: list[str]


@pytest.mark.parametrize(
    "reply",
    [
        '{"score": 3, "reasons": ["a"]}',
        '```json\n{"score": 3, "reasons": ["a"]}\n```',
        'Here you go:\n{"score": 3, "reasons": ["a"]}\nHope that helps.',
        SimpleNamespace(content='{"score": 3, "reasons": ["a"]}'),
        [{"type": "text", "text": '{"score": 3, '}, {"type": "text", "text": '"reasons": ["a"]}'}],
        {"content": [{"type": "text", "text": '{"score": 3, "reasons": ["a"]}'}]},
        "{'score': 3, 'reasons': ['a'],}",  # repaired
    ],
)
def test_the_shapes_models_actually_return(reply):
    assert parse_structured(reply, Verdict) == Verdict(score=3, reasons=["a"])


def test_nothing_is_an_empty_output_error():
    with pytest.raises(EmptyOutputError):
        parse_structured(SimpleNamespace(content=""), Verdict, context="judge")


def test_a_wrong_shape_is_a_structured_output_error_with_the_raw_text():
    with pytest.raises(StructuredOutputError) as exc:
        parse_structured('{"score": "high"}', Verdict, context="judge")
    assert "(judge)" in str(exc.value) and "Verdict" in str(exc.value)
    assert exc.value.raw == '{"score": "high"}'


def test_images_in_content_parts_are_not_text():
    assert extract_text([{"type": "image_url", "image_url": {}}, {"type": "text", "text": "hi"}]) == "hi"
