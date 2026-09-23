"""The shared contract regularizes edges, not siblings or a whole tree."""

from copy import deepcopy

import pytest


def connection(relation="extend"):
    return {
        "relation": relation,
        "basis": "The parent exposes a streaming iterator.",
        "delta": "Add cancellation without buffering the complete stream.",
        "preserves": ["Bounded memory"],
        "supersedes": ["Offset checkpoints"] if relation == "replace" else [],
    }


def test_broad_mode_preserves_existing_artifacts(coherence):
    assert coherence.validate_connection("broad", None, "parent", None) == []


def test_coherent_root_has_its_own_goal(coherence):
    assert coherence.validate_connection("coherent", "Streaming export", None, None) == []


@pytest.mark.parametrize(
    "relation", ["extend", "integrate", "challenge", "replace", "simplify", "alternative"]
)
def test_parent_transitions(coherence, relation):
    assert coherence.validate_connection(
        "coherent", "A different child goal", "parent", connection(relation)
    ) == []


def test_ten_diverse_siblings_do_not_need_a_common_goal(coherence):
    for index in range(10):
        assert coherence.validate_connection(
            "coherent", f"Independent worthwhile direction {index}",
            "same-parent", connection("alternative"),
        ) == []


@pytest.mark.parametrize("mode", ["focused", "", None, [], 1])
def test_unknown_mode_is_not_silently_broad(coherence, mode):
    assert "mode" in " ".join(coherence.validate_connection(mode, "Goal", None, None))


@pytest.mark.parametrize("goal", [None, "", " \n", [], 1])
def test_coherent_goal_is_required(coherence, goal):
    assert "goal" in " ".join(coherence.validate_connection("coherent", goal, None, None))


def test_child_requires_connection_but_root_does_not(coherence):
    errors = coherence.validate_connection("coherent", "Goal", "parent", None)
    assert "parent_connection" in " ".join(errors)
    errors = coherence.validate_connection("coherent", "Goal", None, connection())
    assert "parent" in " ".join(errors)


@pytest.mark.parametrize("field", ["relation", "basis", "delta", "preserves", "supersedes"])
def test_connection_fields_are_required(coherence, field):
    edge = connection()
    del edge[field]
    assert field in " ".join(
        coherence.validate_connection("coherent", "Goal", "parent", edge)
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("relation", "unrelated"),
        ("relation", []),
        ("basis", " "),
        ("delta", 3),
        ("preserves", "the API"),
        ("preserves", [""]),
        ("supersedes", [None]),
    ],
)
def test_malformed_connection_is_reported(coherence, field, value):
    edge = connection()
    edge[field] = value
    assert field in " ".join(
        coherence.validate_connection("coherent", "Goal", "parent", edge)
    )


def test_replace_names_the_superseded_decision(coherence):
    edge = connection("replace")
    edge["supersedes"] = []
    assert "supersedes" in " ".join(
        coherence.validate_connection("coherent", "Goal", "parent", edge)
    )


def test_validation_does_not_mutate_or_refute_the_parent(coherence):
    edge = connection("challenge")
    original = deepcopy(edge)
    assert coherence.validate_connection("coherent", "Goal", "parent", edge) == []
    assert edge == original
