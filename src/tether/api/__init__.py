"""Public API for plugin authors. Everything outside `tether.api` may change between versions."""

from tether.api.plugin import Command, Plugin, plugin

__all__ = ["Command", "Plugin", "plugin"]
