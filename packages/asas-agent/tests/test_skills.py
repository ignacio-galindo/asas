from asas_agent import load_skills, parse_skill


def test_a_skill_is_its_header_and_its_playbook():
    spec, body = parse_skill(
        "---\nname: a\nenabled: true\ntools: [x]\n---\n\n Do this.\n"
    )
    assert spec == {"name": "a", "enabled": True, "tools": ["x"]}
    assert body == "Do this."


def test_the_playbook_may_hold_a_rule_line():
    _, body = parse_skill("---\nname: a\n---\nOne --- two\n---\nthree")
    assert body == "One --- two\n---\nthree"


def test_enabled_skills_load_by_name_and_always_skills_join_the_words(agents_dir):
    skills, always = load_skills(agents_dir / "skills")
    assert set(skills) == {
        "intake",
        "quotes",
    }  # parked is disabled, speaking applies always
    assert always == ["Speak plainly, one question at a time."]
    assert (
        skills["intake"]["instructions"] == "Read the record, then ask what is missing."
    )
    assert skills["intake"]["clauses"] == ["R-1"]  # a host's own header fields are kept
