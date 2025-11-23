# test_generation/orchestrator/console.py
import io
import logging
import logging.config
import os
import sys
import threading
import traceback
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import Mock

# ---------------- Rich availability (warn, but don't hard-disable) ----------------
RICH_AVAILABLE = False
try:
    _rich_ver = version("rich")
    RICH_AVAILABLE = True
except PackageNotFoundError:
    RICH_AVAILABLE = False
except Exception as e:
    print(
        f"Warning: An error occurred during rich initialization: {e}. "
        "Rich console output will be disabled.",
        file=sys.stderr,
    )
    RICH_AVAILABLE = False

if TYPE_CHECKING:
    from rich.console import Console
else:
    Console = None

# Conditionally import Rich components, with mock fallbacks.
# This makes them valid, importable names from this module.
if RICH_AVAILABLE:
    try:
        from rich.columns import Columns
        from rich.panel import Panel
        from rich.progress import (
            BarColumn,
            Progress,
            TextColumn,
            TimeElapsedColumn,
            TimeRemainingColumn,
        )
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        # Downgrade availability if import fails
        RICH_AVAILABLE = False
        Progress, BarColumn, TimeElapsedColumn, TimeRemainingColumn, TextColumn = (Mock(),) * 5

        # Use simple dummy classes so isinstance(...) is always safe
        class _Dummy:
            pass

        class _DummyText(_Dummy):
            pass

        class _DummyPanel(_Dummy):
            pass

        class _DummyTable(_Dummy):
            pass

        class _DummyColumns(_Dummy):
            pass

        Table, Panel, Text, Columns = (
            _DummyTable,
            _DummyPanel,
            _DummyText,
            _DummyColumns,
        )

        # provide a console for fallback too
        class MockConsole(Mock):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.buf = io.StringIO()

            def print(self, *args, **kwargs):
                self.buf.write(" ".join(str(a) for a in args) + "\n")

            def log(self, *args, **kwargs):
                self.buf.write(" ".join(str(a) for a in args) + "\n")

        Console = MockConsole
else:
    # use dummy classes instead of Mock instances for consistency
    class MockConsole(Mock):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.buf = io.StringIO()

        def print(self, *args, **kwargs):
            output = " ".join(str(arg) for arg in args)
            self.buf.write(output + "\n")

        def log(self, *args, **kwargs):
            output = " ".join(str(arg) for arg in args)
            self.buf.write(output + "\n")

    Progress, BarColumn, TimeElapsedColumn, TimeRemainingColumn, TextColumn = (Mock(),) * 5

    class _Dummy:
        pass

    class _DummyText(_Dummy):
        pass

    class _DummyPanel(_Dummy):
        pass

    class _DummyTable(_Dummy):
        pass

    class _DummyColumns(_Dummy):
        pass

    Table, Panel, Text, Columns = _DummyTable, _DummyPanel, _DummyText, _DummyColumns

    Console = MockConsole


# ---------------- Module globals ----------------
logger = logging.getLogger(__name__)
audit_logger_instance = logging.getLogger("atco_audit")

_log_lock = threading.Lock()
_FORCE_PLAIN = os.getenv("ATCO_PLAIN_LOGGING") == "1"

console: Optional["Console"] = None

GLYPHS: Dict[str, str] = {}
LOG_STYLES: Dict[str, Any] = {}

# Define a custom logging level for SUCCESS and wire it
SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")
logging.SUCCESS = SUCCESS  # expose numeric level for getattr lookups


def _logger_success(self, msg, *args, **kwargs):
    if self.isEnabledFor(SUCCESS):
        self._log(SUCCESS, msg, args, **kwargs)


logging.Logger.success = _logger_success

_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "SUCCESS": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def init_console_and_styles(cfg: Dict[str, Any] | None = None) -> None:
    """Initialize glyphs/log styles based on current stdout encoding."""
    cfg = cfg or {}
    global GLYPHS, LOG_STYLES
    supports_utf8 = "UTF" in (getattr(sys.stdout, "encoding", "") or "").upper()

    default_unicode = {"check": "✓", "warn": "▲", "x": "✗"}
    default_ascii = {"check": "[OK]", "warn": "[!]", "x": "[X]"}

    GLYPHS = (
        cfg.get("console_glyphs", default_unicode)
        if supports_utf8
        else cfg.get("console_ascii_glyphs", default_ascii)
    )
    LOG_STYLES = cfg.get(
        "log_styles",
        {
            "INFO": {"console_style": "default"},
            "SUCCESS": {"console_style": "bold green"},
            "WARNING": {"console_style": "yellow"},
            "ERROR": {"console_style": "red"},
            "CRITICAL": {"console_style": "bold white on red"},
        },
    )


def _format_msg(msg: str, level: str) -> str:
    lu = level.upper()
    if lu == "SUCCESS":
        return f"{GLYPHS.get('check', '[OK]')} {msg}"
    if lu == "WARNING":
        return f"{GLYPHS.get('warn', '[!]')} {msg}"
    if lu == "ERROR":
        return f"{GLYPHS.get('x', '[X]')} {msg}"
    if lu == "CRITICAL":
        return f"CRITICAL: {msg}"
    return msg


def _ensure_console() -> Optional["Console"]:
    """Instantiate a Console using the module-level symbol (so tests can patch it)."""
    global Console, console
    if not RICH_AVAILABLE:
        # FIX: Ensure a mock console is created even if rich is not available.
        if console is None:
            console = Console()
        return console
    try:
        if Console is None:
            from rich.console import Console as _C

            Console = _C
        if console is None:
            console = Console()
        return console
    except Exception as e:
        print(f"Error initializing rich.Console: {e}", file=sys.stderr)
        console = None
        return None


def _log_print_backend(message: Any, level: str, style: Optional[str] = None) -> None:
    """Fallback backend that logs formatted messages directly to the logging framework."""
    lu = level.upper()
    # Normalize level using the map
    levelno = _LEVEL_MAP.get(lu, logging.INFO)
    formatted = _format_msg(str(message), level)
    logger.log(levelno, formatted)


def _log_rich_backend(message: Any, level: str, style: Optional[str] = None) -> None:
    """Backend for rich console output, which also logs to the file framework."""
    # Log raw message for file handlers first
    log_msg = str(message.plain) if isinstance(message, Text) else str(message)
    # Log to the appropriate level, defaulting to INFO if a custom level isn't found
    levelno = _LEVEL_MAP.get(level.upper(), logging.INFO)
    logger.log(levelno, log_msg)

    # Then log formatted message to rich console
    renderable = message
    if console:
        # Allow passing pre-constructed rich renderables
        if not isinstance(message, (str, bytes, Text, Panel, Table, Columns)):
            renderable = str(message)
        elif isinstance(message, str):
            renderable = _format_msg(message, level)

        style_info = LOG_STYLES.get(level.upper(), {})
        final_style = style or style_info.get("console_style", "default")

        # Use print for all rich renderables
        console.print(renderable, style=final_style)
    else:
        # Fallback if console disappears after being enabled
        _log_print_backend(message, level, style)


_log_backend: Callable[[Any, str, Optional[str]], None] = _log_print_backend


def set_plain_logging(force: bool = True) -> None:
    global _log_backend
    with _log_lock:
        _log_backend = _log_print_backend


def set_rich_logging(force: bool = True) -> None:
    global _log_backend
    if _FORCE_PLAIN:
        set_plain_logging(True)
        return
    with _log_lock:
        c = _ensure_console()
        if RICH_AVAILABLE and c is not None:
            _log_backend = _log_rich_backend
        else:
            _log_backend = _log_print_backend


def rich_enabled() -> bool:
    return _log_backend == _log_rich_backend


init_console_and_styles()
if _FORCE_PLAIN:
    set_plain_logging()
else:
    set_rich_logging()


def log(message: Any, level: str = "INFO", style: Optional[str] = None) -> None:
    """Dispatches a message or rich renderable to the configured backend."""
    with _log_lock:
        _log_backend(message, level, style)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or __name__)


def fallback_to_basic_logging() -> None:
    """Switches to the plain backend and ensures basic logging is configured."""
    set_plain_logging(True)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    # Set up a custom LogRecordFactory for consistent message formatting
    orig_factory = logging.getLogRecordFactory()

    def _factory(*args, **kwargs):
        record = orig_factory(*args, **kwargs)
        # prefix formatting for message attribute (so tests see it in logger.handle)
        if record.levelno >= logging.ERROR:
            prefix = "[!]"
        elif record.levelno >= logging.WARNING:
            prefix = "[!]"
        else:
            prefix = "[OK]"
        # Ensure .message exists even before any Formatter runs:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        record.message = f"{prefix} {msg}"
        return record

    logging.setLogRecordFactory(_factory)


def configure_logging(
    logging_config: Dict[str, Any],
    audit_log_file: str,
    audit_handler_name: str = "audit_file",
    error_log_path: Optional[str] = None,
) -> None:
    """Attempt dictConfig; on failure, fall back and still emit a human message."""
    try:
        if not isinstance(logging_config, dict) or "handlers" not in logging_config:
            raise ValueError("Invalid logging_config schema: missing 'handlers'.")

        handlers = logging_config["handlers"]
        if audit_handler_name in handlers and "filename" in handlers[audit_handler_name]:
            audit_log_dir = os.path.dirname(audit_log_file)
            if audit_log_dir:
                os.makedirs(audit_log_dir, exist_ok=True)
            handlers[audit_handler_name]["filename"] = audit_log_file

        logging.config.dictConfig(logging_config)
        global audit_logger_instance
        audit_logger_instance = logging.getLogger("atco_audit")
        set_rich_logging()
    except Exception as e:
        fallback_to_basic_logging()
        log(f"Failed to set up structured logging: {e}", "ERROR")

        if error_log_path:
            try:
                with open(error_log_path, "a", encoding="utf-8") as f:
                    f.write(f"ERROR: Failed to set up structured logging: {e}\n")
                    traceback.print_exc(file=f)
            except Exception as fe:
                log(
                    f"Failed to write fallback error to file '{error_log_path}': {fe}",
                    "ERROR",
                )


# ---------------- Progress bars ----------------
class _ProgressTask:
    def __init__(self, progress, task_id):
        self._p = progress
        self.task_id = task_id

    def update(self, advance: float = 1.0) -> None:
        try:
            self._p.update(self.task_id, advance=advance)
        except Exception:
            pass


@contextmanager
def log_progress_bars(title: str, tasks: List[Tuple[str, int]]):
    """Rich or plain-text progress bar context manager."""
    if rich_enabled() and RICH_AVAILABLE:
        p = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console or _ensure_console(),
            transient=True,
        )
        try:
            with p as real_progress:
                task_map = {}
                for name, total in tasks:
                    task_id = real_progress.add_task(description=name, total=total)
                    task_map[name] = _ProgressTask(real_progress, task_id)
                yield task_map
                return
        except Exception:
            # Fall through to plain text if rich progress fails for any reason
            pass

    # Plain text fallback
    log(f"Starting: {title} ({', '.join(n for n, _ in tasks)})", "INFO")

    class _MockTask:
        def __init__(self, name, total):
            self.task_id = name
            self.total = total

        def update(self, advance: float = 1.0) -> None:
            pass

    try:
        yield {name: _MockTask(name, total) for name, total in tasks}
    finally:
        try:
            log(f"Finished: {title}", "INFO")
        except Exception:
            pass
