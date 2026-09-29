"""Strict schemas: what strict structured output refuses, fixed."""

from typing import Optional

import pydantic
from pydantic import BaseModel, Field

from asas_llm import response_format, schema_name, to_strict_json_schema


class Address(BaseModel):
    city: str
    country: Optional[str] = None


class Person(BaseModel):
    """Candidate Summary"""

    model_config = pydantic.ConfigDict(title="Candidate Summary")
    name: str
    nickname: Optional[str] = None
    home: Address = Field(description="Where they live")
    past: list[Address] = []


def test_every_object_is_closed_and_every_property_required():
    schema = to_strict_json_schema(Person)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["name", "nickname", "home", "past"]
    address = schema["$defs"]["Address"]
    assert address["additionalProperties"] is False and address["required"] == ["city", "country"]


def test_a_documented_reference_is_inlined_and_made_strict():
    home = to_strict_json_schema(Person)["properties"]["home"]
    assert "$ref" not in home and "allOf" not in home
    assert home["description"] == "Where they live"
    assert home["additionalProperties"] is False and home["required"] == ["city", "country"]


def test_null_defaults_are_dropped_but_nullability_stays():
    nickname = to_strict_json_schema(Person)["properties"]["nickname"]
    assert "default" not in nickname
    assert {"type": "null"} in nickname["anyOf"]


def test_the_models_own_schema_is_not_mutated():
    before = Person.model_json_schema()
    to_strict_json_schema(Person)
    assert Person.model_json_schema() == before


def test_the_envelope_name_is_one_the_api_accepts():
    assert schema_name(Person) == "Candidate_Summary"
    fmt = response_format(Person)
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"] == "Candidate_Summary"


def test_type_adapters_work_too():
    schema = to_strict_json_schema(pydantic.TypeAdapter(list[Address]))
    assert schema["type"] == "array"
