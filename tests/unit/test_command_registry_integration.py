"""S98.0 — the consultant command surfaces only while enabled (Liskov).

Drives bot-base's ``CommandRegistry`` with a fake plugin manager: when the
plugin is among the enabled set, ``consultant`` appears in ``command_index()``;
when it is not, the command is absent. Skips gracefully when bot-base is absent
(a bare per-plugin CI clone) — bot-base is a declared peer in the SDK.
"""
import pytest

pytest.importorskip("plugins.bot_base.bot_base.services.command_registry")

from plugins.bot_base.bot_base.services.command_registry import (  # noqa: E402
    CommandRegistry,
)

from plugins.bot_meinchat_llm import BotMeinchatLlmPlugin  # noqa: E402


class _FakePluginManager:
    def __init__(self, enabled):
        self._enabled = enabled

    def get_enabled_plugins(self):
        return list(self._enabled)


def _plugin():
    plugin = BotMeinchatLlmPlugin()
    plugin.initialize()
    return plugin


def test_consultant_command_present_when_enabled():
    plugin = _plugin()
    registry = CommandRegistry(_FakePluginManager([plugin]))

    index = registry.command_index()

    assert "consultant" in index
    assert index["consultant"] is plugin


def test_consultant_command_absent_when_disabled():
    # A disabled plugin is simply not in the enabled set the registry walks.
    registry = CommandRegistry(_FakePluginManager([]))

    index = registry.command_index()

    assert "consultant" not in index


def test_provider_resolves_for_consultant_namespace():
    plugin = _plugin()
    registry = CommandRegistry(_FakePluginManager([plugin]))

    assert registry.get_provider_for_namespace("consultant") is plugin
