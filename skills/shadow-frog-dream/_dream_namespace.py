"""Shared dream namespace resolution for setup and reconciliation."""
import json
import os


class NamespaceConfigurationError(ValueError):
    """Raised when a namespace source exists but cannot be used safely."""


def _read_env_namespace(path):
    """Read the first DREAM_NAMESPACE value, stripping matching quotes."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("DREAM_NAMESPACE="):
                    value = line.split("=", 1)[1].strip()
                    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                        value = value[1:-1]
                    return value.strip()
    except OSError as exc:
        raise NamespaceConfigurationError(
            f"could not read {os.path.basename(path)}: {exc}"
        ) from exc
    return ""


def resolve_dream_namespace(repo_root, override=None, environ=None):
    """Resolve namespace with one precedence/order contract for all callers."""
    if override:
        return override

    environ = os.environ if environ is None else environ
    if environ.get("DREAM_NAMESPACE"):
        return environ["DREAM_NAMESPACE"]

    task_info_path = os.path.join(repo_root, "TASK_INFO.json")
    if os.path.isfile(task_info_path):
        try:
            with open(task_info_path, encoding="utf-8") as f:
                task_info = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise NamespaceConfigurationError(
                f"could not parse TASK_INFO.json: {exc}"
            ) from exc
        if not isinstance(task_info, dict):
            raise NamespaceConfigurationError(
                "TASK_INFO.json must contain a JSON object"
            )
        if "dream_namespace" in task_info:
            task_namespace = task_info["dream_namespace"]
            if not isinstance(task_namespace, str):
                raise NamespaceConfigurationError(
                    "TASK_INFO.json dream_namespace must be a string"
                )
            if task_namespace:
                return task_namespace

    env_file = os.path.join(repo_root, ".env")
    if os.path.isfile(env_file):
        namespace = _read_env_namespace(env_file)
        if namespace:
            return namespace

    return os.path.basename(repo_root)
