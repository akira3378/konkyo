import pytest

from konkyo.config import ConfigError, cors_allow_origins


class TestCorsAllowOrigins:
    def test_default_is_localhost_3000(self, monkeypatch):
        monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)
        assert cors_allow_origins() == ["http://localhost:3000"]

    def test_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://konkyo.example.com")
        assert cors_allow_origins() == ["https://konkyo.example.com"]

    def test_splits_comma_separated_list(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://a.com, https://b.com")
        # 空格要被去掉——部署时这个环境变量是人手填的，不能因为多打一个空格就炸
        assert cors_allow_origins() == ["https://a.com", "https://b.com"]


class TestRequire:
    def test_missing_env_raises_config_error(self, monkeypatch):
        from konkyo.config import _require

        monkeypatch.delenv("SOME_UNSET_VAR", raising=False)
        with pytest.raises(ConfigError):
            _require("SOME_UNSET_VAR")
