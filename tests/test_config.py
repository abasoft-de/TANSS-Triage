"""
Usage: pytest tests/test_config.py

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: test_config.py
Beschreibung: Prüft Laden, Defaults und Validierung der Konfiguration.
Letzte Änderung: 2026-09-10
"""

from tanss_triage.config import load_config


def _clean_env(monkeypatch):
    for name in ("TANSS_BASE_URL", "TANSS_USERNAME", "TANSS_PASSWORD",
                 "ANTHROPIC_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_without_files(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    cfg = load_config(config_path=str(tmp_path / "gibtsnicht.toml"),
                      env_path=str(tmp_path / "gibtsnicht.env"))
    assert cfg.webhook.listen_port == 8763
    assert cfg.whisper.model == "large-v3"
    assert "wav" in cfg.audio.extensions
    assert cfg.llm.resolved_provider() == "none"      # kein Key gesetzt
    # Pflichtwerte fehlen -> check() meldet das
    assert any("TANSS_USERNAME" in problem for problem in cfg.check())


def test_reads_toml_and_env(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    toml = tmp_path / "config.toml"
    toml.write_text("""
[webhook]
listen_port = 9999
secret = "geheim"

[whisper]
model = "small"

[llm]
provider = "openai_compatible"
base_url = "http://localhost:11434/v1"
""", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("TANSS_USERNAME=api\nTANSS_PASSWORD=pw\n"
                   "LLM_API_KEY=lokal\n", encoding="utf-8")

    cfg = load_config(config_path=str(toml), env_path=str(env))
    assert cfg.webhook.listen_port == 9999
    assert cfg.webhook.secret == "geheim"
    assert cfg.whisper.model == "small"
    assert cfg.llm.resolved_provider() == "openai_compatible"
    assert cfg.llm.api_key == "lokal"
    assert cfg.check() == []


def test_anthropic_auto_selection(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("TANSS_USERNAME", "api")
    monkeypatch.setenv("TANSS_PASSWORD", "pw")
    cfg = load_config(config_path=str(tmp_path / "x.toml"),
                      env_path=str(tmp_path / "x.env"))
    assert cfg.llm.resolved_provider() == "anthropic"
    assert cfg.check() == []


def test_unknown_toml_keys_are_ignored(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    toml = tmp_path / "config.toml"
    toml.write_text("[webhook]\nlisten_port = 8000\nneuer_schalter = true\n",
                    encoding="utf-8")
    cfg = load_config(config_path=str(toml),
                      env_path=str(tmp_path / "x.env"))
    assert cfg.webhook.listen_port == 8000
