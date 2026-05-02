from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class AgentPromptRegistry:
    """Loads agent prompt specs from YAML files and renders them at request time.

    Two-layer design:
    - render_system(agent_id)  → static identity + behavioral rules (system message)
    - render_context(agent_id, **slots) → dynamic context template filled with request data
    """

    def __init__(self, prompts_dir: Path = _PROMPTS_DIR) -> None:
        self._specs: dict[str, dict[str, Any]] = {}
        self._load(prompts_dir)

    def _load(self, directory: Path) -> None:
        if not directory.exists():
            logger.warning("Prompts directory not found: %s", directory)
            return
        for path in directory.glob("*.yaml"):
            try:
                spec = yaml.safe_load(path.read_text())
                agent_id = spec.get("agent_id")
                if agent_id:
                    self._specs[agent_id] = spec
                    logger.debug("Loaded prompt spec: %s (v%s)", agent_id, spec.get("version", "?"))
                else:
                    logger.warning("Prompt file missing agent_id: %s", path)
            except Exception:
                logger.exception("Failed to load prompt file: %s", path)

    def render_system(self, agent_id: str) -> str:
        spec = self._get_spec(agent_id)
        parts = []
        if identity := spec.get("identity", "").strip():
            parts.append(identity)
        if rules := spec.get("behavioral_rules", "").strip():
            parts.append(rules)
        return "\n\n".join(parts)

    def render_context(self, agent_id: str, **slots: Any) -> str:
        spec = self._get_spec(agent_id)
        template: str = spec.get("context_template", "")
        try:
            return template.format_map(_SafeFormatMap(slots))
        except Exception:
            logger.exception("Failed to render context for agent_id=%s", agent_id)
            return template

    def _get_spec(self, agent_id: str) -> dict[str, Any]:
        if agent_id not in self._specs:
            raise KeyError(f"No prompt spec registered for agent_id='{agent_id}'")
        return self._specs[agent_id]

    def reload(self) -> None:
        self._specs.clear()
        self._load(_PROMPTS_DIR)


class _SafeFormatMap(dict):  # type: ignore[type-arg]
    """Returns the original placeholder for any missing key instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# Module-level singleton — import and use directly in nodes.
registry = AgentPromptRegistry()
