import hashlib
import json

import pytest

from asas_agent import (
    build_release,
    load_release,
    read_release,
    release_id,
    stage_tools,
    stage_view,
)

#: The fixture release's id. It changes only when the fixture or the release format does,
#: and a change to the format changes every host's ids: that is a breaking release.
FIXTURE_ID = "d96d435f78739440bd5a37e48fb9a241421b20e47f811c039b26e634cdeb15b8"


def build(agents_dir, model="m-1"):
    return build_release(agents_dir, model, agent="helper", extra_files=("fields.md",))


def test_the_release_is_its_files_and_its_id_their_hash(agents_dir):
    release = build(agents_dir)
    assert release["name"] == "Helper" and release["model"] == "m-1"
    assert (release["max_turns"], release["max_tools"]) == (40, 30)
    assert release["instructions"] == (
        "You help a person state what they need.\n\n"
        "The record needs a title and a quantity.\n\n"
        "Speak plainly, one question at a time."
    )
    assert set(release["skills"]) == {"intake", "quotes"}
    body = {k: v for k, v in release.items() if k != "id"}
    assert (
        release["id"]
        == hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    )
    assert release["id"] == release_id(body)


def test_the_fixture_release_keeps_its_id(agents_dir):
    assert build(agents_dir)["id"] == FIXTURE_ID


def test_the_model_and_every_word_are_part_of_the_id(agents_dir):
    assert build(agents_dir, "m-1")["id"] != build(agents_dir, "m-2")["id"]
    plain = build_release(agents_dir, "m-1", agent="helper")
    assert "title and a quantity" not in plain["instructions"]
    assert plain["id"] != build(agents_dir)["id"]


def test_a_missing_extra_file_is_left_out(agents_dir):
    with_missing = build_release(
        agents_dir, "m-1", agent="helper", extra_files=("absent.md",)
    )
    assert with_missing == build_release(agents_dir, "m-1", agent="helper")


def publish(directory, release, environment="prod"):
    releases = directory / "releases"
    releases.mkdir()
    (releases / f"{release['id']}.json").write_text(json.dumps(release))
    (releases / f"active-{environment}.json").write_text(
        json.dumps({"id": release["id"]})
    )
    return releases


def test_outside_the_built_in_environments_the_published_release_is_read(
    agents_dir, tmp_path
):
    release = build(agents_dir)
    publish(tmp_path, release)
    assert load_release("m-1", tmp_path, agent="helper", environment="prod") == release
    assert read_release(tmp_path, "m-1", environment="prod") == release


def test_a_tampered_or_mismatched_release_is_refused(agents_dir, tmp_path):
    release = build(agents_dir)
    releases = publish(tmp_path, release)
    with pytest.raises(ValueError, match="model configuration mismatch"):
        read_release(tmp_path, "m-2", environment="prod")
    (releases / f"{release['id']}.json").write_text(
        json.dumps({**release, "max_tools": 999})
    )
    with pytest.raises(ValueError, match="integrity"):
        read_release(tmp_path, "m-1", environment="prod")
    (releases / "active-prod.json").write_text(json.dumps({"id": "../../etc/passwd"}))
    with pytest.raises(ValueError, match="Invalid agent release reference"):
        read_release(tmp_path, "m-1", environment="prod")


def test_a_stage_sees_only_its_skills_and_their_tools(agents_dir):
    release = build(agents_dir)
    assert set(stage_view(release, "intake")["skills"]) == {"intake"}
    assert set(stage_view(release, "sourcing")["skills"]) == {"quotes"}
    assert stage_view(release, "sourcing")["id"] == release["id"]
    assert stage_tools(release, "intake") == {"record_read", "record_update"}
    assert stage_tools(release, "sourcing") == {"record_read", "quote_read"}
    assert set(stage_view(release, "draft", default_stage="draft")["skills"]) == {
        "intake"
    }
