"""Structural parent-child contract shared by Dream and Nap.

These checks cannot establish semantic coherence. They deliberately impose
no tree-wide goal, sibling similarity, or source-file diversity requirements.
"""

MODES = ("broad", "coherent")
RELATIONS = ("extend", "integrate", "challenge", "replace", "simplify", "alternative")


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_connection(
    mode: object, goal: object, parent_id: object, connection: object,
) -> list[str]:
    """Return explicit format errors without mutating the node or its parent."""
    errors = []
    if not isinstance(mode, str) or mode not in MODES:
        errors.append("mode must be 'broad' or 'coherent'")
        return errors
    if mode == "coherent" and not _has_text(goal):
        errors.append("goal must describe this node's own objective")
    if parent_id is not None and not _has_text(parent_id):
        errors.append("parent must be a nonempty identifier or null")
        return errors
    if parent_id is None:
        if connection is not None:
            errors.append("parent_connection requires a parent; seeds use null")
        return errors
    if connection is None:
        if mode == "coherent":
            errors.append("parent_connection is required for a coherent child")
        return errors
    if not isinstance(connection, dict):
        errors.append("parent_connection must be an object")
        return errors

    relation = connection.get("relation")
    if not isinstance(relation, str) or relation not in RELATIONS:
        errors.append(f"parent_connection.relation must be one of {', '.join(RELATIONS)}")
    for name in ("basis", "delta"):
        if not _has_text(connection.get(name)):
            errors.append(f"parent_connection.{name} must be nonempty text")
    for name in ("preserves", "supersedes"):
        value = connection.get(name)
        if not isinstance(value, list) or any(not _has_text(item) for item in value):
            errors.append(f"parent_connection.{name} must be a list of nonempty strings")
    if relation == "replace" and connection.get("supersedes") == []:
        errors.append("parent_connection.supersedes must name the replaced decisions")
    return errors
