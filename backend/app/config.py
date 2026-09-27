"""Runtime configuration with a deliberately small, safe environment surface."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _project_root() -> Path:
    """Resolve the checkout root without baking a developer path into source.

    Launchers may point at a copied checkout with ``ROAD2M_PROJECT_ROOT``;
    otherwise the package location is the portable source of truth.
    """
    configured = os.environ.get("ROAD2M_PROJECT_ROOT")
    if configured and configured.strip():
        return Path(configured).expanduser().resolve()
    return _DEFAULT_PROJECT_ROOT


PROJECT_ROOT = _project_root()

_OPERATIONAL_ENV_NAMES = {
    "ROAD2M_ENABLE_MARKET_CONNECTORS",
    "ROAD2M_ENABLE_REDDIT_INTAKE",
    "ROAD2M_ALPACA_DATA_FEED",
    # Private queue/provider bounds.  These are intentionally allowlisted so
    # they can be read from a local .env without exposing arbitrary process
    # configuration through the browser settings object.
    "ROAD2M_REDDIT_PARALLEL_LIMIT",
    "ROAD2M_CODEX_GLOBAL_CONCURRENCY",
    "ROAD2M_CODEX_BACKGROUND_CONCURRENCY",
    "ROAD2M_SEC_USER_AGENT",
}


def _known_env(name: str, default: str) -> str:
    # Provider credentials and arbitrary command-line configuration are never
    # copied into the browser-facing settings object.  Only named operational
    # variables are read here.
    value = os.environ.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _bounded_int(value: str | int | None, *, default: int, minimum: int, maximum: int) -> int:
    """Parse one private numeric bound and clamp malformed/out-of-range input."""
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Read allowlisted settings without evaluating shell syntax."""

    result: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError):
        return result
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in _OPERATIONAL_ENV_NAMES:
            continue
        value = value.strip()
        # Remove only matching quote wrappers.  No interpolation, command
        # substitution, escapes, or shell parsing is performed.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[key] = value
    return result


def _operational_value(name: str, *, project_root: Path) -> str | None:
    """Resolve one operational setting from the process or root ``.env``."""

    value = os.environ.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _parse_dotenv(project_root / ".env").get(name)


def _operational_bool(name: str, *, project_root: Path, default: bool = False) -> bool:
    value = _operational_value(name, project_root=project_root)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _operational_int(name: str, *, project_root: Path, default: int, minimum: int, maximum: int) -> int:
    value = _operational_value(name, project_root=project_root)
    return _bounded_int(value, default=default, minimum=minimum, maximum=maximum)


def _alpaca_data_feed(value: str | None) -> str:
    feed = str(value or "iex").strip().casefold() or "iex"
    if feed not in {"iex", "sip"}:
        raise ValueError("ROAD2M_ALPACA_DATA_FEED must be either 'iex' or 'sip'.")
    return feed


def _codex_default() -> str:
    configured = os.environ.get("ROAD2M_CODEX_BINARY")
    if configured and configured.strip():
        return configured.strip()
    # The desktop bundle carries the model catalog shipped with the app.
    # An older standalone CLI may authenticate but reject newer model names.
    for bundled in (
        Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"),
        Path("/Applications/Codex.app/Contents/Resources/codex"),
    ):
        if bundled.is_file() and os.access(bundled, os.X_OK):
            return str(bundled)
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    return str(Path.home() / ".local" / "bin" / "codex")


@dataclass(slots=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    data_dir: Path = field(default_factory=lambda: Path(_known_env("ROAD2M_DATA_DIR", str(PROJECT_ROOT / "data"))))
    db_path: Path | None = None
    evidence_dir: Path | None = None
    backup_dir: Path | None = None
    shared_memory_vault_path: Path | None = field(default_factory=lambda: Path(os.environ["ROAD2M_SHARED_MEMORY_VAULT_PATH"]).expanduser() if os.environ.get("ROAD2M_SHARED_MEMORY_VAULT_PATH", "").strip() else None)
    codex_binary: str = field(default_factory=_codex_default)
    codex_model: str = field(default_factory=lambda: _known_env("ROAD2M_CODEX_MODEL", "gpt-6-luna"))
    codex_reasoning: str = field(default_factory=lambda: _known_env("ROAD2M_CODEX_REASONING", "high"))
    codex_timeout_seconds: int = 900
    ollama_url: str = field(default_factory=lambda: _known_env("ROAD2M_OLLAMA_URL", "http://127.0.0.1:11434"))
    sec_user_agent: str | None = None
    max_event_replay: int = 500
    max_source_bytes: int = 5_000_000
    worker_concurrency: int = 1
    # Reddit intake is a bounded background queue.  Three independent root
    # groups may be admitted by default; each root plus its repair descendants
    # occupies one slot until every member is terminal.
    reddit_parallel_limit: int | None = None
    # Hosted generations share one process-wide pool.  Background work is
    # capped below the global pool so one user generation always has room.
    codex_global_concurrency: int | None = None
    codex_background_concurrency: int | None = None
    # Market/Reddit connectors remain opt-in for the local process.  Tests can
    # inject a connector directly into the orchestrator; production enables
    # live reads only after credentials and provider bounds are verified.
    enable_market_connectors: bool | None = None
    enable_reddit_intake: bool | None = None
    alpaca_data_feed: str | None = None
    cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    )

    def __post_init__(self) -> None:
        self.project_root = Path(self.project_root).expanduser().resolve()
        if self.sec_user_agent is None:
            self.sec_user_agent = _operational_value("ROAD2M_SEC_USER_AGENT", project_root=self.project_root)
        self.sec_user_agent = (self.sec_user_agent or "").strip() or "Road2M local research; contact not configured"
        if self.enable_market_connectors is None:
            self.enable_market_connectors = _operational_bool(
                "ROAD2M_ENABLE_MARKET_CONNECTORS",
                project_root=self.project_root,
                default=False,
            )
        if self.enable_reddit_intake is None:
            self.enable_reddit_intake = _operational_bool(
                "ROAD2M_ENABLE_REDDIT_INTAKE",
                project_root=self.project_root,
                default=False,
            )
        if self.alpaca_data_feed is None:
            self.alpaca_data_feed = _alpaca_data_feed(
                _operational_value("ROAD2M_ALPACA_DATA_FEED", project_root=self.project_root)
            )
        else:
            self.alpaca_data_feed = _alpaca_data_feed(self.alpaca_data_feed)
        self.data_dir = Path(self.data_dir).expanduser().resolve()
        self.shared_memory_vault_path = Path(self.shared_memory_vault_path or self.data_dir / "memory-vault").expanduser().resolve()
        self.db_path = Path(self.db_path or self.data_dir / "road2m.sqlite3").expanduser().resolve()
        self.evidence_dir = Path(self.evidence_dir or self.data_dir / "evidence").expanduser().resolve()
        self.backup_dir = Path(self.backup_dir or self.data_dir / "backups").expanduser().resolve()
        self.codex_timeout_seconds = max(30, int(self.codex_timeout_seconds))
        self.max_event_replay = min(max(1, int(self.max_event_replay)), 5000)
        self.max_source_bytes = min(max(1024, int(self.max_source_bytes)), 50_000_000)
        self.worker_concurrency = min(max(1, int(self.worker_concurrency)), 2)
        # Keep an explicit user lane available even when private bounds are
        # configured.  The defaults are 4 global / 3 background.
        if self.reddit_parallel_limit is None:
            self.reddit_parallel_limit = _operational_int(
                "ROAD2M_REDDIT_PARALLEL_LIMIT", project_root=self.project_root,
                default=3, minimum=1, maximum=8,
            )
        else:
            self.reddit_parallel_limit = _bounded_int(
                self.reddit_parallel_limit, default=3, minimum=1, maximum=8
            )
        if self.codex_global_concurrency is None:
            self.codex_global_concurrency = _operational_int(
                "ROAD2M_CODEX_GLOBAL_CONCURRENCY", project_root=self.project_root,
                default=4, minimum=1, maximum=8,
            )
        else:
            self.codex_global_concurrency = _bounded_int(
                self.codex_global_concurrency, default=4, minimum=1, maximum=8
            )
        if self.codex_background_concurrency is None:
            self.codex_background_concurrency = _operational_int(
                "ROAD2M_CODEX_BACKGROUND_CONCURRENCY", project_root=self.project_root,
                default=3, minimum=0, maximum=7,
            )
        else:
            self.codex_background_concurrency = _bounded_int(
                self.codex_background_concurrency, default=3, minimum=0, maximum=7
            )
        self.codex_background_concurrency = min(
            _bounded_int(self.codex_background_concurrency, default=3, minimum=0, maximum=7),
            max(0, self.codex_global_concurrency - 1),
        )

    def prepare(self) -> None:
        for path in (self.data_dir, self.evidence_dir, self.backup_dir):
            path.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass


settings = Settings()
