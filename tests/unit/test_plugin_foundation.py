"""S98.0 — plugin foundation: command seam, config defaults, permission.

Unit-level (no DB): the plugin's BotCommandProvider seam returns the consultant
command(s) and the config defaults resolve; the manage permission is declared.
"""
from plugins.bot_meinchat_llm import (
    BotMeinchatLlmPlugin,
    CONSULTANT_COMMAND,
    MANAGE_PERMISSION_KEY,
    REINDEX_COMMAND,
)


def _initialized_plugin() -> BotMeinchatLlmPlugin:
    plugin = BotMeinchatLlmPlugin()
    plugin.initialize()
    return plugin


def test_metadata_name_and_namespace():
    plugin = _initialized_plugin()
    assert plugin.metadata.name == "bot-meinchat-llm"
    assert plugin.bot_namespace == "consultant"


def test_metadata_declares_all_dependencies():
    plugin = _initialized_plugin()
    assert set(plugin.metadata.dependencies) == {
        "bot-base",
        "meinchat",
        "referral",
        "discount",
        "subscription",
        "shop",
        "booking",
    }


def test_get_bot_commands_exposes_consultant_command():
    plugin = _initialized_plugin()
    command_names = {command.name for command in plugin.get_bot_commands()}
    assert CONSULTANT_COMMAND in command_names
    assert REINDEX_COMMAND in command_names
    for command in plugin.get_bot_commands():
        assert command.namespace == "consultant"


def test_config_defaults_resolve():
    plugin = _initialized_plugin()
    # The LLM client is CORE — the plugin holds only an optional slug, no key.
    assert plugin.get_config("llm_connection_slug") == ""
    assert plugin.get_config("retrieval_mode") == "fts"
    assert plugin.get_config("reward_enabled") is True
    assert plugin.get_config("debug_mode") is False
    assert "rag_dir" in plugin._config


def test_config_has_no_api_key():
    plugin = _initialized_plugin()
    for key in plugin._config:
        assert "api_key" not in key.lower()
        assert "secret" not in key.lower()


def test_resolved_rag_dir_expands_var_dir(monkeypatch):
    monkeypatch.setenv("VBWD_VAR_DIR", "/tmp/var-test")
    plugin = _initialized_plugin()
    assert plugin.resolved_rag_dir() == "/tmp/var-test/bot-meinchat-llm/rag"


def test_admin_permission_declared():
    plugin = _initialized_plugin()
    keys = {entry["key"] for entry in plugin.admin_permissions}
    assert MANAGE_PERMISSION_KEY in keys
