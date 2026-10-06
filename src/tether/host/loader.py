"""Plugin discovery.

Sources, later ones overriding earlier ones on a name clash:
1. installed packages exposing the `tether.plugins` entry point;
2. `~/.tether/plugins/` (user plugins);
3. `./.tether/plugins/` (project plugins).

A plugin source may export `Plugin` objects and/or `AbstractCapability` subclasses.
Capabilities from `pydantic_ai_harness` are resolved lazily by name, because many of
them need optional extras.
"""

import dataclasses
import importlib
import importlib.util
import inspect
import logging
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic_ai.capabilities import AbstractCapability

from tether.api.plugin import Command, Plugin

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "tether.plugins"


@dataclass
class LoadedPlugin:
    name: str
    cap_type: type[AbstractCapability[Any]]
    source: str
    plugin: Plugin | None = None


@dataclass
class PluginSet:
    plugins: dict[str, LoadedPlugin] = field(default_factory=dict)
    errors: list[tuple[str, Exception]] = field(default_factory=list)

    def add(self, loaded: LoadedPlugin) -> None:
        if (prev := self.plugins.get(loaded.name)) is not None:
            log.warning("plugin %r from %s overrides %s", loaded.name, loaded.source, prev.source)
        self.plugins[loaded.name] = loaded

    def capability_types(self) -> list[type[AbstractCapability[Any]]]:
        return [p.cap_type for p in self.plugins.values()]

    def commands(self, enabled: Iterable[str]) -> dict[str, Command]:
        result: dict[str, Command] = {}
        for name in enabled:
            loaded = self.plugins.get(name)
            if loaded is not None and loaded.plugin is not None:
                result.update(loaded.plugin.commands)
        return result


def discover(plugin_dirs: Iterable[Path], *, use_entry_points: bool = True) -> PluginSet:
    found = PluginSet()
    if use_entry_points:
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            _collect(found, f"entry point {ep.name}", lambda ep=ep: ep.load())
    for directory in plugin_dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.name.startswith(("_", ".")):
                continue
            if path.suffix == ".py" or (path / "__init__.py").is_file():
                _collect(found, str(path), lambda path=path: import_path(path))
    return found


def _collect(found: PluginSet, source: str, load: Any) -> None:
    try:
        obj = load()
        items = list(_extract(obj))
    except Exception as e:  # a broken plugin must not take the host down
        log.error("failed to load plugin from %s: %s", source, e)
        found.errors.append((source, e))
        return
    if not items:
        log.warning("%s exports no plugins", source)
    for item in items:
        found.add(_to_loaded(item, source))


def _extract(obj: Any) -> Iterable[Plugin | type[AbstractCapability[Any]]]:
    if isinstance(obj, Plugin) or _is_capability_class(obj):
        yield obj
    elif isinstance(obj, ModuleType):
        for value in vars(obj).values():
            if isinstance(value, Plugin):
                yield value
            elif _is_capability_class(value) and value.__module__ == obj.__name__:
                yield value
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from _extract(value)
    else:
        raise TypeError(f"expected a Plugin, a capability class or a module, got {type(obj).__name__}")


def _is_capability_class(value: Any) -> bool:
    return (
        inspect.isclass(value)
        and issubclass(value, AbstractCapability)
        and not inspect.isabstract(value)
        and value.get_serialization_name() is not None
    )


def _to_loaded(item: Plugin | type[AbstractCapability[Any]], source: str) -> LoadedPlugin:
    if isinstance(item, Plugin):
        return LoadedPlugin(item.name, item.capability_type(), source, item)
    name = item.get_serialization_name()
    assert name is not None
    return LoadedPlugin(name, spec_compatible(item), source)


def spec_compatible(cls: type[AbstractCapability[Any]]) -> type[AbstractCapability[Any]]:
    """`Agent.from_spec` only accepts dataclass capability types (e.g. harness `Coder` is not one).

    A non-dataclass gets a same-named dataclass subclass that keeps the original `__init__`.
    """
    if dataclasses.is_dataclass(cls) and "__dataclass_fields__" in cls.__dict__:
        return cls
    name = cls.get_serialization_name()
    shim = type(cls.__name__, (cls,), {"__module__": cls.__module__, "__qualname__": cls.__qualname__})
    shim.get_serialization_name = classmethod(lambda _cls: name)  # type: ignore[method-assign]
    return dataclasses.dataclass(init=False, repr=False, eq=False)(shim)


def import_path(path: Path) -> ModuleType:
    """Import a plugin file or package under a private, path-derived module name."""
    target = path / "__init__.py" if path.is_dir() else path
    stem = re.sub(r"\W", "_", "_".join(path.resolve().with_suffix("").parts[1:]))
    module_name = f"tether_plugins.{stem}"
    spec = importlib.util.spec_from_file_location(
        module_name, target, submodule_search_locations=[str(path)] if path.is_dir() else None
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def resolve_harness_capability(name: str) -> type[AbstractCapability[Any]] | None:
    """Look up a capability exported by `pydantic_ai_harness` by its class name."""
    harness = importlib.import_module("pydantic_ai_harness")
    if name not in getattr(harness, "__all__", ()):
        return None
    try:
        value = getattr(harness, name)
    except ImportError as e:
        raise ImportError(f"capability {name!r} needs an optional extra of pydantic-ai-harness: {e}") from e
    return spec_compatible(value) if _is_capability_class(value) else None
