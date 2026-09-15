"""
Usage: from tanss_triage.config import load_config

Autor: SO, (c) abasoft GmbH 2026-09-10
Datei: config.py
Beschreibung: Konfiguration von TANSS-Triage. Secrets kommen aus der .env
              (bzw. der Umgebung), das Verhalten aus config.toml. Beide Dateien
              werden relativ zum Projektstamm gesucht, damit ein systemd-Dienst
              und ein manueller Aufruf dieselben Werte sehen. check() liefert
              Klartextfehler statt Ausnahmen beim Laden.
Letzte Änderung: 2026-09-15
"""

import os
from dataclasses import dataclass, field

try:
    import tomllib
except ModuleNotFoundError:                        # Python < 3.11
    import tomli as tomllib

from dotenv import load_dotenv

# Der Projektstamm kommt aus dem Paket selbst (TANSS_TRIAGE_HOME, Quellbaum
# oder Arbeitsverzeichnis) - NICHT relativ zu dieser Datei rechnen: nach
# "pip install ." liegt sie in site-packages und zeigt ins Leere.
from . import BASE_DIR


@dataclass
class WebhookConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 8763
    secret: str = ""


@dataclass
class AudioConfig:
    extensions: list = field(default_factory=lambda: [
        "wav", "mp3", "m4a", "ogg", "opus", "flac", "aac", "wma"])


@dataclass
class WhisperConfig:
    model: str = "large-v3"
    device: str = "auto"
    compute_type: str = "int8"
    language: str = "de"
    download_root: str = ""


@dataclass
class LlmConfig:
    provider: str = ""            # "" = Automatik (anthropic bei Key, sonst none)
    model: str = "claude-sonnet-5"
    base_url: str = ""
    max_tokens: int = 1500
    timeout: float = 120.0
    api_key: str = ""             # kommt aus der Umgebung, nicht aus dem TOML

    def resolved_provider(self):
        """Automatik auflösen: expliziter Wert gewinnt, sonst nach Key."""
        if self.provider:
            return self.provider
        return "anthropic" if self.api_key else "none"


@dataclass
class StarfaceConfig:
    sender_patterns: list = field(default_factory=lambda: [
        "starface@tele-x.abasoft-gmbh.de"])
    generic_title_pattern: str = "^Sie haben eine Sprachnachricht"
    generic_content_patterns: list = field(default_factory=lambda: [
        "WARNUNG: EXTERNE NACHRICHT", "STARFACE"])


@dataclass
class AssignmentConfig:
    enabled: bool = True
    assign_remitter: bool = True


@dataclass
class DbConfig:
    defaults_file: str = "~/.my.cnf"
    host: str = "localhost"
    database: str = "tanss"

    def resolved_defaults_file(self):
        return os.path.expanduser(self.defaults_file) if self.defaults_file else ""


@dataclass
class CommentConfig:
    internal: bool = True


@dataclass
class StateConfig:
    db_path: str = "state.db"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = ""


@dataclass
class TanssConfig:
    base_url: str = "https://tanss.abasoft.de"
    username: str = ""
    password: str = ""
    timeout: float = 30.0


@dataclass
class Config:
    tanss: TanssConfig = field(default_factory=TanssConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    starface: StarfaceConfig = field(default_factory=StarfaceConfig)
    assignment: AssignmentConfig = field(default_factory=AssignmentConfig)
    db: DbConfig = field(default_factory=DbConfig)
    comment: CommentConfig = field(default_factory=CommentConfig)
    state: StateConfig = field(default_factory=StateConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    dry_run: bool = False

    def check(self):
        """Liefert eine Liste der Konfigurationsfehler, leer heißt in Ordnung."""
        problems = []
        if not self.tanss.base_url.startswith(("http://", "https://")):
            problems.append("TANSS_BASE_URL sieht nicht wie eine URL aus: %r"
                            % self.tanss.base_url)
        if not self.tanss.username or not self.tanss.password:
            problems.append("TANSS_USERNAME/TANSS_PASSWORD fehlen. Beide "
                            "gehören in die .env (Vorlage: .env.example).")
        provider = self.llm.resolved_provider()
        if provider not in ("anthropic", "openai_compatible", "none"):
            problems.append("[llm] provider muss anthropic, openai_compatible "
                            "oder none sein, nicht %r." % provider)
        if provider == "anthropic" and not self.llm.api_key:
            problems.append("[llm] provider = anthropic, aber ANTHROPIC_API_KEY "
                            "ist nicht gesetzt.")
        if provider == "openai_compatible" and not self.llm.base_url:
            problems.append("[llm] provider = openai_compatible braucht eine "
                            "base_url (z. B. http://localhost:11434/v1).")
        if not (1 <= self.webhook.listen_port <= 65535):
            problems.append("[webhook] listen_port muss zwischen 1 und 65535 "
                            "liegen, nicht %r." % self.webhook.listen_port)
        return problems


def _fill(instance, values):
    """Überträgt bekannte TOML-Schlüssel in eine Dataclass-Instanz.

    Unbekannte Schlüssel werden ignoriert statt zu knallen - eine neuere
    config.toml soll eine ältere Programmversion nicht am Start hindern.
    """
    for key, value in (values or {}).items():
        if hasattr(instance, key):
            setattr(instance, key, value)
    return instance


def load_config(config_path=None, env_path=None):
    """Lädt .env und config.toml und liefert die fertige Config.

    Suchreihenfolge für beide Dateien: expliziter Pfad, dann Projektstamm.
    Fehlende Dateien sind erlaubt (alles hat Defaults); ob die Pflichtwerte
    da sind, sagt hinterher Config.check().
    """
    load_dotenv(env_path or os.path.join(BASE_DIR, ".env"))

    raw = {}
    path = config_path or os.path.join(BASE_DIR, "config.toml")
    if os.path.isfile(path):
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)

    cfg = Config()
    _fill(cfg.webhook, raw.get("webhook"))
    _fill(cfg.audio, raw.get("audio"))
    _fill(cfg.whisper, raw.get("whisper"))
    _fill(cfg.llm, raw.get("llm"))
    _fill(cfg.starface, raw.get("starface"))
    _fill(cfg.assignment, raw.get("assignment"))
    _fill(cfg.db, raw.get("db"))
    _fill(cfg.comment, raw.get("comment"))
    _fill(cfg.state, raw.get("state"))
    _fill(cfg.logging, raw.get("logging"))

    cfg.tanss.base_url = os.environ.get(
        "TANSS_BASE_URL", cfg.tanss.base_url).rstrip("/")
    cfg.tanss.username = os.environ.get("TANSS_USERNAME", "")
    cfg.tanss.password = os.environ.get("TANSS_PASSWORD", "")

    # Der Key hängt am Provider: anthropic nimmt ANTHROPIC_API_KEY,
    # openai_compatible nimmt LLM_API_KEY (viele lokale Endpunkte brauchen
    # gar keinen - dann bleibt er leer).
    if cfg.llm.provider == "openai_compatible":
        cfg.llm.api_key = os.environ.get("LLM_API_KEY", "")
    else:
        cfg.llm.api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # Relative Pfade (state.db, Logdatei) beziehen sich auf den Projektstamm,
    # nicht auf das zufällige Arbeitsverzeichnis des Aufrufers.
    if cfg.state.db_path and not os.path.isabs(cfg.state.db_path):
        cfg.state.db_path = os.path.join(BASE_DIR, cfg.state.db_path)
    if cfg.logging.file and not os.path.isabs(cfg.logging.file):
        cfg.logging.file = os.path.join(BASE_DIR, cfg.logging.file)

    return cfg
