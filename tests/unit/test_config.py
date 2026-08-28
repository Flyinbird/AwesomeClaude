"""core/config.py 服务端配置测试。"""

import pytest

from awesome_claude.core.config import ServerConfig, load_server_config

_ENV_KEYS = [
    "ANTHROPIC_API_KEY",
    "AWESOME_CLAUDE_HOST",
    "AWESOME_CLAUDE_PORT",
    "AWESOME_CLAUDE_MODEL",
    "AWESOME_CLAUDE_MAX_TOKENS",
    "AWESOME_CLAUDE_LOG_LEVEL",
    "AWESOME_CLAUDE_LOG_DIR",
    "AWESOME_CLAUDE_BASE_URL",
]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离 .env 加载与测试环境变量。"""
    monkeypatch.setattr("awesome_claude.core.config.load_dotenv", lambda: None)
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


class TestLoadServerConfig:
    """load_server_config 环境变量读取测试。"""

    def test_reads_all_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AWESOME_CLAUDE_HOST", "0.0.0.0")
        monkeypatch.setenv("AWESOME_CLAUDE_PORT", "9999")
        monkeypatch.setenv("AWESOME_CLAUDE_MODEL", "claude-3-5-sonnet-latest")
        monkeypatch.setenv("AWESOME_CLAUDE_MAX_TOKENS", "2048")
        monkeypatch.setenv("AWESOME_CLAUDE_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("AWESOME_CLAUDE_LOG_DIR", "/tmp/logs")

        config = load_server_config()
        assert config.api_key == "sk-test"
        assert config.host == "0.0.0.0"
        assert config.port == 9999
        assert config.model == "claude-3-5-sonnet-latest"
        assert config.max_tokens == 2048
        assert config.log_level == "DEBUG"
        assert config.log_dir == "/tmp/logs"

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        config = load_server_config()
        assert config.host == "127.0.0.1"
        assert config.port == 9527
        assert config.model == "claude-sonnet-4-20250514"
        assert config.base_url is None
        assert config.max_tokens == 4096
        assert config.log_level == "INFO"
        assert config.log_dir == "logs"

    def test_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv(
            "AWESOME_CLAUDE_BASE_URL", "https://api.deepseek.com/anthropic"
        )
        monkeypatch.setenv("AWESOME_CLAUDE_MODEL", "deepseek-chat")
        config = load_server_config()
        assert config.base_url == "https://api.deepseek.com/anthropic"
        assert config.model == "deepseek-chat"

    def test_missing_api_key_raises(self) -> None:
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            load_server_config()

    def test_invalid_port_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AWESOME_CLAUDE_PORT", "not-a-number")
        with pytest.raises(ValueError, match="AWESOME_CLAUDE_PORT"):
            load_server_config()

    def test_from_env_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("AWESOME_CLAUDE_PORT", "1000")
        config = ServerConfig.from_env()
        assert config.port == 1000
        assert config.api_key == "sk-test"
