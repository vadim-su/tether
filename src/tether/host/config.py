"""Config: a native Pydantic AI agent spec plus a `host:` section for tether itself.

```yaml
model: anthropic:claude-haiku-4-5  # the default when omitted
instructions: Ты помощник.
capabilities:
  - LocalWorkspace: {root: .}
  - Coder: {}
  - git-guard: {}
host:
  frontends: [headless]
```

`~/.tether/tether.yaml` and `./.tether/tether.yaml` are merged: mappings merge
recursively, the project wins on scalars, and capability lists are concatenated
with the project's entry replacing a global one of the same name.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import CAPABILITY_TYPES

from tether.host.loader import PluginSet, resolve_harness_capability

CONFIG_NAME = "tether.yaml"
DEFAULT_MODEL = "anthropic:claude-haiku-4-5"
"""Used when neither tether.yaml nor --model names one; needs ANTHROPIC_API_KEY."""


def user_dir() -> Path:
    return Path(os.environ.get("TETHER_HOME", Path.home() / ".tether"))


def project_dir(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".tether"


class HostConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    frontends: list[str] = ["headless"]


@dataclass
class TetherConfig:
    agent: dict[str, Any]
    host: HostConfig
    sources: list[Path] = field(default_factory=list)

    @property
    def capability_names(self) -> list[str]:
        return [_cap_name(c) for c in self.agent.get("capabilities", [])]


def load_config(paths: list[Path] | None = None, *, cwd: Path | None = None) -> TetherConfig:
    if paths is None:
        paths = [user_dir() / CONFIG_NAME, project_dir(cwd) / CONFIG_NAME]
    merged: dict[str, Any] = {}
    sources: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected a mapping at top level")
        merged = merge(merged, data)
        sources.append(path)
    host = HostConfig.model_validate(merged.pop("host", None) or {})
    return TetherConfig(agent=merged, host=host, sources=sources)


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if key == "capabilities" and isinstance(value, list):
            out[key] = _merge_capabilities(out.get(key) or [], value)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def _merge_capabilities(base: list[Any], over: list[Any]) -> list[Any]:
    names = {_cap_name(c) for c in over}
    return [c for c in base if _cap_name(c) not in names] + list(over)


def _cap_name(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and len(entry) == 1:
        return next(iter(entry))
    raise ValueError(f"capability entry must be a name or a one-key mapping, got {entry!r}")


def build_agent(config: TetherConfig, plugins: PluginSet, *, model: str | None = None) -> Agent[Any, str]:
    """Build the agent: plugins plus any `pydantic_ai_harness` capability the spec names."""
    custom = plugins.capability_types()
    known = {t.get_serialization_name() for t in custom} | set(CAPABILITY_TYPES)
    for name in config.capability_names:
        if name not in known and (cap := resolve_harness_capability(name)) is not None:
            custom.append(cap)
            known.add(name)
    spec = AgentSpec.model_validate(config.agent)
    return Agent.from_spec(spec, custom_capability_types=custom, model=model or spec.model or DEFAULT_MODEL)
