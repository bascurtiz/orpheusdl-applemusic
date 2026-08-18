import os
import sys
import time
import re
import ast
import json
import inspect
import shutil
import platform
import tempfile
import subprocess
import threading
import concurrent.futures
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional, List
from contextlib import contextmanager, nullcontext
import asyncio

# gamdl is a pip dependency (gamdl>=3.8.5 ships a compiled Rust decrypt/mux engine
# as abi3 wheels, so it cannot be vendored as source).


def _pip_gamdl_available() -> bool:
    """Return True when gamdl is importable from the Python environment (pip install)."""
    try:
        import gamdl  # noqa: F401
        return True
    except ImportError:
        return False


def _gamdl_supports_tcp_decrypt() -> bool:
    """Return True when the imported gamdl accepts WrapperApi.create decrypt_host/decrypt_port."""
    try:
        params = inspect.signature(WrapperApi.create).parameters
        return 'decrypt_host' in params and 'decrypt_port' in params
    except Exception:
        return False


def _first(payload):
    """Unwrap an Apple Music API response like {'data': [item]} to its first item."""
    if isinstance(payload, dict) and payload.get('data'):
        return payload['data'][0]
    return payload


def _label_from_copyright(copyright_info: str) -> str:
    """Best-effort record label from a copyright line: strip ℗/©/(P)/(C) symbols and years."""
    label = re.sub(r'^(?:℗|©|p|c|\(p\)|\(c\)|\u2117|\u00a9)\s*(?:\d{4})?\s*', '', copyright_info, flags=re.IGNORECASE).strip()
    return re.sub(r'^\d{4}\s*', '', label).strip()


def _filter_standard_lossless(playlists: list) -> list:
    """Keep only ≤48kHz ALAC playlists (standard lossless, not hi-res).

    Playlists whose audio group ID has no parseable sample rate are kept as-is.
    """
    filtered = []
    for p in playlists:
        try:
            parts = p["stream_info"]["audio"].split('-')
            if len(parts) < 4 or int(parts[-2]) <= 48000:
                filtered.append(p)
        except Exception:
            filtered.append(p)
    return filtered


# Substrings that indicate the local decryption wrapper (WV2D/amdecrypt) is not reachable.
_WRAPPER_CONN_ERROR_MARKERS = (
    "10061", "127.0.0.1", "connectionrefused", "refused", "geweigerd", "dial tcp", "connect error",
)


# Initialize gamdl availability check
GAMDL_AVAILABLE = False
LAST_GAMDL_ERROR = None

# Names _lazy_import_gamdl() publishes into module globals once gamdl is importable.
_GAMDL_NAMES = (
    'AppleMusicApi', 'ItunesApi', 'WrapperApi', 'GamdlSongCodec', 'GamdlDownloadMode',
    'AppleMusicDownloader', 'AppleMusicBaseDownloader', 'AppleMusicSongDownloader',
    'AppleMusicMusicVideoDownloader', 'AppleMusicUploadedVideoDownloader',
    'AppleMusicBaseInterface', 'AppleMusicInterface', 'AppleMusicSongInterface',
    'AppleMusicMusicVideoInterface', 'AppleMusicUploadedVideoInterface',
    'AppleMusicMedia', 'SyncedLyricsFormat',
)
for _name in _GAMDL_NAMES:
    globals()[_name] = None

def _lazy_import_gamdl():
    """Lazy import gamdl components to avoid conflicts with GUI patches"""
    global GAMDL_AVAILABLE, LAST_GAMDL_ERROR

    if GAMDL_AVAILABLE:
        return True

    # GUI builds patch/replace click & InquirerPy; gamdl imports them at import time.
    # A universal mock (any attribute, any call, iterable) satisfies those imports,
    # including 'from ... import ...' for nested modules via sys.modules entries.
    class _UniversalMock:
        def __init__(self, *args, **kwargs): pass
        def __call__(self, *args, **kwargs): return self
        def __getattr__(self, name): return self
        def __iter__(self): yield from ()

    _mock_instance = _UniversalMock()
    for mod_name in ('click', 'colorama', 'InquirerPy', 'inquirerpy',
                     'InquirerPy.base', 'InquirerPy.base.control',
                     'inquirerpy.base', 'inquirerpy.base.control'):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = _mock_instance

    if not _pip_gamdl_available():
        print("[Apple Music Error] gamdl is not installed — run: pip install gamdl>=3.8.5")
        globals()['LAST_GAMDL_ERROR'] = "gamdl not installed — run: pip install gamdl>=3.8.5"
        return False

    # The GUI may have patched subprocess.Popen into a plain function, which breaks
    # yt-dlp (it subclasses Popen). Wrap it in a class for the duration of the import.
    original_popen = None
    subprocess_module = sys.modules.get('subprocess')
    if subprocess_module and hasattr(subprocess_module, 'Popen'):
        current_popen = subprocess_module.Popen
        if not isinstance(current_popen, type):
            class TempPopen:
                def __new__(cls, *args, **kwargs):
                    return current_popen(*args, **kwargs)
                def __init__(self, *args, **kwargs):
                    pass

            if os.environ.get('GAMDL_DEBUG') == 'true':
                print("[Apple Music Debug] Temporarily replacing patched subprocess.Popen with class wrapper for yt-dlp compatibility")
            subprocess_module.Popen = TempPopen
            original_popen = current_popen

    try:
        from gamdl.api import AppleMusicApi, ItunesApi, WrapperApi
        from gamdl.downloader import (
            AppleMusicBaseDownloader,
            AppleMusicDownloader,
            AppleMusicMusicVideoDownloader,
            AppleMusicSongDownloader,
            AppleMusicUploadedVideoDownloader,
        )
        from gamdl.interface import (
            AppleMusicBaseInterface,
            AppleMusicInterface,
            AppleMusicMusicVideoInterface,
            AppleMusicSongInterface,
            AppleMusicUploadedVideoInterface,
            AppleMusicMedia,
        )
        from gamdl.interface.enums import (
            SongCodec as GamdlSongCodec,
            SyncedLyricsFormat
        )
        from gamdl.downloader.enums import (
            DownloadMode as GamdlDownloadMode,
        )

        for name in _GAMDL_NAMES:
            globals()[name] = locals()[name]

        class OrpheusAppleMusicSongInterface(AppleMusicSongInterface):
            def __init__(self, base: AppleMusicBaseInterface, quality_tier: QualityEnum = None, debug: bool = False, **kwargs):
                super().__init__(base, **kwargs)
                self.quality_tier = quality_tier
                self._debug = debug

            def _get_playlist_from_codec_enhanced(self, m3u8_data: dict, codec: 'GamdlSongCodec') -> dict | None:
                from gamdl.interface.constants import SONG_CODEC_REGEX_MAP

                def matches(key):
                    return [p for p in m3u8_data["playlists"]
                            if re.fullmatch(SONG_CODEC_REGEX_MAP[key], p["stream_info"]["audio"])]

                # Atmos falls back to plain AC-3 (Atmos/Surround) when no atmos stream exists
                if codec.value == 'atmos':
                    matching_playlists = matches('atmos') or matches('ac3')
                else:
                    matching_playlists = matches(codec.value)

                if not matching_playlists:
                    if self._debug:
                        flavors = [p["stream_info"]["audio"] for p in m3u8_data["playlists"]]
                        print(f"[Apple Music Debug] No matching playlist for codec '{codec.value}'. Available flavors: {flavors}")
                    return None

                # Standard-lossless requests cap at 48kHz so HI-RES (96k+) variants are excluded.
                if codec.value == "alac" and self.quality_tier == QualityEnum.LOSSLESS:
                    filtered = _filter_standard_lossless(matching_playlists)
                    if filtered:
                        matching_playlists = filtered
                    else:
                        print(f"[Apple Music Debug] No playlists matched sample_rate <= 48000. Returning best available.")

                return max(
                    matching_playlists,
                    key=lambda x: x["stream_info"]["average_bandwidth"],
                )

        globals()['OrpheusAppleMusicSongInterface'] = OrpheusAppleMusicSongInterface
        globals()['GAMDL_AVAILABLE'] = True
        LAST_GAMDL_ERROR = None

        # gamdl 3.8.2+ moved decryption to the WV2D TCP endpoint; the vendored copy
        # cannot work without the compiled Rust extension, so warn when the installed
        # gamdl is too old for the wrapper-v2 API 0.0.2 protocol.
        if not _gamdl_supports_tcp_decrypt():
            print(
                "[Apple Music Warning] Installed gamdl is older than 3.8.2 — wrapper decryption "
                "will use the legacy HTTP /decrypt endpoint. Run: pip install -U gamdl"
            )
        return True
    except ImportError as e:
        error_msg = f"ImportError: {e}"
        print(f"[Apple Music] Warning: Could not import gamdl components: {error_msg}")
        if os.environ.get('GAMDL_DEBUG') == 'true':
            import traceback
            traceback.print_exc()
        globals()['LAST_GAMDL_ERROR'] = error_msg
        return False
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"[Apple Music] Error during gamdl import: {error_msg}")
        import traceback
        traceback.print_exc()
        globals()['LAST_GAMDL_ERROR'] = error_msg
        return False
    finally:
        if original_popen and subprocess_module:
            if os.environ.get('GAMDL_DEBUG') == 'true':
                print("[Apple Music Debug] Restoring patched subprocess.Popen")
            subprocess_module.Popen = original_popen

from utils.models import *
from utils.utils import (
    artists_from_apple_attrs,
    format_album_artist_tag,
    resolve_album_artist_tag,
)

from utils.models import (
    TrackInfo, AlbumInfo, ArtistInfo, PlaylistInfo, LyricsInfo,
    DownloadTypeEnum, QualityEnum,
    DownloadEnum,
    ModuleInformation, ModuleModes, ManualEnum, Tags, CodecEnum,
    TrackDownloadInfo, ModuleController, OrpheusOptions,
    CreditsInfo, CoverInfo, CoverOptions, ImageFileTypeEnum
)
from utils.exceptions import AuthenticationError, DownloadError, TrackUnavailableError

DEFAULT_WRAPPER_URL = "127.0.0.1"
_LEGACY_WRAPPER_HOSTS = frozenset({
    "127.0.0.1:10020",
    "localhost:10020",
    "0.0.0.0:10020",
})
# wrapper-v2 worker listens on 18080 inside the container; the public HTTP API is on port 80.
_WRONG_WRAPPER_PUBLIC_PORTS = frozenset({
    "127.0.0.1:18080",
    "localhost:18080",
    "0.0.0.0:18080",
})

def normalize_wrapper_url(url: str | None) -> str:
    """Normalize wrapper-v2 base URL (HTTP on port 80 by default)."""
    normalized = (url or "").strip() or DEFAULT_WRAPPER_URL
    bare = normalized.replace("http://", "").replace("https://", "").rstrip("/")
    if bare in _LEGACY_WRAPPER_HOSTS or bare in _WRONG_WRAPPER_PUBLIC_PORTS:
        bare = DEFAULT_WRAPPER_URL
    elif bare.endswith(":18080") or bare.endswith(":10020"):
        bare = bare.rsplit(":", 1)[0] or DEFAULT_WRAPPER_URL
    return f"http://{bare}".rstrip("/")

module_information = ModuleInformation(
    service_name='Apple Music',
    module_supported_modes=ModuleModes.download | ModuleModes.lyrics | ModuleModes.covers | ModuleModes.credits,
    session_settings={
        'cookies_path': './config/cookies.txt',
        'language': 'en-US',
        'use_wrapper': False,
        # Optional: paste the 'media-user-token' cookie value directly instead of a
        # cookies.txt export. Same token gamdl extracts from the cookies file.
        'media_user_token': '',
        # Base URL of wrapper-v2 (HTTP control API, port 80 by default). The WV2D
        # TCP decrypt host/port are derived from this URL automatically.
        'wrapper_decrypt_ip': DEFAULT_WRAPPER_URL,
        'wrapper_restart_command': ''
    },
    netlocation_constant='music.apple',
    test_url='https://music.apple.com/us/album/1989-taylors-version/1708308989',
    url_decoding=ManualEnum.manual,
    login_behaviour=ManualEnum.manual
)

@contextmanager
def suppress_gamdl_debug():
    """Context manager to suppress verbose gamdl debug messages.

    All gamdl work runs on one background event-loop thread, so we keep a
    per-thread reference count with a single shared devnull. The outermost
    entry captures the current sys.stdout (the GUI's QueueWriter for this
    download) and the outermost exit restores it; nested/interleaved contexts
    just increment the count and keep pointing at the same open devnull.

    This fixes two bugs in the old code:
      * the captured stdout was cached once per thread and never refreshed, so
        after the first download it restored sys.stdout to a stale QueueWriter
        and swallowed the rest of that download's output (incl. the summary);
      * per-context devnulls could be closed while an outer context was still
        active, leaving sys.stdout pointing at a closed file.
    """
    thread = threading.current_thread()
    depth = getattr(thread, '_gamdl_stdout_depth', 0)
    if depth == 0:
        thread._gamdl_stdout_original = sys.stdout
        thread._gamdl_stdout_devnull = open(os.devnull, 'w')
    thread._gamdl_stdout_depth = depth + 1

    try:
        sys.stdout = thread._gamdl_stdout_devnull
        yield
    finally:
        thread._gamdl_stdout_depth -= 1
        if thread._gamdl_stdout_depth == 0:
            sys.stdout = thread._gamdl_stdout_original
            thread._gamdl_stdout_devnull.close()
            thread._gamdl_stdout_devnull = None
        else:
            # Still suppressed by an outer context: keep the shared devnull.
            sys.stdout = thread._gamdl_stdout_devnull

def _get_original_stdout():
    """Helper to get the real stdout even while gamdl output is suppressed."""
    thread = threading.current_thread()
    if getattr(thread, '_gamdl_stdout_depth', 0) > 0:
        return getattr(thread, '_gamdl_stdout_original', sys.stdout)
    return sys.stdout

_gamdl_structlog_sink = None

def _gamdl_drop_processor(logger, method_name, event_dict):
    """Drop all structlog events (used when debug is off)."""
    import structlog
    raise structlog.DropEvent

def _configure_gamdl_structlog(debug: bool) -> None:
    """Silence gamdl structlog output unless Apple Music / global debug is enabled."""
    global _gamdl_structlog_sink
    try:
        import logging
        import structlog
    except ImportError:
        return

    if _gamdl_structlog_sink is None:
        _gamdl_structlog_sink = open(os.devnull, 'w')

    if debug:
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.dev.ConsoleRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            logger_factory=structlog.PrintLoggerFactory(file=_get_original_stdout()),
            cache_logger_on_first_use=False,
        )
        return

    structlog.configure(
        processors=[_gamdl_drop_processor],
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        logger_factory=structlog.PrintLoggerFactory(file=_gamdl_structlog_sink),
        cache_logger_on_first_use=False,
    )

# Default to quiet until a module instance syncs with user settings.
_configure_gamdl_structlog(False)

class ModuleInterface:
    def __init__(self, module_controller: ModuleController):
        self.exception = module_controller.module_error
        settings = module_controller.module_settings
        self.module_controller = module_controller
        self.settings = settings
        self.printer = module_controller.printer_controller
        self.gamdl_downloader_song = None
        self.gamdl_downloader = None
        self.is_authenticated = False
        # Consolidate debug setting from module-specific and global settings
        self._debug = settings.get('debug', False) or module_controller.orpheus_options.debug_mode
        self.quality_tier = None

        # Lock for synchronizing async operations across threads
        self._lock = threading.Lock()

        # Persistent event loop and thread for async operations to avoid asyncio.run() overhead
        self._loop_ready = threading.Event()
        self.loop = None
        self.loop_thread = None
        self._start_background_loop()

        # Cache for wrapper health to avoid redundant timeouts
        self._wrapper_offline = False
        self._last_gamdl_init_error = None

        if not _lazy_import_gamdl():
            detail = f": {LAST_GAMDL_ERROR}" if LAST_GAMDL_ERROR else ""
            raise self.exception(f"gamdl components not available - please check installation{detail}")

        self._refresh_debug_mode()

        cookies_path = self._cookies_path()
        if cookies_path is None and self._debug:
            print(f"[Apple Music Warning] Cookies file not found at specified/default path: "
                  f"{self.settings.get('cookies_path', './config/cookies.txt')}. "
                  "Downloads may fail if authentication is required.")

        if self._debug: print(f"[Apple Music Debug] Using cookies_path: {os.path.abspath(cookies_path) if cookies_path else 'None'}")

        # Initialize gamdl APIs. On failure we swallow the error here and retry
        # during the first actual operation (self-healing in _run_async).
        try:
            self._run_async(self._setup_api_clients, allow_reinit=False)
        except Exception as e:
            if self._is_ssl_certificate_error(e):
                if platform.system() == "Darwin":  # macOS
                    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
                    raise self.exception(
                        f"SSL Certificate Error on macOS detected!\n\n"
                        f"To fix this issue, run this command in Terminal:\n"
                        f"open '/Applications/Python {python_version}/Install Certificates.command'\n\n"
                        f"Or install certificates manually:\n"
                        f"pip3 install --upgrade certifi\n\n"
                        f"This is a known macOS issue where Python doesn't use system certificates by default.\n"
                        f"Original error: {e}"
                    )
                raise self.exception(
                    f"SSL Certificate Error detected!\n\n"
                    f"Try updating certificates with:\n"
                    f"pip3 install --upgrade certifi\n\n"
                    f"Original error: {e}"
                )
            print(f"[Apple Music Error] Initial initialization failed: {e}")

    def _refresh_debug_mode(self):
        """Sync gamdl logging with current module/global debug settings."""
        self._debug = bool(
            self.settings.get('debug', False)
            or getattr(self.module_controller.orpheus_options, 'debug_mode', False)
        )
        os.environ['GAMDL_DEBUG'] = 'true' if self._debug else 'false'
        _configure_gamdl_structlog(self._debug)

    def _gamdl_quiet(self):
        """Context manager: suppress gamdl stdout noise when debug is off."""
        return nullcontext() if self._debug else suppress_gamdl_debug()

    def _is_ssl_certificate_error(self, exception) -> bool:
        """Return True when an exception looks like an SSL certificate verification failure."""
        error_str = str(exception).lower()
        ssl_error_indicators = [
            "certificate verify failed",
            "ssl: certificate_verify_failed",
            "unable to get local issuer certificate",
            "certificate_verify_failed",
            "ssl certificate problem",
        ]
        return any(indicator in error_str for indicator in ssl_error_indicators)

    def _cookies_path(self) -> Optional[Path]:
        """Configured cookies path if it exists, else ./config/cookies.txt, else None."""
        configured = self.settings.get('cookies_path', './config/cookies.txt')
        path = Path(configured) if configured else None
        if not path or not path.exists():
            default = Path('./config/cookies.txt')
            path = default if default.exists() else None
        return path

    def _read_orpheus_settings(self) -> dict:
        """Read the main OrpheusDL config/settings.json (empty dict if unreadable)."""
        try:
            with open(Path("./config/settings.json"), encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            if self._debug: print(f"[Apple Music Debug] Could not read config/settings.json: {e}")
            return {}

    def _start_background_loop(self):
        """Start or restart the background event loop thread."""
        with self._lock:
            if self.loop_thread and self.loop_thread.is_alive() and self.loop and not self.loop.is_closed():
                if self._debug: print(f"[Apple Music Debug] Background loop already healthy (Loop ID: {id(self.loop)})")
                return

            # Clear gamdl caches + null out interfaces/downloader/API clients to force
            # re-initialization in the new loop ("different event loop" protection)
            self._clear_gamdl_caches()
            self.apple_music_api = None
            self.itunes_api = None
            self.wrapper_api = None

            self._wrapper_offline = False

            if self._debug: print(f"[Apple Music Debug] Starting background event loop thread...")

            self._loop_ready.clear()
            self.loop_thread = threading.Thread(target=self._run_loop, daemon=True, name="AppleMusicLoop")
            self.loop_thread.start()
            # Wait up to 5 seconds for the loop to start
            if not self._loop_ready.wait(5):
                 print("[Apple Music Error] Background loop failed to start within 5 seconds.")
            elif self._debug:
                 print(f"[Apple Music Debug] Background loop started successfully (Loop ID: {id(self.loop)})")

    def _run_loop(self):
        """Internal loop runner for the background thread."""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            if self._debug: print(f"[Apple Music Debug] Background loop created and set. Loop id: {id(self.loop)}")
            self._loop_ready.set()
            self.loop.run_forever()
        except Exception as e:
            print(f"[Apple Music Error] Background loop thread crashed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self._debug: print("[Apple Music Debug] Background loop thread exiting...")
            self._loop_ready.clear()

    def _run_async(self, func, *args, **kwargs):
        """Run an async function or lambda in the internal event loop thread and return the result."""
        allow_reinit = kwargs.pop('allow_reinit', True)

        # Already inside the background loop thread: future.result() would block the
        # loop, so run the function directly instead of scheduling it.
        if threading.current_thread() == self.loop_thread:
            if asyncio.iscoroutinefunction(func):
                if self._debug: print("[Apple Music Warning] Nested async _run_async call detected! Attempting to run in current loop...")
                return asyncio.run_coroutine_threadsafe(func(*args, **kwargs), self.loop).result()
            return func(self, *args, **kwargs)

        target_sf = kwargs.pop('storefront', None)

        for attempt in range(4): # Increased to 4 attempts to allow for 3 retries with backoff
            # 1. Ensure thread is alive and loop is valid
            if not self.loop_thread or not self.loop_thread.is_alive() or not self.loop or self.loop.is_closed():
                if self._debug and attempt > 0:
                    print(f"[Apple Music Debug] Retrying _run_async (attempt {attempt+1}) due to loop closure/failure...")
                self._start_background_loop()

            async def wrapper():
                # If APIs are missing, initialize them first (self-healing)
                if allow_reinit and not getattr(self, 'apple_music_api', None):
                    if self._debug: print("[Apple Music Debug] Re-establishing API clients for operation...")
                    await self._setup_api_clients()

                am_api = getattr(self, 'apple_music_api', None)
                it_api = getattr(self, 'itunes_api', None)

                sf = target_sf or getattr(am_api, 'storefront', None) or getattr(self, 'account_storefront', 'us')

                try:
                    # Update storefront if different
                    if am_api and sf and getattr(am_api, 'storefront', None) != sf:
                        am_api.storefront = sf
                    if it_api and sf and getattr(it_api, 'storefront', None) != sf:
                        it_api.storefront = sf

                    # Run the target function
                    if asyncio.iscoroutinefunction(func):
                        return await func(*args, **kwargs)
                    else:
                        res = func(self, *args, **kwargs)
                        if asyncio.iscoroutine(res):
                            return await res
                        return res
                except Exception as inner_e:
                    # Propagate inner exceptions
                    return inner_e

            try:
                # 2. Schedule and wait
                if self._debug: print(f"[Apple Music Debug] Scheduling coroutine on loop {id(self.loop)} (Thread: {self.loop_thread.name if self.loop_thread else 'None'})")

                future = asyncio.run_coroutine_threadsafe(wrapper(), self.loop)
                if self._debug: print(f"[Apple Music Debug] Scheduled coroutine. Waiting for result (Timeout: 1200s)...")
                result = future.result(timeout=1200)
                if self._debug: print(f"[Apple Music Debug] Coroutine finished with result type: {type(result).__name__}")

                # 3. Handle propagated exceptions
                if isinstance(result, Exception):
                    # Apple Music API rate limit (429): gamdl ApiError or other TooManyRequests type
                    is_rate_limit = (
                        ('ApiError' in type(result).__name__ and getattr(result, 'status_code', None) == 429)
                        or 'TooManyRequests' in type(result).__name__
                    )

                    if is_rate_limit and attempt < 3:
                        backoff_times = [2, 5, 10]
                        wait_time = backoff_times[attempt]
                        if self._debug: print(f"[Apple Music Warning] Rate limit (429) detected. Retrying in {wait_time}s... (Attempt {attempt+1}/4)")
                        time.sleep(wait_time)
                        continue

                    result_str = str(result)
                    if "Multiple cookies exist with name=" in result_str and attempt < 3:
                        cookie_name = result_str.split("name=")[-1].strip().strip("'\"")
                        if self._debug: print(f"[Apple Music Warning] Cookie conflict detected for '{cookie_name}'. Attempting self-healing...")
                        try:
                            am_api = getattr(self, 'apple_music_api', None)
                            if am_api and hasattr(am_api, 'client') and hasattr(am_api.client, 'cookies'):
                                jar = am_api.client.cookies.jar
                                cookies_to_remove = [c for c in jar if c.name == cookie_name]
                                for c in cookies_to_remove:
                                    try:
                                        jar.clear(c.domain, c.path, c.name)
                                    except: pass
                                if self._debug: print(f"[Apple Music Debug] Cleared {len(cookies_to_remove)} conflicting '{cookie_name}' cookies.")
                        except Exception as ce:
                            if self._debug: print(f"[Apple Music Error] Failed to clear conflicting cookies: {ce}")
                        continue

                    if "closed" in result_str.lower() and isinstance(result, RuntimeError):
                        if self._debug: print(f"[Apple Music Warning] background thread returned closed loop error: {result}")
                        self.loop = None
                        self.loop_thread = None
                        continue
                    raise result
                return result

            except (RuntimeError, TimeoutError, concurrent.futures.TimeoutError, AttributeError) as e:
                # If it's an AttributeError involving 'send', it's likely a dead transport on a closed loop
                is_dead_transport = isinstance(e, AttributeError) and ('send' in str(e) or 'recv' in str(e))

                if self._debug: print(f"[Apple Music Debug] _run_async caught {type(e).__name__}: {e}")

                if isinstance(e, RuntimeError) or is_dead_transport:
                    # Force restart loop and APIs on next attempt
                    with self._lock:
                        if self.loop:
                            try: self.loop.stop()
                            except: pass
                        self.loop = None
                        if hasattr(self, 'apple_music_api'): self.apple_music_api = None

                if attempt == 3: # Last attempt (4 total)
                    raise e
                continue

        raise RuntimeError("Apple Music: Failed to execute async operation after 4 attempts (including rate limit retries).")

    def _clear_gamdl_caches(self):
        """Clear alru_cache in gamdl interfaces to prevent loop-mismatch errors"""
        interfaces = [getattr(self, name, None) for name in
                      ('gamdl_base_interface', 'gamdl_interface', 'gamdl_song_interface')]

        for name in ('gamdl_base_interface', 'gamdl_interface', 'gamdl_song_interface',
                     'gamdl_song_downloader', 'gamdl_downloader', 'gamdl_downloader_song',
                     'gamdl_base_downloader'):
            setattr(self, name, None)

        interface_classes = []
        if GAMDL_AVAILABLE:
            try:
                from gamdl.interface.base import AppleMusicBaseInterface
                from gamdl.interface.interface import AppleMusicInterface
                from gamdl.interface.song import AppleMusicSongInterface
                interface_classes = [AppleMusicBaseInterface, AppleMusicInterface, AppleMusicSongInterface]
            except Exception:
                pass

        for obj in (interfaces + interface_classes):
            if obj:
                for attr_name in dir(obj):
                    try:
                        attr = getattr(obj, attr_name)
                        if hasattr(attr, 'cache_clear'):
                            # async-lru check: reset the loop to None before clearing
                            # to avoid "alru_cache is not safe to use across event loops" RuntimeError.
                            actual_func = getattr(attr, '__func__', attr)
                            if hasattr(actual_func, '_loop'):
                                actual_func._loop = None
                            attr.cache_clear()
                    except:
                        pass

    def _set_storefront(self, country_code: Optional[str]):
        """Sets the storefront for API calls if a country code is provided."""
        if not country_code:
            return
        country_code_lower = country_code.lower()
        if self.apple_music_api and self.apple_music_api.storefront != country_code_lower:
            if self._debug: print(f"[Apple Music Debug] Switching storefront from {self.apple_music_api.storefront} to {country_code_lower}")
            self.apple_music_api.storefront = country_code_lower
            if self.itunes_api:
                self.itunes_api.storefront = country_code_lower

    def _get_gamdl_codec(self, codec_str: str):
        """Map codec string to gamdl SongCodec enum"""
        codec_lower = (codec_str or '').lower()
        if codec_lower == 'alac' or 'alac-' in codec_lower:
            return GamdlSongCodec.ALAC
        if codec_lower == 'atmos':
            return GamdlSongCodec.ATMOS
        return GamdlSongCodec.AAC_WEB

    def _quality_to_codec(self, quality_tier: QualityEnum):
        """Map OrpheusDL QualityEnum to gamdl SongCodec enum"""
        if not quality_tier:
            return None
        if quality_tier & QualityEnum.ATMOS:
            return GamdlSongCodec.ATMOS
        if quality_tier & (QualityEnum.LOSSLESS | QualityEnum.HIFI):
            return GamdlSongCodec.ALAC
        # LOW, MEDIUM, HIGH and MINIMUM all map to standard AAC 256
        return GamdlSongCodec.AAC_WEB

    def _get_wrapper_url(self) -> str:
        return normalize_wrapper_url(self.settings.get('wrapper_decrypt_ip'))

    def _get_wrapper_decrypt_host(self) -> str:
        """TCP host for WV2D batch decryption (wrapper-v2), derived from the wrapper URL.

        An explicit wrapper_decrypt_host setting still wins for non-standard setups.
        """
        explicit = (self.settings.get('wrapper_decrypt_host') or '').strip()
        if explicit:
            return explicit
        try:
            host = urllib.parse.urlparse(self._get_wrapper_url()).hostname
            if host:
                return host
        except Exception:
            pass
        return '127.0.0.1'

    def _get_wrapper_decrypt_port(self) -> int:
        """TCP port for WV2D decryption; wrapper-v2 uses the fixed protocol port 10020."""
        try:
            port = int(self.settings.get('wrapper_decrypt_port') or 10020)
        except (TypeError, ValueError):
            port = 10020
        return port if 1 <= port <= 65535 else 10020

    def _wrapper_create_kwargs(self) -> dict:
        """decrypt_host/decrypt_port kwargs for WrapperApi.create, only when the
        installed gamdl supports them (3.8.2+)."""
        try:
            params = inspect.signature(WrapperApi.create).parameters
        except Exception:
            return {}
        kwargs = {}
        if 'decrypt_host' in params:
            kwargs['decrypt_host'] = self._get_wrapper_decrypt_host()
        if 'decrypt_port' in params:
            kwargs['decrypt_port'] = self._get_wrapper_decrypt_port()
        return kwargs

    def _wrapper_display_url(self) -> str:
        return self._get_wrapper_url().replace("http://", "").replace("https://", "")

    def _wrapper_connection_error_message(self) -> str:
        return (
            f"Could not connect to the wrapper at {self._wrapper_display_url()}. "
            "Make sure wrapper-v2 is running (docker compose up) and Use Wrapper is enabled."
        )

    def _wrapper_not_authenticated_message(self) -> str:
        return (
            "Wrapper is running but not logged in. Sign in to wrapper-v2 first "
            f"(POST /login on {self._wrapper_display_url()}, or set WRAPPER_USERNAME/WRAPPER_PASSWORD in Docker)."
        )

    def _cookie_init_error_message(self, cookies_path, error: Exception) -> str:
        cause = str(error).strip()
        path = os.path.abspath(str(cookies_path))
        if "media-user-token" in cause.lower():
            return (
                f'Apple Music cookies at "{path}" are missing the required "media-user-token" cookie. '
                "Export fresh cookies from music.apple.com while logged in with an active Apple Music subscription."
            )
        return (
            f'Failed to authenticate Apple Music with cookies at "{path}": {cause} '
            "Ensure cookies.txt is in Netscape format and exported from music.apple.com."
        )

    def _wrapper_enabled(self, override: Optional[bool] = None) -> bool:
        """Wrapper usage for a download: an explicit override wins, else whether wrapper_api is live."""
        if override is not None:
            return bool(override)
        return bool(getattr(self, 'wrapper_api', None))

    def _is_wrapper_auth_error(self, err_str: str) -> bool:
        lowered = (err_str or "").lower()
        return (
            "not authenticated" in lowered
            or "log in via the wrapper" in lowered
            or "wrapper login requires 2fa" in lowered
        )

    @staticmethod
    def _is_atmos_stream_codec(codec_val) -> bool:
        if not codec_val:
            return False
        c = str(codec_val).lower()
        return any(marker in c for marker in ('atmos', 'ec-3', 'ec3', 'eac3', 'eac-3', 'audio-ec3', 'audio-atmos'))

    @staticmethod
    def _is_alac_stream_codec(codec_val) -> bool:
        if not codec_val:
            return False
        return 'alac' in str(codec_val).lower()

    def _alac_requires_wrapper_message(self) -> str:
        return "Make sure wrapper is running and enabled to download ALAC format."

    @staticmethod
    def _is_alac_license_restriction_error(err_str: str) -> bool:
        """Detect Apple Music -1002 license errors that require the wrapper for ALAC."""
        if not err_str:
            return False
        compact = err_str.replace(' ', '')
        if '-1002' in err_str or '"status":-1002' in compact:
            return True
        lowered = err_str.lower()
        if 'license exchange' in lowered and ('-1002' in err_str or '"status"' in err_str):
            return True
        if 'api error 200' in lowered and '-1002' in err_str:
            return True
        return False

    def _maybe_raise_alac_wrapper_error(self, exc: Exception, local_effective_codec) -> None:
        if local_effective_codec != GamdlSongCodec.ALAC:
            return
        if self._is_alac_license_restriction_error(str(exc)):
            raise DownloadError(self._alac_requires_wrapper_message()) from exc

    def _gamdl_init_failure_message(self, wrapper_requested: bool = False) -> str:
        needs_wrapper = wrapper_requested or self.use_wrapper
        err = getattr(self, '_last_gamdl_init_error', None)
        if err is not None:
            err_str = str(err)
            if self._is_wrapper_auth_error(err_str):
                return self._wrapper_not_authenticated_message()
            lowered = err_str.lower()
            if any(token in lowered for token in (
                "connect", "connection refused", "connection error",
                "failed to connect", "wrapper account info", "wrapper is not authenticated",
            )):
                if needs_wrapper:
                    if "not authenticated" in lowered:
                        return self._wrapper_not_authenticated_message()
                    return self._wrapper_connection_error_message()
            if wrapper_requested and err_str:
                return f"Apple Music: wrapper initialization failed — {err_str}"
        if needs_wrapper:
            return self._wrapper_connection_error_message()
        return "Apple Music: gamdl components could not be initialized."

    async def _initialize_gamdl_components(self, song_codec=None, use_wrapper=None, force=False):
        self._clear_gamdl_caches()
        self._last_gamdl_init_error = None

        requested_codec = song_codec if song_codec is not None else self.song_codec
        requested_wrapper = use_wrapper if use_wrapper is not None else self.use_wrapper

        # Re-initialize when forced, or when any request-relevant setting drifted
        lyrics_settings = self._get_global_lyrics_settings()
        needs_reinit = force or (self.gamdl_downloader and (
            self._wrapper_enabled() != bool(requested_wrapper)
            or (hasattr(self.gamdl_song_interface, 'codec_priority') and self.gamdl_song_interface.codec_priority != [requested_codec])
            or self.song_codec != requested_codec
            or getattr(self, '_gamdl_lyrics_settings', None) != lyrics_settings
        ))

        if not self.gamdl_downloader or needs_reinit:
            if self._debug: print(f"[Apple Music Debug] Initializing gamdl components (force={force})...")
            try:
                orpheus_temp_path = Path(self.settings.get("temp_path", tempfile.gettempdir()))
                wrapper_api = self.wrapper_api if requested_wrapper else None
                if requested_wrapper and wrapper_api is None:
                    wrapper_api = await WrapperApi.create(base_url=self._get_wrapper_url(), **self._wrapper_create_kwargs())
                    self.wrapper_api = wrapper_api

                self.gamdl_base_interface = await AppleMusicBaseInterface.create(
                    apple_music_api=self.apple_music_api,
                    itunes_api=self.itunes_api,
                    wrapper_api=wrapper_api,
                )

                self.gamdl_song_interface = OrpheusAppleMusicSongInterface(
                    base=self.gamdl_base_interface,
                    quality_tier=self.quality_tier,
                    debug=self._debug,
                    codec_priority=[requested_codec],
                    synced_lyrics_format=SyncedLyricsFormat.LRC,
                )

                self.gamdl_interface = AppleMusicInterface(
                    song=self.gamdl_song_interface,
                    music_video=AppleMusicMusicVideoInterface(base=self.gamdl_base_interface),
                    uploaded_video=AppleMusicUploadedVideoInterface(base=self.gamdl_base_interface),
                    disallowed_media_types=[
                        "music-videos", "library-music-videos", "uploaded-videos", "music-video", "post",
                    ],
                )

                gamdl_exclude_tags = [] if lyrics_settings.get('embed_lyrics', True) else ['lyrics']
                self._gamdl_lyrics_settings = lyrics_settings

                self.gamdl_base_downloader = AppleMusicBaseDownloader(
                    interface=self.gamdl_interface,
                    output_path=str(orpheus_temp_path / "gamdl_out"),
                    temp_path=str(orpheus_temp_path / "gamdl_temp"),
                    ffmpeg_path=self.binary_paths.get('ffmpeg', 'ffmpeg'),
                    nm3u8dlre_path=self.binary_paths.get('nm3u8dlre', 'N_m3u8DL-RE'),
                    download_mode=self.settings.get('download_mode', GamdlDownloadMode.YTDLP),
                    exclude_tags=gamdl_exclude_tags or None,
                    silent=not self._debug,
                )

                self.gamdl_song_downloader = AppleMusicSongDownloader(base=self.gamdl_base_downloader)
                self.gamdl_downloader = AppleMusicDownloader(
                    song=self.gamdl_song_downloader,
                    music_video=AppleMusicMusicVideoDownloader(base=self.gamdl_base_downloader),
                    uploaded_video=AppleMusicUploadedVideoDownloader(base=self.gamdl_base_downloader),
                    skip_cleanup=True,
                    no_synced_lyrics=not lyrics_settings.get('save_synced_lyrics', True),
                )
                self.gamdl_downloader_song = self.gamdl_song_downloader

                if self._debug: print("[Apple Music Debug] gamdl_downloader components initialized successfully.")
            except Exception as e:
                self._last_gamdl_init_error = e
                print(f"[Apple Music Error] Failed to initialize gamdl components: {e}")
                import traceback
                if self._debug: print(traceback.format_exc())
                self.gamdl_downloader = None
                self.gamdl_downloader_song = None

    def custom_url_parse(self, link):
        """Parse Apple Music URLs and determine media type and ID"""
        try:
            url_info = self._parse_apple_music_url(link)

            type_mapping = {
                'song': DownloadTypeEnum.track,
                'album': DownloadTypeEnum.album,
                'playlist': DownloadTypeEnum.playlist,
                'artist': DownloadTypeEnum.artist,
                'music-video': DownloadTypeEnum.track,
            }
            media_type = type_mapping.get(url_info['type'], DownloadTypeEnum.track)

            # If authenticated, use the account storefront; otherwise the URL's country.
            storefront = url_info['country']
            if self.is_authenticated and self.account_storefront:
                if self._debug: print(f"[Apple Music Debug] Authenticated: Overriding URL storefront '{storefront}' with account storefront '{self.account_storefront}'")
                storefront = self.account_storefront

            extra_kwargs = {'country': storefront, 'original_country': url_info['country']}
            if url_info.get('is_library'):
                extra_kwargs['is_library'] = True

            return MediaIdentification(
                media_type=media_type,
                media_id=url_info['id'],
                extra_kwargs=extra_kwargs
            )

        except Exception as e:
            raise self.exception(f"Failed to parse Apple Music URL: {e}")

    def _parse_apple_music_url(self, url):
        """Parse Apple Music URL to extract type and ID"""
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        if path.endswith('/'):
            path = path[:-1]
        path_parts = [part for part in path.split('/') if part]

        if len(path_parts) < 2:
            raise ValueError("Invalid Apple Music URL format")

        # Handle library URLs: /library/playlist/p.XXXXX
        if path_parts[0] == 'library':
            media_type = path_parts[1]
            media_id = path_parts[2] if len(path_parts) >= 3 else None
            # Library requests require user storefront, but 'library' is not a country code
            country = getattr(self, 'account_storefront', 'us')

            return {
                'type': media_type,
                'id': media_id,
                'country': country,
                'is_library': True
            }

        # Catalog URLs: /country/type/name/id or /country/type/name-id
        if len(path_parts) < 3:
             raise ValueError(f"Invalid standard Apple Music URL format: {url}")

        country = path_parts[0]  # e.g., 'us'
        media_type = path_parts[1]  # e.g., 'song', 'album', 'playlist'

        # Check for song ID in query parameter first
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'i' in query_params and query_params['i']:
            media_id = query_params['i'][0]
            media_type = 'song'  # If 'i' is present, it's a song
        else:
            # For URLs with format: /country/type/name/id or /country/type/name-id
            # Check if we have 4+ parts (separate name and ID)
            if len(path_parts) >= 4:
                # ID is the last part
                potential_id = path_parts[-1]

                # Check if it's a playlist/album ID (pl.xxxxx, p.xxxxx, or l.xxxxx format)
                if potential_id.startswith('pl.') or potential_id.startswith('p.') or potential_id.startswith('l.'):
                    media_id = potential_id
                # Check if it's a numeric ID
                elif potential_id.isdigit():
                    media_id = potential_id
                else:
                    raise ValueError(f"Could not parse ID from last path part: {potential_id}")
            else:
                # Older URL formats: /country/type/name-id
                name_and_id = path_parts[2]
                id_match = re.search(r'/(\d+)(?:\?|$)', url) or re.search(r'(\d+)(?:\?|$)', name_and_id)
                if id_match:
                    media_id = id_match.group(1)
                else:
                    # Match pl. (catalog), p. (library playlist), or l. (library album)
                    pl_match = (re.search(r'/((?:pl\.|p\.|l\.)[a-f0-9]+)(?:\?|$)', url)
                                or re.search(r'((?:pl\.|p\.)[a-f0-9]+)(?:\?|$)', name_and_id))
                    if pl_match:
                        media_id = pl_match.group(1)
                    else:
                        raise ValueError("Could not extract ID from Apple Music URL")

        return {
            'type': media_type,
            'id': media_id,
            'country': country
        }

    def search(self, query_type: DownloadTypeEnum, query, tags: Tags = None, limit=10):
        """Search Apple Music catalog"""
        try:
            # Map OrpheusDL query types to Apple Music search types
            type_mapping = {
                DownloadTypeEnum.track: 'songs',
                DownloadTypeEnum.album: 'albums',
                DownloadTypeEnum.artist: 'artists',
                DownloadTypeEnum.playlist: 'playlists'
            }

            search_type = type_mapping.get(query_type, 'songs')

            # Apple Music Search API has a hard limit of 50 results per request
            search_limit = min(int(limit), 50) if limit else 50

            results = self._run_async(lambda s: s.apple_music_api.get_search_results(term=query, types=search_type, limit=search_limit))

            # Map 'results' structure to what the rest of the method expects
            if 'results' in results:
                results = results['results']

            search_results = []
            if search_type in results:
                for item in results[search_type]['data']:
                    attrs = item.get('attributes', {})

                    # Only hide playlists that explicitly have 0 tracks; keep them when
                    # trackCount is missing (the API may omit it)
                    if query_type == DownloadTypeEnum.playlist and attrs.get('trackCount') == 0:
                        continue

                    artists = []
                    if query_type == DownloadTypeEnum.artist:
                        artists = [attrs.get('name', '')]
                    elif 'artistName' in attrs:
                        artists = artists_from_apple_attrs(attrs)
                    elif 'curatorName' in attrs:  # playlists
                        artists = [attrs['curatorName']]

                    additional = []
                    if 'trackCount' in attrs:
                        tc = attrs['trackCount']; additional.append(f"1 track" if tc == 1 else f"{tc} tracks")
                    formatted_traits = self._format_audio_traits(attrs, item_type=item.get('type'))
                    if formatted_traits:
                        additional.append(formatted_traits)

                    artwork = attrs.get('artwork', {})
                    previews = attrs.get('previews') or []
                    # Playlists use lastModifiedDate for the year when releaseDate is absent
                    year_val = self._extract_year(attrs.get('releaseDate'))
                    if year_val is None and query_type == DownloadTypeEnum.playlist:
                        year_val = self._extract_year(attrs.get('lastModifiedDate'))
                    if 'url' in attrs:
                        attrs['url'] = self._localize_url(attrs['url'])

                    search_results.append(SearchResult(
                        result_id=item['id'],
                        name=attrs.get('name', ''),
                        artists=artists,
                        duration=attrs['durationInMillis'] // 1000 if 'durationInMillis' in attrs else None,
                        year=year_val,
                        explicit=attrs.get('contentRating') == 'explicit',
                        additional=additional,
                        # 56x56 thumbnails for search result covers
                        image_url=artwork['url'].replace('{w}', '56').replace('{h}', '56') if artwork.get('url') else None,
                        preview_url=previews[0].get('url') if previews else None,
                        extra_kwargs={'raw_result': item}
                    ))

            # Backfill missing playlist metadata (track counts / durations) and album durations
            if query_type == DownloadTypeEnum.playlist:
                missing = [t for t in search_results if not t.additional or not t.duration]
                if missing:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                        pcounts = {pid: {'tc': tc, 'dur': dur} for pid, tc, dur
                                   in executor.map(self._fetch_am_playlist_meta, [t.result_id for t in missing])}
                    for t in missing:
                        meta = pcounts.get(t.result_id, {})
                        if not t.additional and meta.get('tc'):
                            t.additional = [f"1 track" if meta['tc'] == 1 else f"{meta['tc']} tracks"]
                        if not t.duration and meta.get('dur'):
                            t.duration = meta['dur']
            elif query_type == DownloadTypeEnum.album:
                missing = [t for t in search_results if not t.duration]
                if missing:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                        acounts = {aid: dur for aid, dur
                                   in executor.map(self._fetch_am_album_duration, [t.result_id for t in missing]) if dur}
                    for t in missing:
                        if t.result_id in acounts:
                            t.duration = acounts[t.result_id]

            return search_results

        except Exception as e:
            raise self.exception(f"Search failed: {e}")

    def _extract_year(self, release_date):
        """Extract year from release date string"""
        if not release_date:
            return None
        try:
            return int(release_date.split('-')[0])
        except (ValueError, IndexError):
            return None

    def _ensure_credentials(self):
        """
        Require a valid authenticated session before download/metadata operations.
        Also reloads cookies.txt if it changed, so users can drop the file in
        while the app is running. Matches Spotify/Qobuz/Deezer modules: show
        what's missing and where to fill it in.
        """
        cookies_path = self._cookies_path()
        if cookies_path and self.apple_music_api:
            try:
                if self._debug: print(f"[Apple Music Debug] Reloading cookies from {cookies_path}...")
                with self._lock:
                    # Re-initialize the API with new cookies via the background loop
                    self.apple_music_api = self._run_async(lambda s: AppleMusicApi.create_from_netscape_cookies(
                        cookies_path=str(cookies_path),
                        language=self.settings.get('language', 'en-US')
                    ))
                self.is_authenticated = self.apple_music_api.active_subscription
                if self._debug: print(f"[Apple Music Debug] Cookie reload authenticated={self.is_authenticated}")
            except Exception as e:
                if self._debug: print(f"[Apple Music Error] Failed to reload cookies: {e}")
                raise self.exception(self._cookie_init_error_message(cookies_path, e))

        if self.is_authenticated and self.apple_music_api:
            return
        raise self.exception(
            'Apple Music authentication is required for downloading. Please provide '
            'cookies.txt in the /config folder or fill in the Media User Token in Settings → Apple Music.'
        )

    async def _setup_api_clients(self):
        """
        Initialize or re-initialize AppleMusicApi and ItunesApi based on current settings.
        Extracted so both __init__ and _run_async's self-healing can call it.
        This MUST be called within the background loop thread.
        """
        cookies_path = self._cookies_path()

        with self._gamdl_quiet():
            self.use_wrapper = self.settings.get('use_wrapper', False)
            language = self.settings.get('language', 'en-US')
            self.wrapper_api = None

            try:
                if self.use_wrapper:
                    wrapper_url = self._get_wrapper_url()
                    try:
                        if not getattr(self, '_wrapper_offline', False):
                            self.wrapper_api = await WrapperApi.create(base_url=wrapper_url, **self._wrapper_create_kwargs())
                            self.apple_music_api = await AppleMusicApi.create_from_wrapper(
                                wrapper_api=self.wrapper_api,
                                language=language,
                            )
                    except Exception as wrapper_err:
                        if self._is_wrapper_auth_error(str(wrapper_err)):
                            if self._debug: print(f"[Apple Music Debug] Wrapper auth required: {wrapper_err}")
                        self._wrapper_offline = True
                        self.wrapper_api = None

                if not getattr(self, 'apple_music_api', None):
                    if cookies_path and cookies_path.exists():
                        # Prefer cookies.txt when present.
                        try:
                            self.apple_music_api = await AppleMusicApi.create_from_netscape_cookies(
                                cookies_path=str(cookies_path),
                                language=language,
                            )
                        except Exception as ce:
                            if self._debug: print(f"[Apple Music Debug] Cookie initialization failed: {ce}")
                            raise self.exception(self._cookie_init_error_message(cookies_path, ce))
                    else:
                        media_token = str(self.settings.get('media_user_token') or '').strip()
                        if media_token:
                            # Fall back to direct media-user-token auth (the same token
                            # gamdl extracts from cookies.txt) — enables AAC/Lyrics/Videos
                            # without a cookies file.
                            try:
                                self.apple_music_api = await AppleMusicApi.create(
                                    media_user_token=media_token,
                                    language=language,
                                )
                            except Exception as me:
                                if self._debug: print(f"[Apple Music Debug] Media token initialization failed: {me}")
                                raise self.exception(f"Apple Music media token authentication failed: {me}")
                        else:
                            self.apple_music_api = await AppleMusicApi.create(language=language)

                if self.apple_music_api:
                    self.itunes_api = await ItunesApi.create(
                        storefront=self.apple_music_api.storefront,
                        language=language,
                    )
                    self.account_storefront = self.apple_music_api.storefront
                    self.is_authenticated = self.apple_music_api.active_subscription

                self._resolve_all_binary_paths()
                self.song_codec = self._get_gamdl_codec(self.settings.get('codec', 'aac'))

            except Exception as e:
                if self._debug: print(f"[Apple Music Error] API Setup failed: {e}")
                import traceback
                traceback.print_exc()
                raise

    def _get_equivalent_track_id(self, isrc: str, target_storefront: str, title: str = None, artist: str = None) -> Optional[str]:
        """
        Search for a track by ISRC (or Title/Artist) in the target storefront and return its ID.
        Useful when the original track ID is from a different region and 404s in the user's region.
        """
        if not target_storefront:
            return None

        if self._debug: print(f"[Apple Music Debug] Searching for equivalent track in storefront '{target_storefront}'...")

        current_storefront = self.apple_music_api.storefront
        # Set storefront BEFORE _run_async so it's captured by the preservation logic
        self.apple_music_api.storefront = target_storefront

        try:
            # 1. Search by ISRC if available
            if isrc:
                if self._debug: print(f"[Apple Music Debug] Trying ISRC search: {isrc}")
                results = self._run_async(lambda s: s.apple_music_api.get_search_results(term=isrc, types="songs", limit=5), storefront=target_storefront)

                if results and 'results' in results and 'songs' in results['results']:
                    songs = results['results']['songs'].get('data', [])
                    for song in songs:
                        song_isrc = song.get('attributes', {}).get('isrc')
                        if song_isrc and song_isrc.lower() == isrc.lower():
                            new_id = song.get('id')
                            if self._debug: print(f"[Apple Music Debug] Found equivalent track ID {new_id} via ISRC {isrc}")
                            return new_id

            # 2. Fallback to Search by Title and Artist if ISRC failed or wasn't provided
            if title and artist:
                if self._debug: print(f"[Apple Music Debug] Trying semantic search: {title} {artist}")
                results = self._run_async(lambda s: s.apple_music_api.get_search_results(term=f"{title} {artist}", types="songs", limit=10), storefront=target_storefront)

                if results and 'results' in results and 'songs' in results['results']:
                    songs = results['results']['songs'].get('data', [])

                    def artist_matches(attrs):
                        return (artist.lower() in attrs.get('artistName', '').lower()
                                or any(artist.lower() in a.lower() for a in attrs.get('artistNames', [])))

                    # Pass 1: exact title match
                    for song in songs:
                        attrs = song.get('attributes', {})
                        if title.lower() == attrs.get('name', '').lower() and artist_matches(attrs):
                            new_id = song.get('id')
                            if self._debug: print(f"[Apple Music Debug] Found equivalent track ID {new_id} via exact semantic search")
                            return new_id

                    # Pass 2: fuzzy title match — skip remixes/versions of a clean original title
                    original_clean = "remix" not in title.lower() and "version" not in title.lower()
                    for song in songs:
                        attrs = song.get('attributes', {})
                        result_name = attrs.get('name', '').lower()
                        if original_clean and ("remix" in result_name or "version" in result_name):
                            continue
                        if title.lower() in result_name and artist_matches(attrs):
                            new_id = song.get('id')
                            if self._debug: print(f"[Apple Music Debug] Found equivalent track ID {new_id} via fuzzy semantic search")
                            return new_id

            if self._debug: print(f"[Apple Music Debug] No equivalent track found in {target_storefront}")
            return None

        except Exception as e:
            if self._debug: print(f"[Apple Music Debug] Error searching for equivalent track: {e}")
            return None
        finally:
            self.apple_music_api.storefront = current_storefront

    def get_track_info(self, track_id: str, quality_tier: QualityEnum, codec_options: CodecOptions, data: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[TrackInfo]:
        if self._debug:
            print(f"[{module_information.service_name} DEBUG] get_track_info called for track_id: {track_id}, kwargs: {list(kwargs.keys())}")

        # Re-evaluate settings from config to ensure we catch changes from the GUI
        self.song_codec = self._get_gamdl_codec(self.settings.get('codec', 'aac'))
        self.use_wrapper = self.settings.get('use_wrapper', False)

        # allow_refetch=True will trigger a full API fetch if essential IDs are missing.
        # We default to True now to ensure complete metadata during downloads,
        # but Orpheus search/listing usually passes 'data' which we use first.
        allow_refetch = kwargs.get('allow_refetch', True)

        # track_id can arrive as a dict (album/playlist track lists) or even as a
        # stringified/URL-encoded dict from elsewhere in the pipeline — recover the ID.
        if isinstance(track_id, dict):
            if data is None:
                data = track_id
            track_id = str(data.get('id'))
        elif isinstance(track_id, str) and (track_id.strip().startswith('{') or "%7B" in track_id):
            try:
                clean_id = urllib.parse.unquote(track_id) if "%7B" in track_id else track_id
                if clean_id.strip().startswith('{'):
                    try:
                        potential_data = ast.literal_eval(clean_id)
                        if isinstance(potential_data, dict) and potential_data.get('id'):
                            track_id = str(potential_data['id'])
                            # Also use the data if we don't have any
                            if data is None:
                                data = potential_data
                            if self._debug: print(f"[Apple Music Debug] Successfully parsed stringified dict ID: {track_id}")
                    except (ValueError, SyntaxError):
                        # Fallback: naive string extraction if literal_eval fails
                        if "'id': '" in clean_id:
                            start = clean_id.find("'id': '") + 7
                            end = clean_id.find("'", start)
                            if start > 6 and end > start:
                                track_id = clean_id[start:end]
                                if self._debug: print(f"[Apple Music Debug] Extracted ID via string manipulation: {track_id}")
            except Exception as e:
                if self._debug: print(f"[Apple Music Debug] Failed to parse stringified track_id: {e}")
                # Continue with original track_id and let the API handle the error

        self._ensure_credentials()

        try:
            # Detection for library IDs (e.g., 65WJUJIOK3UJT5J5H4DXEXBFUY)
            def is_library_id(tid):
                tid_str = str(tid)
                return len(tid_str) > 15 and not tid_str.isdigit()

            # Initialize country from kwargs
            country = kwargs.get('country')

            # Try to extract country if URL is provided (overrides kwargs if present)
            if 'url' in kwargs and kwargs['url']:
                url_country = self._parse_apple_music_url(kwargs['url']).get('country')
                if url_country:
                    country = url_country
                    if self._debug: print(f"[Apple Music Debug] Parsed country '{country}' from URL: {kwargs['url']}")

            self._set_storefront(country)

            # Library-aware song fetch: maps library items to their catalog
            # counterpart (richer metadata) when the relationship is available.
            async def _fetch_song(s, sid, log=False):
                library_data = None
                if is_library_id(sid):
                    if log and s._debug:
                        print(f"[Apple Music Debug] Library ID detected: {sid}. Fetching via library API...")
                    library_data = await s.apple_music_api.get_library_song(sid)
                    track = _first(library_data)
                    catalog_rels = track.get('relationships', {}).get('catalog', {}).get('data', []) if isinstance(track, dict) else []
                    if catalog_rels:
                        cat_id = catalog_rels[0].get('id')
                        if cat_id:
                            if s._debug: print(f"[Apple Music Debug] Library ID {sid} mapped to catalog ID {cat_id}. Fetching catalog metadata...")
                            return await s.apple_music_api.get_song(cat_id)
                    return library_data
                return await s.apple_music_api.get_song(sid)

            async def _fetch_with_logging(s, sid):
                try:
                    return await _fetch_song(s, sid, log=True)
                except Exception as fe:
                    if getattr(s, '_debug', False): print(f"[Apple Music Debug] API fetch failed for {sid}: {fe}")
                    return None

            # Check if we have raw_result from search
            if 'raw_result' in kwargs and kwargs['raw_result']:
                track_api_data = kwargs['raw_result']
                if self._debug: print(f"[Apple Music Debug] Using raw_result from search for track {track_id}")
            else:
                # Use data if provided (e.g., from album track list), otherwise fetch
                track_api_data = data if data and isinstance(data, dict) and data.get('id') == track_id and 'attributes' in data else self._run_async(lambda s: _fetch_with_logging(s, track_id), storefront=country)

                # Early ID Reconciliation: if the data already has a catalogId in
                # playParams, switch track_id to it before unwrapping
                if track_api_data:
                    temp_item = _first(track_api_data)
                    if isinstance(temp_item, dict):
                        catalog_id = temp_item.get('attributes', {}).get('playParams', {}).get('catalogId')
                        if catalog_id and str(catalog_id) != str(track_id):
                            if self._debug: print(f"[Apple Music Debug] Map library ID {track_id} to catalog ID {catalog_id} before unwrap. Updating track_id.")
                            track_id = str(catalog_id)

                track_api_data = _first(track_api_data)

                # Fallback to account storefront if url-based storefront fails
                if (not track_api_data or 'attributes' not in track_api_data) and country and self.account_storefront.lower() != country.lower():
                    if self._debug: print(f"[Apple Music Debug] Fetch failed for storefront '{country}'. Retrying with account storefront '{self.account_storefront}'...")
                    track_api_data = _first(self._run_async(lambda s: _fetch_with_logging(s, track_id), storefront=self.account_storefront))

                # If still failed, try a "guest" fetch (without user token) for metadata
                if not track_api_data or 'attributes' not in track_api_data:
                    if self._debug: print(f"[Apple Music Debug] Initial fetch failed. Attempting guest fetch for metadata...")

                    async def _fetch_guest(s, sid, st):
                        # Temporarily remove tokens to avoid storefront restrictions on metadata
                        original_headers = dict(s.apple_music_api.client.headers)
                        original_cookies = dict(s.apple_music_api.client.cookies)
                        if "media-user-token" in s.apple_music_api.client.cookies:
                            del s.apple_music_api.client.cookies["media-user-token"]
                        orig_st = s.apple_music_api.storefront
                        s.apple_music_api.storefront = st
                        try:
                            return await _fetch_song(s, sid)
                        finally:
                            s.apple_music_api.storefront = orig_st
                            s.apple_music_api.client.headers.update(original_headers)
                            s.apple_music_api.client.cookies.update(original_cookies)

                    track_api_data = _first(self._run_async(lambda s: _fetch_guest(s, track_id, country or self.account_storefront), storefront=country or self.account_storefront))

                # If everything else failed, try iTunes Search API (lookup)
                if not track_api_data or 'attributes' not in track_api_data:
                    if self._debug: print(f"[Apple Music Debug] Apple Music API failed. Trying iTunes Search API fallback...")

                    async def _fetch_itunes(s, sid):
                        try:
                            res = await s.itunes_api.get_lookup_result(sid, entity='song')
                            if res and res.get('resultCount', 0) > 0:
                                itunes_track = res['results'][0]
                                # Map iTunes format to something resembling Apple Music API attributes
                                return {
                                    'id': str(itunes_track.get('trackId')),
                                    'type': 'songs',
                                    'attributes': {
                                        'name': itunes_track.get('trackName'),
                                        'albumName': itunes_track.get('collectionName'),
                                        'artistName': itunes_track.get('artistName'),
                                        'artwork': {'url': itunes_track.get('artworkUrl100', '').replace('100x100bb.jpg', '{w}x{h}bb.jpg')},
                                        'durationInMillis': itunes_track.get('trackTimeMillis'),
                                        'releaseDate': itunes_track.get('releaseDate'),
                                        'genreNames': [g for g in [itunes_track.get('primaryGenreName')] if g and g.lower() != 'music'],
                                        'trackNumber': itunes_track.get('trackNumber'),
                                        'discNumber': itunes_track.get('discNumber'),
                                        'contentRating': itunes_track.get('contentAdvisoryRating', '').lower()
                                    }
                                }
                        except Exception as ie:
                            if getattr(s, '_debug', False): print(f"[Apple Music Debug] iTunes lookup failed: {ie}")
                        return None

                    track_api_data = self._run_async(lambda s: _fetch_itunes(s, track_id), storefront=country)

            if not track_api_data or 'attributes' not in track_api_data:
                if self._debug: print(f"[Apple Music Error] Could not fetch track data for {track_id} from AppleMusicApi.")
                return TrackInfo(name=f"Error: Fetch failed for {track_id}", error="API Fetch Failed", artists=["Unknown Artist"], album="", album_id=None, artist_id=None, duration=0, codec=CodecEnum.AAC, bitrate=0, sample_rate=0, release_year=None, cover_url=None, explicit=False, tags=Tags())

            attrs = track_api_data['attributes']

            def get_ids(d):
                """(album_id, artist_id) from a track's relationships."""
                aid = arid = None
                if d.get('relationships'):
                    rel_data = d['relationships'].get('albums', {}).get('data')
                    if rel_data: aid = rel_data[0].get('id')
                    rel_data = d['relationships'].get('artists', {}).get('data')
                    if rel_data: arid = rel_data[0].get('id')
                return aid, arid

            # Note: albumName/artistName are used as ID proxies here (they're always
            # present on catalog tracks) — get_ids() supplies the real IDs after the
            # supplemental refetch below.
            album_id_from_rels = attrs.get('albumName') or (track_api_data.get('relationships', {}).get('albums', {}).get('data', [{}])[0].get('id'))
            artist_id_from_rels = attrs.get('artistName') or (track_api_data.get('relationships', {}).get('artists', {}).get('data', [{}])[0].get('id'))
            if allow_refetch and (not album_id_from_rels or not artist_id_from_rels or 'hasLyrics' not in attrs or 'audioTraits' not in attrs or 'recordLabel' not in attrs or 'copyright' not in attrs or 'upc' not in attrs):
                if self._debug: print(f"[Apple Music Debug] Incomplete metadata (Album={album_id_from_rels}, Artist={artist_id_from_rels}, hasLyrics={'hasLyrics' in attrs}, audioTraits={'audioTraits' in attrs}) for track {track_id}. Fetching full song data.")
                full_track_data = _first(self._run_async(lambda s: _fetch_with_logging(s, track_id), storefront=country))
                if isinstance(full_track_data, dict) and 'attributes' in full_track_data:
                    track_api_data = full_track_data
                    attrs = track_api_data['attributes']
                    album_id_from_rels, artist_id_from_rels = get_ids(track_api_data)
                    if self._debug: print(f"[Apple Music Debug] Metadata updated after full fetch.")

            # --- Storefront Mismatch / Equivalent Check ---
            actual_download_id = track_id
            user_storefront = getattr(self, 'account_storefront', None)
            api_storefront = country.lower() if country else (self.apple_music_api.storefront if self.apple_music_api else 'us')

            if self.is_authenticated and user_storefront and api_storefront and user_storefront.lower() != api_storefront.lower():
                track_isrc = attrs.get('isrc')
                name_for_search = attrs.get('name')
                artist_name_for_search = attrs.get('artistName')
                if track_isrc or (name_for_search and artist_name_for_search):
                    equivalent_id = self._get_equivalent_track_id(track_isrc, user_storefront, name_for_search, artist_name_for_search)

                    if self._debug: print(f"[Apple Music Debug] ID {track_id} -> Storefronts: User={user_storefront}, API={api_storefront}. Result equivalent_id={equivalent_id}")

                    if equivalent_id:
                        actual_download_id = equivalent_id
                        if self._debug: print(f"[Apple Music Debug] Using equivalent track {actual_download_id} in {user_storefront}. Fetching its metadata...")

                        # Re-fetch metadata for the equivalent ID in the user's storefront so the downloader has working info
                        equiv_metadata = _first(self._run_async(lambda s: s.apple_music_api.get_song(actual_download_id), storefront=user_storefront))
                        if isinstance(equiv_metadata, dict) and 'attributes' in equiv_metadata:
                            track_api_data = equiv_metadata
                            # Update local attrs for any later logic in this method
                            attrs = track_api_data['attributes']
                            album_id_from_rels, artist_id_from_rels = get_ids(track_api_data)
                            if self._debug: print(f"[Apple Music Debug] Successfully fetched metadata for equivalent track {actual_download_id}")

            # --- Final Consolidated Metadata Extraction ---
            name = attrs.get('name', 'Unknown Track')
            album_name = attrs.get('albumName', 'Unknown Album')
            artists_list = artists_from_apple_attrs(attrs)

            duration_ms = attrs.get('durationInMillis')
            duration_sec = duration_ms // 1000 if duration_ms is not None else 0
            release_date_str = attrs.get('releaseDate')
            # Album downloads should use the album release date for all tracks.
            album_release_date = kwargs.get('album_release_date')
            if album_release_date:
                release_date_str = album_release_date
            year = self._extract_year(release_date_str)
            explicit = attrs.get('contentRating') == 'explicit'

            # Codec selection (indicative)
            override_song_codec = kwargs.get('song_codec')
            effective_codec = self._quality_to_codec(quality_tier) if quality_tier else (self._get_gamdl_codec(override_song_codec) if override_song_codec else self.song_codec)

            if self._debug: print(f"[{module_information.service_name} DEBUG] info effective_codec: {effective_codec.name if hasattr(effective_codec, 'name') else str(effective_codec)}")

            display_codec = CodecEnum.AAC
            display_bitrate = 256
            display_bit_depth = 16
            display_sample_rate = 44100

            if effective_codec in (GamdlSongCodec.ALAC, GamdlSongCodec.ATMOS):
                traits = attrs.get('audioTraits', [])

                # Verify if requested quality is actually available
                supports_alac = 'lossless' in traits or 'hi-res-lossless' in traits
                supports_atmos = 'atmos' in traits or 'spatial' in traits

                if effective_codec == GamdlSongCodec.ATMOS and supports_atmos:
                    display_codec = CodecEnum.EAC3
                    display_bitrate = 768
                    display_bit_depth = 16
                    display_sample_rate = 48000
                elif (effective_codec == GamdlSongCodec.ALAC or (effective_codec == GamdlSongCodec.ATMOS and not supports_atmos)) and supports_alac:
                    display_codec = CodecEnum.ALAC
                    display_bitrate = 0

                    if effective_codec == GamdlSongCodec.ATMOS and self._debug:
                        print(f"[Apple Music Debug] Display fallback: Downgrading codec to ALAC as ATMOS is unavailable (Traits: {traits})")

                    # Try to get precise info from manifest
                    precise_info = self._get_precise_alac_info(attrs, GamdlSongCodec.ALAC, quality_tier=quality_tier)
                    if precise_info:
                        display_bit_depth = precise_info.get('bit_depth', 24)
                        display_sample_rate = precise_info.get('sample_rate', 48000)
                    else:
                        # Fallback to trait-based inference if manifest fails
                        if 'hi-res-lossless' in traits and quality_tier != QualityEnum.LOSSLESS:
                            display_bit_depth, display_sample_rate = 24, 96000
                        else:
                            display_bit_depth, display_sample_rate = 24, 48000
                else:
                    # Requested quality not available per track traits — keep AAC defaults
                    if self._debug: print(f"[Apple Music Debug] Requested {effective_codec.name} but track traits {traits} do not support it. Falling back to AAC.")

            # Extract record label and copyright from song attributes
            record_label = attrs.get('recordLabel')
            copyright_info = attrs.get('copyright')

            # --- Relationship-based Metadata Inheritance ---
            # If missing from song attributes, try to inherit from album relationship (crucial for Apple Music)
            rels = track_api_data.get('relationships', {})
            albums_rel = rels.get('albums', {}).get('data', [])
            album_attrs = albums_rel[0].get('attributes', {}) if albums_rel else {}

            # Album artist: prefer album-level context so every track shares one value.
            # Use the primary artist only so collaboration albums file under one artist
            # instead of every credited artist.
            album_artist = resolve_album_artist_tag(
                kwargs.get('album_artist'),
                attrs.get('albumArtistName'),
                album_attrs.get('artistName') if album_attrs else None,
                attrs.get('artistName'),
                primary_only=True,
            ) or (artists_list[0] if artists_list else 'Unknown Artist')

            if not record_label or not copyright_info:
                if not record_label: record_label = album_attrs.get('recordLabel')
                if not copyright_info: copyright_info = album_attrs.get('copyright')

            # Extract UPC (Universal Product Code) from track or album attributes
            upc = attrs.get('upc') or album_attrs.get('upc')

            if self._debug: print(f"[Apple Music Metadata Debug] Available keys for track {track_id}: {list(attrs.keys())}")
            if not record_label and copyright_info:
                record_label = _label_from_copyright(copyright_info)

            # Extract track and disc counts
            total_tracks_val = attrs.get('trackCount') or album_attrs.get('trackCount')
            total_discs_val = attrs.get('discCount') or album_attrs.get('discCount')

            # Determine the storefront that will be used for the URL (the one that actually yielded a valid metadata/download)
            effective_storefront = user_storefront if actual_download_id != track_id else api_storefront

            tags_obj = Tags(
                album_artist=album_artist,
                track_number=attrs.get('trackNumber'),
                total_tracks=total_tracks_val,
                disc_number=attrs.get('discNumber'),
                total_discs=total_discs_val,
                release_date=release_date_str,
                genres=[g for g in attrs.get('genreNames', []) if g.lower() != 'music'],
                isrc=attrs.get('isrc'),
                composer=attrs.get('composerName'),
                label=record_label,
                copyright=copyright_info,
                upc=upc,
                track_url=f"https://music.apple.com/{effective_storefront}/song/unknown/{actual_download_id}"
            )

            cover_url = self._get_cover_url(attrs.get('artwork', {}).get('url'))

            download_extra_kwargs = {
                'track_id': actual_download_id,
                'api_response': track_api_data,
                'quality_tier': quality_tier,
                'source_quality_tier': quality_tier.name if hasattr(quality_tier, 'name') else str(quality_tier),
                'original_id': track_id,
                'effective_storefront': effective_storefront
            }
            if override_song_codec: download_extra_kwargs['song_codec'] = override_song_codec
            if kwargs.get('use_wrapper') is not None: download_extra_kwargs['use_wrapper'] = kwargs.get('use_wrapper')

            return TrackInfo(
                name=name, album=album_name, album_id=str(album_id_from_rels) if album_id_from_rels else None,
                artists=artists_list, artist_id=str(artist_id_from_rels) if artist_id_from_rels else None,
                duration=duration_sec, codec=display_codec, bitrate=display_bitrate, bit_depth=display_bit_depth,
                sample_rate=display_sample_rate // 1000 if display_sample_rate else None, release_year=year,
                cover_url=cover_url, explicit=explicit, tags=tags_obj, id=actual_download_id,
                download_extra_kwargs=download_extra_kwargs,
                lyrics_extra_kwargs={'data': track_api_data}
            )

        except Exception as e:
            # Create a clean, concise error message
            error_msg = str(e)
            if "ConnectionError" in str(type(e)) or "NameResolutionError" in error_msg:
                error_msg = "Network connection failed"
            elif "HTTPSConnectionPool" in error_msg:
                error_msg = "Unable to connect to Apple Music servers"
            elif "Max retries exceeded" in error_msg:
                error_msg = "Connection timeout"
            elif "getaddrinfo failed" in error_msg:
                error_msg = "DNS resolution failed"

            if self._debug:
                import traceback
                print(f"[Apple Music Error] An unexpected error occurred in get_track_info for track {track_id}: {e}")
                print(traceback.format_exc())

            # Return an error-state TrackInfo object
            return TrackInfo(name=f"Error for {track_id}", error=error_msg, artists=["Unknown Artist"], album="", album_id=None, artist_id=None, duration=0, codec=CodecEnum.AAC, bitrate=0, sample_rate=0, release_year=None, cover_url=None, explicit=False, tags=Tags())

    def get_track_download(self, track_id: str = None, quality_tier: QualityEnum = None, codec_options: CodecOptions = None, **kwargs) -> Optional[TrackDownloadInfo]:
        self._refresh_debug_mode()
        if self._debug:
            print(f"[Apple Music Debug] get_track_download called for track_id: {track_id}")
            print(f"[Apple Music Debug] quality_tier: {quality_tier} (Type: {type(quality_tier)})")

        # Re-evaluate settings from config to ensure we catch changes from the GUI
        self.song_codec = self._get_gamdl_codec(self.settings.get('codec', 'aac'))
        self.use_wrapper = self.settings.get('use_wrapper', False)

        self._ensure_credentials()

        # Check for overrides from kwargs (passed from orpheus.py via extra_kwargs)
        override_song_codec = kwargs.get('song_codec')
        override_use_wrapper = kwargs.get('use_wrapper')

        # Try to recover quality_tier from kwargs if not passed directly
        if quality_tier is None:
            if 'source_quality_tier' in kwargs:
                tier_btn_name = kwargs.get('source_quality_tier')
                try:
                    quality_tier = QualityEnum[tier_btn_name]
                except:
                    pass
            elif 'quality_tier' in kwargs:
                quality_tier = kwargs.get('quality_tier')

        # Infer quality_tier from codec override if still None
        if quality_tier is None and override_song_codec:
            if 'alac-lossless' in override_song_codec.lower():
                quality_tier = QualityEnum.LOSSLESS
            elif 'alac-hi-res' in override_song_codec.lower():
                quality_tier = QualityEnum.HIFI

        # Map quality_tier or string override to enum
        if quality_tier:
            effective_codec = self._quality_to_codec(quality_tier)
        elif override_song_codec:
            effective_codec = self._get_gamdl_codec(override_song_codec)
        else:
            effective_codec = self.song_codec

        # Whether this download asked for the wrapper (settings or per-track override)
        wrapper_requested = bool(override_use_wrapper if override_use_wrapper is not None else self.use_wrapper)

        # Always log important selection info to GUI output when debug is on
        if self._debug:
            codec_name = effective_codec.name if hasattr(effective_codec, 'name') else str(effective_codec)
            msg = f"[Apple Music Debug] Download: id={track_id}, tier={quality_tier}, codec={codec_name}"
            print(msg)
            if self.printer:
                self.printer.oprint(f"       Debug: {msg}", 0)

        indent_spaces = "        "

        async def _download_async():
            # Stabilize storefront based on extra_kwargs to avoid region mismatches
            local_storefront = kwargs.get('effective_storefront')
            if local_storefront:
                self._set_storefront(local_storefront)

            # 1. Get metadata (use provided api_response if available to save a request)
            # We fetch this BEFORE initializing components so we can adjust the codec if needed
            song_api_data = kwargs.get('api_response')
            if song_api_data:
                # Handle both full 'data' wrapper and direct item dict
                if 'data' in song_api_data and isinstance(song_api_data['data'], list) and len(song_api_data['data']) > 0:
                    song_data = song_api_data['data'][0]
                elif 'attributes' in song_api_data:
                    song_data = song_api_data
                else:
                    song_data = None
            else:
                song_data = None

            if not song_data:
                with self._gamdl_quiet():
                    # track_id might be None if passed via kwargs
                    target_id = track_id or kwargs.get('track_id')
                    if not target_id:
                        raise DownloadError("Apple Music: No track ID provided for download.")
                    song_metadata = await self.apple_music_api.get_song(target_id)

                if not song_metadata or not song_metadata.get('data'):
                    raise DownloadError(f"Apple Music: Failed to get metadata for track {target_id}")
                song_data = song_metadata['data'][0]

            # 2. Check for quality availability and adjust effective_codec if needed
            # Use local copy of effective_codec to avoid modifying the outer variable
            local_effective_codec = effective_codec
            traits = song_data.get('attributes', {}).get('audioTraits', [])

            if local_effective_codec == GamdlSongCodec.ALAC and not ('lossless' in traits or 'hi-res-lossless' in traits):
                if self._debug: print(f"[Apple Music Debug] Downgrading codec to AAC as ALAC is unavailable for this track (Traits: {traits})")
                local_effective_codec = GamdlSongCodec.AAC_WEB
            elif local_effective_codec == GamdlSongCodec.ATMOS and not ('atmos' in traits or 'spatial' in traits):
                if 'lossless' in traits or 'hi-res-lossless' in traits:
                    if self._debug: print(f"[Apple Music Debug] Downgrading codec to ALAC as ATMOS is unavailable for this track (Traits: {traits})")
                    local_effective_codec = GamdlSongCodec.ALAC
                else:
                    if self._debug: print(f"[Apple Music Debug] Downgrading codec to AAC as ATMOS and ALAC are unavailable for this track (Traits: {traits})")
                    local_effective_codec = GamdlSongCodec.AAC_WEB

            # 3. Ensure gamdl components are initialized, passing overrides if present
            with self._gamdl_quiet():
                await self._initialize_gamdl_components(song_codec=local_effective_codec, use_wrapper=override_use_wrapper)

            # Update quality_tier on our custom interface before each download
            if hasattr(self.gamdl_song_interface, 'quality_tier'):
                self.gamdl_song_interface.quality_tier = quality_tier

            if not self.gamdl_downloader_song or not self.gamdl_downloader:
                raise DownloadError(self._gamdl_init_failure_message(wrapper_requested=wrapper_requested))

            # Sanitize song_data: Ensure relationships is a dict, not None, to avoid TypeError in gamdl/tagging
            if song_data and song_data.get('relationships') is None:
                song_data['relationships'] = {}

            # Filter generic "Music" genre so it doesn't end up in gamdl's internal tagging
            if song_data and 'attributes' in song_data and 'genreNames' in song_data['attributes']:
                song_data['attributes']['genreNames'] = [g for g in song_data['attributes']['genreNames'] if g.lower() != 'music']

            # 2. Build and populate AppleMusicMedia via gamdl song interface
            if self._debug: print(f"[Apple Music Debug] Getting download item for track {song_data.get('id')}...")

            media = AppleMusicMedia(
                media_id=song_data.get('id'),
                media_metadata=song_data,
                is_library=bool(kwargs.get('is_library')),
            )

            def _prepare_download_error(e: Exception) -> DownloadError:
                """Map a media-preparation failure to the right user-facing DownloadError."""
                self._maybe_raise_alac_wrapper_error(e, local_effective_codec)
                if self._is_wrapper_auth_error(str(e)):
                    return DownloadError(self._wrapper_not_authenticated_message())
                return DownloadError(f"Apple Music: Failed to prepare download - {type(e).__name__}: {e}")

            with self._gamdl_quiet():
                try:
                    async for populated_media in self.gamdl_song_interface.get_media(media):
                        media = populated_media
                except StopIteration as si:
                    if self._debug:
                        print(f"[Apple Music Error] StopIteration during get_media: {si}")
                        try:
                            attrs = song_data.get('attributes', {})
                            ext_assets = attrs.get('extendedAssetUrls', {})
                            hls_url = ext_assets.get('enhancedHls')
                            print(f"[Apple Music Debug] Enhanced HLS URL present: {bool(hls_url)}")
                            if hls_url:
                                try:
                                    response = await AppleMusicBaseInterface.get_response(hls_url)
                                    import m3u8
                                    m3u8_master = m3u8.loads(response.text)
                                    flavors = [p['stream_info']['audio'] for p in m3u8_master.data.get('playlists', [])]
                                    print(f"[Apple Music Debug] Available flavors in playlist: {flavors}")
                                    print(f"[Apple Music Debug] Requested codec: {local_effective_codec}")
                                except Exception:
                                    print("[Apple Music Debug] Could not fetch/parse HLS flavors for diagnostics.")
                        except Exception:
                            pass
                    raise DownloadError(f"Apple Music: Download failed - StopIteration: {si}. This often means the requested quality/flavor is unavailable for this track.") from si
                except Exception as e:
                    if self._debug: print(f"[Apple Music Error] Failed to prepare media: {type(e).__name__}: {e}")
                    raise _prepare_download_error(e) from e

                if media.error:
                    if self._debug: print(f"[Apple Music Error] media contains error: {media.error}")
                    raise media.error

                try:
                    download_item = await self.gamdl_song_downloader.get_download_item(media)
                except Exception as e:
                    if self._debug: print(f"[Apple Music Error] Failed to get download item: {type(e).__name__}: {e}")
                    raise _prepare_download_error(e) from e

                if download_item.media.error:
                    if self._debug: print(f"[Apple Music Error] download_item contains error: {download_item.media.error}")
                    raise download_item.media.error

            # 4. Check for silent quality fallback (e.g. ALAC/Atmos requested but AAC returned)
            requested_codec_val = local_effective_codec.value if hasattr(local_effective_codec, 'value') else str(local_effective_codec)

            stream_info = download_item.media.stream_info.audio_track if download_item.media.stream_info else None
            actual_codec_val = stream_info.codec if stream_info else None

            if self._debug: print(f"[Apple Music Debug] internal stream codec (actual_codec_val): {actual_codec_val}")

            if requested_codec_val == 'alac' and (
                actual_codec_val is None or not self._is_alac_stream_codec(actual_codec_val)
            ):
                if not self._wrapper_enabled(override_use_wrapper):
                    raise DownloadError(self._alac_requires_wrapper_message())
                raise DownloadError("Apple Music: Could not obtain ALAC stream for this track.")
            elif requested_codec_val == 'atmos' and (
                actual_codec_val is None or not self._is_atmos_stream_codec(actual_codec_val)
            ):
                raise DownloadError("Apple Music: Could not obtain Dolby Atmos stream for this track.")

            # 5. Download and process
            codec_name = local_effective_codec.name if hasattr(local_effective_codec, 'name') else str(local_effective_codec)

            if self._debug and stream_info and getattr(stream_info, 'width', None) and getattr(stream_info, 'height', None):
                # Stream info with width/height means video; for audio we just print the codec
                print(f"{indent_spaces}Detected Stream: {codec_name} ({stream_info.width}x{stream_info.height})")

            if self._debug: print(f"{indent_spaces}Downloading and processing {codec_name} track...")
            # Retry loop for wrapper connection errors. Without the wrapper there is
            # nothing to wait for, so a connection error must fail fast — otherwise
            # the track spins for hours and the end-of-run summary is never reached.
            max_retries = 30 if wrapper_requested else 1
            retry_wait = 10 # Seconds
            restarted_wrapper = False

            for attempt in range(max_retries):
                try:
                    with self._gamdl_quiet():
                        await self.gamdl_downloader.download(download_item)

                    # Sanity check for extremely small files (e.g. 1.5MB for multi-minute ALAC)
                    final_path = Path(download_item.final_path)
                    if final_path.exists():
                        file_size = final_path.stat().st_size
                        duration_sec = 0
                        try:
                            attrs = download_item.media.media_metadata.get('attributes', {})
                            duration_ms = attrs.get('durationInMillis')
                            if duration_ms: duration_sec = duration_ms // 1000
                        except: pass

                        if requested_codec_val in ['alac', 'atmos'] and duration_sec > 30 and file_size < 2000000:
                             isrc = download_item.media.media_metadata.get('attributes', {}).get('isrc')
                             if isrc and not kwargs.get('_is_retry'):
                                 if self._debug: print(f"[Apple Music Warning] Downloaded file is too small ({file_size} bytes). Likely a preview. Attempting to find a better ID for ISRC {isrc} in {self.account_storefront}...")
                                 # Try to find the track again in our account storefront specifically
                                 equiv_id = self._get_equivalent_track_id(isrc, self.account_storefront)
                                 if equiv_id and equiv_id != track_id:
                                     if self._debug: print(f"[Apple Music Debug] Found different ID {equiv_id} for ISRC {isrc}. Retrying download...")
                                     try: final_path.unlink()
                                     except: pass
                                     # Recursive call with retry flag + forced fresh lookup
                                     new_kwargs = kwargs.copy()
                                     new_kwargs['_is_retry'] = True
                                     new_kwargs['api_response'] = None
                                     return await self.get_track_download(equiv_id, quality_tier, codec_options, **new_kwargs)

                             if self._debug: print(f"[Apple Music Error] Downloaded file is suspiciously small ({file_size} bytes for {duration_sec}s). Likely a preview.")
                             raise DownloadError(f"Apple Music: The downloaded {requested_codec_val.upper()} file is corrupt or a preview (too small).")

                    break # Success!

                except Exception as e:
                    error_str = str(e)
                    # Check for amdecrypt connection error (wrapper agent not running)
                    if wrapper_requested and (
                        any(ind in error_str.lower() for ind in _WRAPPER_CONN_ERROR_MARKERS)
                        or isinstance(e, ConnectionRefusedError)
                    ):
                        # Play audible notification if enabled
                        if getattr(self.module_controller.orpheus_options, 'play_sound_on_finish', True):
                            try:
                                current_platform = platform.system()
                                if current_platform == "Windows":
                                    import winsound
                                    winsound.PlaySound("SystemHand", winsound.SND_ALIAS | winsound.SND_ASYNC)
                                elif current_platform == "Darwin":
                                    subprocess.Popen(["afplay", "/System/Library/Sounds/Sosumi.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            except Exception as sound_e:
                                if self._debug: print(f"[Apple Music Warning] Could not play retry sound: {sound_e}")

                        print(f"{indent_spaces}Connection to the local decryption service (Wrapper) failed.")

                        # Attempt to restart the wrapper if a command is configured and it's the first retry
                        restart_command = self.settings.get('wrapper_restart_command')
                        if restart_command and not restarted_wrapper:
                            print(f"{indent_spaces}Attempting to restart decryption wrapper: {restart_command}")
                            try:
                                # Execute the restart command in the background
                                subprocess.run(restart_command, shell=True, capture_output=True, text=True)
                                restarted_wrapper = True
                                # Extra wait for service to initialize after restart command
                                await asyncio.sleep(5)
                            except Exception as restart_e:
                                print(f"{indent_spaces}Wrapper restart command failed: {restart_e}")

                        print(f"{indent_spaces}Waiting {retry_wait}s for restoration before retrying download...")
                        await asyncio.sleep(retry_wait)
                        continue

                    if self._debug: print(f"[Apple Music Error] gamdl download failed: {type(e).__name__}: {e}")
                    raise DownloadError(f"Apple Music: Download execution failed - {type(e).__name__}: {e}") from e

            return download_item

        try:
            # Explicitly pass target storefront to ensure background loop worker sets it correctly
            target_st = kwargs.get('effective_storefront') or kwargs.get('country') or self.account_storefront
            if self._debug: print(f"[Apple Music Debug] Starting download async for {track_id} on storefront '{target_st}'")

            download_item = self._run_async(lambda s: _download_async(), storefront=target_st)

            if self._debug: print(f"[Apple Music Success] Download completed: {download_item.final_path}")

            return TrackDownloadInfo(
                download_type=DownloadEnum.TEMP_FILE_PATH,
                temp_file_path=str(download_item.final_path)
            )

        except (AuthenticationError, TrackUnavailableError, DownloadError):
            raise
        except Exception as e:
            error_str = str(e)

            if self._debug: print(f"[Apple Music Error] Final catch in get_track_download for {track_id}: {type(e).__name__}: {e}")

            # Check for generic amdecrypt connection error strings in cases where it wasn't caught earlier
            if "dial tcp" in error_str and ("refused" in error_str.lower() or "geweigerd" in error_str.lower() or "127.0.0.1" in error_str):
                raise DownloadError(self._wrapper_connection_error_message()) from e

            if '"failureType":"3076"' in error_str:
                raise TrackUnavailableError("This song is unavailable in your region (Error 3076).") from e
            if "too small" in error_str.lower() or " preview" in error_str.lower():
                raise DownloadError("This song is only available as a preview in your region. This usually means it's region-locked or your account cannot access the full track.") from e
            if '"failureType":"2002"' in error_str or "Your session has ended" in error_str:
                raise DownloadError('"cookies.txt" is invalid or expired.')

            if self._debug:
                import traceback
                print(f"[Apple Music Error] Download failed for track {track_id}: {type(e).__name__}: {e}")
                print(traceback.format_exc())

            # Use original exception message if descriptive, else add type
            final_msg = error_str if error_str and len(error_str) > 5 else f"{type(e).__name__}: {e}"

            # Improve FormatNotAvailable or other wrapper-related errors
            requested_codec_name = override_song_codec or self.song_codec
            requested_codec_str = str(requested_codec_name.value if hasattr(requested_codec_name, 'value') else requested_codec_name).lower()

            if requested_codec_str == 'alac' and self._is_alac_license_restriction_error(final_msg):
                final_msg = self._alac_requires_wrapper_message()
            elif self._is_wrapper_auth_error(final_msg):
                final_msg = self._wrapper_not_authenticated_message()
            elif "FormatNotAvailable" in str(type(e)) or "FormatNotAvailable" in final_msg or \
               any(k in final_msg.lower() for k in _WRAPPER_CONN_ERROR_MARKERS) or "connectionrefused" in str(type(e)).lower():
                if requested_codec_str == 'alac':
                    if not self._wrapper_enabled(override_use_wrapper):
                        final_msg = self._alac_requires_wrapper_message()
                    else:
                        final_msg = self._wrapper_connection_error_message()
                elif requested_codec_str == 'atmos':
                    final_msg = "Apple Music: Could not obtain Dolby Atmos stream for this track."

            raise DownloadError(final_msg) from e

    def get_track_lyrics(self, track_id: str, **kwargs) -> Optional[LyricsInfo]:
        # Use provided data if available to save an API call
        song_data = kwargs.get('data')

        # If not provided, we need to fetch it (fallback)
        if not song_data:
            self._ensure_credentials()

            # Use background loop worker to set storefront correctly during fetch
            country = kwargs.get('country')
            song_data = _first(self._run_async(lambda s: s.apple_music_api.get_song(track_id), storefront=country))

        if not song_data:
            return None

        # Initialize gamdl components if not done (needed for gamdl_song_interface)
        # Use standard AAC as it doesn't matter for metadata
        async def _fetch_lyrics():
            await self._initialize_gamdl_components(song_codec=GamdlSongCodec.AAC_WEB)
            if not self.gamdl_song_interface:
                return None
            return await self.gamdl_song_interface.get_lyrics(song_data)

        try:
            lyrics = self._run_async(lambda s: _fetch_lyrics())
            if lyrics:
                return LyricsInfo(
                    embedded=lyrics.unsynced,
                    synced=lyrics.synced
                )
        except Exception as e:
            if self._debug: print(f"[Apple Music Debug] Failed to fetch lyrics for {track_id}: {e}")

        return None

    def get_track_credits(self, track_id: str, data: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[List[CreditsInfo]]:
        # Use existing get_track_info to avoid duplicating extraction logic
        # We pass allow_refetch=True to ensure we get labels/composers
        track_info = self.get_track_info(track_id, QualityEnum.LOW, None, data=data, **kwargs)
        if not track_info or not track_info.tags:
            return []

        credits_dict = []
        if track_info.tags.composer:
            credits_dict.append(CreditsInfo(type='Composer', names=[track_info.tags.composer]))
        if track_info.tags.label:
            credits_dict.append(CreditsInfo(type='Label', names=[track_info.tags.label]))

        return credits_dict

    def get_track_cover(self, track_id: str, cover_options: CoverOptions, data: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[CoverInfo]:
        # Use existing get_track_info to get the cover URL
        track_info = self.get_track_info(track_id, QualityEnum.LOW, None, data=data, **kwargs)
        if not track_info or not track_info.cover_url:
            return None

        # Apple Music artwork URLs are templates, but get_track_info already resolves them
        # using the resolution from settings.
        return CoverInfo(url=track_info.cover_url, file_type=ImageFileTypeEnum.jpg)

    def _extend_paged_tracks(self, tracks_rel: dict) -> None:
        """Fetch the remaining pagination pages of a tracks relationship and merge them in place."""
        if 'next' not in tracks_rel:
            return

        async def fetch_all(api, rel):
            all_data = rel.get('data', [])
            async for page in api.extend_api_data(rel):
                all_data.extend(page.get('data', []))
            return all_data

        if self._debug: print(f"[Apple Music Debug] Fetching remaining tracks via pagination...")
        # extend_api_data uses the API's current storefront; restore it afterwards
        current_sf = self.apple_music_api.storefront
        try:
            paged_tracks = self._run_async(lambda s: fetch_all(s.apple_music_api, tracks_rel))
            if paged_tracks:
                if self._debug: print(f"[Apple Music Debug] Total tracks after pagination: {len(paged_tracks)}")
                tracks_rel['data'] = paged_tracks
        except Exception as e:
            if self._debug: print(f"[Apple Music Warning] Pagination failed, using available tracks: {e}")
        finally:
            self.apple_music_api.storefront = current_sf

    def _track_row(self, track: dict, idx: int, default_artist: str, release_year,
                   fallback_cover: str, inherit_attrs: dict = None):
        """Build one track dict for AlbumInfo/PlaylistInfo track lists.

        Returns the bare track ID (string) when the item carries no attributes,
        matching the format get_track_info expects for ID-only entries.
        """
        t_attrs = track.get('attributes') or {}
        if not t_attrs:
            return track.get('id', '')

        if 'url' in t_attrs:
            t_attrs['url'] = self._localize_url(t_attrs['url'])

        dur_ms = t_attrs.get('durationInMillis')
        # Library tracks sometimes lack artistName — inherit the container's artist
        artist_attrs = dict(t_attrs)
        if not artist_attrs.get('artistName'):
            artist_attrs['artistName'] = default_artist
        # Pass container-level metadata (label/copyright/upc) through to each track
        for key, value in (inherit_attrs or {}).items():
            if value and key not in t_attrs:
                t_attrs[key] = value
        previews = t_attrs.get('previews') or []

        return {
            'id': track.get('id', ''),
            'name': t_attrs.get('name') or f'Track {idx}',
            'duration': (dur_ms // 1000) if isinstance(dur_ms, (int, float)) else None,
            'artists': artists_from_apple_attrs(artist_attrs),
            'release_year': release_year,
            'cover_url': self._get_cover_url(t_attrs.get('artwork', {}).get('url')) or fallback_cover,
            'preview_url': previews[0].get('url') if previews else None,
            # Pass full API data so get_track_info doesn't need to refetch
            'attributes': t_attrs,
            'relationships': track.get('relationships') or {},
            'type': track.get('type'),
            'additional': self._format_audio_traits(t_attrs, item_type='songs'),
        }

    def get_album_info(self, album_id: str, data: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[AlbumInfo]:
        """Get album information (catalog works without cookies; download requires credentials)."""
        try:
            # Extract country from kwargs/data and set storefront
            country = kwargs.get('country') or (data.get('country') if data else None) or (kwargs.get('data', {}).get('country') if isinstance(kwargs.get('data'), dict) else None)
            self._set_storefront(country)

            # Full album data may arrive via kwargs (from get_artist_info) or data
            album_data = kwargs.get('data') or data
            if isinstance(album_data, list) and album_data:
                album_data = album_data[0]
            elif isinstance(album_data, dict):
                album_data = _first(album_data)

            # If we don't have valid attributes, fetch from API
            if not album_data or not isinstance(album_data, dict) or 'attributes' not in album_data:
                if self._debug: print(f"[Apple Music Debug] Fetching full album info for {album_id}")
                is_library = kwargs.get('is_library', False) or str(album_id).startswith('l.')
                if is_library:
                    album_data = self._run_async(lambda s: s.apple_music_api.get_library_album(album_id), storefront=country)
                else:
                    album_data = self._run_async(lambda s: s.apple_music_api.get_album(album_id), storefront=country)
                album_data = _first(album_data)

            tracks_rel = (album_data.get('relationships') or {}).get('tracks')
            if tracks_rel:
                self._extend_paged_tracks(tracks_rel)

            attrs = album_data['attributes']
            if 'url' in attrs:
                attrs['url'] = self._localize_url(attrs['url'])

            album_artist_display = format_album_artist_tag(
                attrs.get('albumArtistName') or attrs.get('artistName', '')
            )
            album_artist = format_album_artist_tag(
                attrs.get('albumArtistName') or attrs.get('artistName', ''),
                primary_only=True,
            )
            cover_url = self._get_cover_url(attrs.get('artwork', {}).get('url'))
            album_release_date = attrs.get('releaseDate')
            release_year = self._extract_year(album_release_date)

            # Extract record label, copyright and UPC (Barcode) from album attributes
            record_label = attrs.get('recordLabel')
            copyright_info = attrs.get('copyright')
            upc = attrs.get('upc')

            if self._debug: print(f"[Apple Music Metadata Debug] Available keys for album {album_id}: {list(attrs.keys())}")

            if not record_label and copyright_info:
                record_label = _label_from_copyright(copyright_info)

            # Use full track data from the album response to avoid N get_track_info calls in the GUI
            tracks_out = [
                self._track_row(
                    track, idx, album_artist, release_year, cover_url,
                    inherit_attrs={'recordLabel': record_label, 'copyright': copyright_info, 'upc': upc},
                )
                for idx, track in enumerate((tracks_rel or {}).get('data', []), start=1)
            ]

            # Extract artist ID from relationships
            artist_rels = (album_data.get('relationships') or {}).get('artists', {}).get('data', [])
            artist_id = artist_rels[0].get('id', '') if artist_rels else ''

            return AlbumInfo(
                name=attrs['name'],
                artist=album_artist_display,
                album_artist=album_artist,
                artist_id=str(artist_id) if artist_id else None,
                id=str(album_id),
                quality=self._album_quality_label_from_attrs(attrs),
                cover_url=cover_url,
                release_year=release_year,
                label=record_label,
                upc=upc,
                tracks=tracks_out,
                expected_track_count=int(attrs['trackCount']) if attrs.get('trackCount') is not None else None,
                track_extra_kwargs={
                    **kwargs,
                    'country': country,
                    'album_release_date': album_release_date,
                    'album_artist': album_artist,
                },
            )

        except Exception as e:
            raise self.exception(f"Failed to get album info: {e}")

    def get_playlist_info(self, playlist_id, data: dict = None, **kwargs):
        """Get playlist information (catalog works without cookies; download requires credentials)."""
        try:
            # Extract country from kwargs and set storefront
            country = kwargs.get('country') or (data.get('country') if data else None)
            self._set_storefront(country)

            def fetch_playlist():
                if str(playlist_id).startswith('p.') or kwargs.get('is_library'):
                    return self._run_async(lambda s: s.apple_music_api.get_library_playlist(playlist_id), storefront=country)
                return self._run_async(lambda s: s.apple_music_api.get_playlist(playlist_id), storefront=country)

            # Check if we have raw_result from search - use it unless it lacks track relationships
            if kwargs.get('raw_result'):
                playlist_data = kwargs['raw_result']
                if self._debug: print(f"[Apple Music Debug] Using raw_result from search for playlist {playlist_id}")
                rels = playlist_data.get('relationships')
                if not rels or 'tracks' not in rels or not rels['tracks'].get('data'):
                    if self._debug: print(f"[Apple Music Debug] Search result missing track data, fetching full playlist info...")
                    playlist_data = fetch_playlist()
            else:
                playlist_data = fetch_playlist()

            playlist_data = _first(playlist_data)

            tracks_rel = (playlist_data.get('relationships') or {}).get('tracks')
            if tracks_rel:
                self._extend_paged_tracks(tracks_rel)

            attrs = playlist_data['attributes']
            if 'url' in attrs:
                attrs['url'] = self._localize_url(attrs['url'])

            cover_url = self._get_cover_url(attrs.get('artwork', {}).get('url'))
            release_year = self._extract_year(attrs.get('lastModifiedDate'))
            creator = attrs.get('curatorName', 'Unknown Creator')

            # Use full track data from the playlist response to avoid N get_track_info calls in the GUI
            tracks_out = [
                self._track_row(track, idx, creator, release_year, cover_url)
                for idx, track in enumerate((tracks_rel or {}).get('data', []), start=1)
            ]

            return PlaylistInfo(
                name=attrs.get('name', 'Unknown Playlist'),
                creator=creator,
                release_year=release_year,
                tracks=tracks_out,
                cover_url=cover_url,
                track_extra_kwargs={**kwargs, 'country': country}
            )

        except Exception as e:
            raise self.exception(f"Failed to get playlist info: {e}")

    def get_artist_info(self, artist_id, get_credited_albums=True, data: dict = None, **kwargs):
        """Get artist information (catalog works without cookies; download requires credentials)."""
        # Extract country from kwargs and set storefront
        country = kwargs.get('country') or (data.get('country') if data else None)
        self._set_storefront(country)

        # Reverting to the call without 'include' which seems to be more robust
        artist_data = _first(self._run_async(lambda s: s.apple_music_api.get_artist(artist_id), storefront=country))

        # Defensive check for API response structure. Expecting a dict.
        if not artist_data or not isinstance(artist_data, dict) or 'attributes' not in artist_data:
            if self._debug: print(f"[Apple Music Debug] Unexpected artist data response for ID {artist_id} on storefront '{self.apple_music_api.storefront}': {artist_data}")
            raise self.exception(f"No data returned for artist ID {artist_id}. They may not be available on the '{self.apple_music_api.storefront}' storefront.")

        attrs = artist_data['attributes']
        if 'url' in attrs:
            attrs['url'] = self._localize_url(attrs['url'])

        artist_name = attrs.get('name', 'Unknown Artist')
        cover_url_default = self._get_cover_url(attrs.get('artwork', {}).get('url'))

        def process_album_item(album_item):
            a_attrs = album_item.get('attributes') or {}
            if not a_attrs:
                return album_item.get('id', '')
            if 'url' in a_attrs:
                a_attrs['url'] = self._localize_url(a_attrs['url'])

            additional_parts = []
            tc = a_attrs.get('trackCount')
            if tc: additional_parts.append("1 track" if tc == 1 else f"{tc} tracks")
            traits = self._format_audio_traits(a_attrs, item_type='albums')
            if traits: additional_parts.append(traits)

            return {
                'id': album_item.get('id', ''),
                'name': a_attrs.get('name') or 'Unknown Album',
                'artist': format_album_artist_tag(a_attrs.get('artistName') or artist_name),
                'release_year': self._extract_year(a_attrs.get('releaseDate')),
                'cover_url': self._get_cover_url(a_attrs.get('artwork', {}).get('url')) or cover_url_default,
                'additional': " / ".join(additional_parts),
                'explicit': a_attrs.get('contentRating') == 'explicit',
                # Pass full API data so get_album_info doesn't need to refetch
                'attributes': a_attrs,
                'relationships': album_item.get('relationships'),
                'type': album_item.get('type')
            }

        # Standard albums relationship
        rel_albums = (artist_data.get('relationships') or {}).get('albums', {}).get('data', [])
        albums_out = [process_album_item(album) for album in rel_albums]

        # Extended views (GAMDL 2.8.5+): 'top-songs' become tracks, everything else
        # is categorized into albums_out with a prefix.
        tracks_out = []
        category_prefixes = {
            'compilation-albums': "[Compilation] ",
            'live-albums': "[Live] ",
            'singles': "[Single/EP] ",
        }
        for view_name, view_data in artist_data.get('views', {}).items():
            view_items = view_data.get('data', [])
            if view_name == 'top-songs':
                for song_item in view_items:
                    s_attrs = song_item.get('attributes') or {}
                    if not s_attrs:
                        continue
                    if 'url' in s_attrs:
                        s_attrs['url'] = self._localize_url(s_attrs['url'])
                    s_artist_attrs = dict(s_attrs)
                    if not s_artist_attrs.get('artistName'):
                        s_artist_attrs['artistName'] = artist_name
                    dur_ms = s_attrs.get('durationInMillis')
                    tracks_out.append({
                        'id': song_item.get('id', ''),
                        'name': s_attrs.get('name') or 'Unknown Track',
                        'artists': artists_from_apple_attrs(s_artist_attrs),
                        'duration': (dur_ms // 1000) if isinstance(dur_ms, (int, float)) else 0,
                        'release_year': self._extract_year(s_attrs.get('releaseDate')),
                        'cover_url': self._get_cover_url(s_attrs.get('artwork', {}).get('url')) or cover_url_default,
                        'additional': self._format_audio_traits(s_attrs, item_type='songs'),
                        'attributes': s_attrs,
                        'relationships': song_item.get('relationships'),
                        'type': song_item.get('type')
                    })
            else:
                prefix = category_prefixes.get(view_name, "")
                for album_item in view_items:
                    processed = process_album_item(album_item)
                    if isinstance(processed, dict) and prefix:
                        processed['name'] = prefix + processed['name']
                    albums_out.append(processed)

        # Batch fetch missing durations for albums
        albums_to_fetch = [idx for idx, t in enumerate(albums_out) if isinstance(t, dict) and not t.get('duration')]
        if albums_to_fetch:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                fetch_ids = [albums_out[idx]['id'] for idx in albums_to_fetch]
                for aid, dur in executor.map(self._fetch_am_album_duration, fetch_ids):
                    if dur:
                        for idx in albums_to_fetch:
                            if albums_out[idx]['id'] == aid:
                                albums_out[idx]['duration'] = dur

        return ArtistInfo(
            name=artist_name,
            artist_id=artist_id,
            albums=albums_out,
            album_extra_kwargs={**kwargs, 'country': country},
            tracks=tracks_out,
            track_extra_kwargs={**kwargs, 'country': country}
        )

    def _fetch_am_playlist_meta(self, pid):
        """(track_count, total_duration_seconds) for a playlist — search backfill."""
        try:
            item = _first(self._run_async(lambda s: s.apple_music_api.get_playlist(pid)))
            if isinstance(item, dict):
                attrs = item.get('attributes', {})
                tc = attrs.get('trackCount')
                rel_tracks = (item.get('relationships') or {}).get('tracks', {}).get('data', [])
                total_duration = None
                if rel_tracks:
                    sum_dur = sum(t.get('attributes', {}).get('durationInMillis', 0) for t in rel_tracks if isinstance(t.get('attributes'), dict))
                    if sum_dur > 0:
                        total_duration = sum_dur // 1000
                if tc is not None and tc > 0:
                    return pid, tc, total_duration
                # Fallback to relationship length
                if rel_tracks:
                    return pid, len(rel_tracks), total_duration
        except Exception:
            pass
        return pid, None, None

    def _fetch_am_album_duration(self, aid, storefront=None):
        """Total duration in seconds of an album's tracks — search/artist backfill."""
        try:
            item = _first(self._run_async(lambda s: s.apple_music_api.get_album(aid), storefront=storefront))
            if isinstance(item, dict):
                rel_tracks = (item.get('relationships') or {}).get('tracks', {}).get('data', [])
                if rel_tracks:
                    sum_dur = sum(t.get('attributes', {}).get('durationInMillis', 0) for t in rel_tracks if isinstance(t.get('attributes'), dict))
                    if sum_dur > 0:
                        return aid, sum_dur // 1000
        except Exception:
            pass
        return aid, None

    def _localize_url(self, url):
        """Replace the country code in an Apple Music URL with the account storefront."""
        if not url or not self.account_storefront:
            return url
        # Matches any 2-letter country code following music.apple.com/
        return re.sub(r'music\.apple\.com/[a-z]{2}/', f'music.apple.com/{self.account_storefront}/', url)

    def _get_cover_url(self, artwork_template):
        """Build a full cover URL from a template"""
        if not artwork_template:
            return None

        try:
            # Get resolution from global settings, default to 1400
            res = self.module_controller.orpheus_options.default_cover_options.resolution
        except Exception as e:
            if getattr(self, '_debug', False): print(f"[Apple Music Error] Failed to get resolution from settings: {e}. Falling back to 1400.")
            res = 1400

        # Replace template markers with resolution
        return artwork_template.replace('{w}', str(res)).replace('{h}', str(res))

    def _album_quality_label_from_attrs(self, attrs) -> Optional[str]:
        """Short quality label for album folder names (discography disambiguation)."""
        traits = attrs.get('audioTraits') or []
        labels = []
        if any(t in traits for t in ('atmos', 'spatial')):
            labels.append('Atmos')
        if 'hi-res-lossless' in traits:
            labels.append('HI-RES')
        elif 'lossless' in traits:
            labels.append('Lossless')
        return ' · '.join(labels) if labels else None

    def _format_audio_traits(self, attrs, item_type=None):
        """Format audio traits according to GUI display rules"""
        if 'audioTraits' not in attrs:
            # Playlists don't expose per-playlist audioTraits; their catalog tracks
            # stream lossless for subscribers. Show a Lossless badge so playlist rows
            # don't look blank next to album/song rows.
            if item_type == 'playlists':
                return "Lossless"
            return ""

        traits = []
        has_atmos = False
        is_lossless = False
        is_hires = False

        for trait in attrs['audioTraits']:
            # 'lossy-stereo' is standard
            if trait == 'lossy-stereo':
                continue
            elif trait == 'lossless':
                is_lossless = True
            elif trait in ('atmos', 'spatial'):
                has_atmos = True
            elif trait == 'hi-res-lossless':
                traits.append('🅷 HI-RES')
                is_lossless = True
                is_hires = True
            else:
                traits.append(trait.replace('-', ' ').title())

        if not is_lossless and item_type in ('songs', 'music-videos'):
            traits.append('AAC only')
        elif is_lossless and not is_hires:
            # Standard CD-quality lossless has no special audioTrait marker, so the
            # Additional column would otherwise be blank. Show it explicitly.
            traits.append('Lossless')

        if has_atmos:
            # Add Atmos trait if detected, always first
            traits.insert(0, '◗◖ ATMOS')

        return " / ".join(traits)

    def _get_precise_alac_info(self, attrs, codec, quality_tier: QualityEnum = None):
        """Fetch HLS manifest and parse audio group ID for exact bit depth and sample rate"""
        # Lazy imports for gamdl logic
        try:
            from gamdl.interface.base import AppleMusicBaseInterface
            from gamdl.interface.constants import SONG_CODEC_REGEX_MAP
            import m3u8
        except ImportError:
            return None

        hls_url = attrs.get('extendedAssetUrls', {}).get('enhancedHls')
        if not hls_url:
            return None

        # Cache successful probes per manifest URL so tracks of the same album
        # display a consistent sample rate (and we don't hammer the CDN once per
        # track). Only successful results are cached, so a transient failure never
        # poisons the cache.
        cache = getattr(self, '_precise_alac_cache', None)
        if cache is None:
            cache = self._precise_alac_cache = {}
        cache_key = (codec.value, quality_tier, hls_url)
        if cache_key in cache:
            return cache[cache_key]

        async def _fetch_manifest():
            # Use gamdl's codec matching logic
            codec_regex = SONG_CODEC_REGEX_MAP.get(codec.value)
            if not codec_regex:
                return None

            # Retry a few times: HLS probes transiently fail under load/rate
            # limits, and a single failed probe degrades the displayed sample
            # rate to the 48kHz fallback even when the real stream is hi-res.
            last_error = None
            for attempt in range(3):
                try:
                    # Use gamdl's get_response utility (uses httpx)
                    response = await AppleMusicBaseInterface.get_response(hls_url)
                    m3u8_obj = m3u8.loads(response.text)
                    m3u8_data = m3u8_obj.data

                    matching_playlists = [
                        p for p in m3u8_data.get('playlists', [])
                        if re.fullmatch(codec_regex, p["stream_info"]["audio"])
                    ]

                    if not matching_playlists:
                        return None

                    # Standard-lossless requests cap at 48kHz so HI-RES (96k+) variants are excluded.
                    if codec.value == "alac" and quality_tier == QualityEnum.LOSSLESS:
                        filtered = _filter_standard_lossless(matching_playlists)
                        if filtered:
                            matching_playlists = filtered

                    # Pick the highest bandwidth playlist for this codec (respecting our filter above)
                    target = max(matching_playlists, key=lambda x: x["stream_info"]["average_bandwidth"])
                    audio_group_id = target["stream_info"]["audio"] # e.g. "audio-alac-stereo-44100-24"

                    # Parse audio-alac-stereo-SAMPLE_RATE-BIT_DEPTH
                    # Regex: audio-alac-(?:stereo|binaural|downmix)-(\d+)-(\d+)
                    match = re.search(r'-(\d+)-(\d+)$', audio_group_id)
                    if match:
                        return {
                            'sample_rate': int(match.group(1)),
                            'bit_depth': int(match.group(2))
                        }
                    return None
                except Exception as e:
                    last_error = e
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (attempt + 1))
            if getattr(self, '_debug', False) and last_error:
                print(f"[Apple Music Debug] Precise info fetch failed after retries: {last_error}")
            return None

        # Run in our background event loop
        result = self._run_async(lambda s: _fetch_manifest())
        if result and len(cache) < 200:
            cache[cache_key] = result
        return result

    def _get_global_lyrics_settings(self) -> dict:
        """Read global lyrics settings from OrpheusDL config/settings.json."""
        lyrics = self._read_orpheus_settings().get('global', {}).get('lyrics', {})
        return {
            'embed_lyrics': lyrics.get('embed_lyrics', True),
            'embed_synced_lyrics': lyrics.get('embed_synced_lyrics', False),
            'save_synced_lyrics': lyrics.get('save_synced_lyrics', True),
        }

    def _resolve_all_binary_paths(self):
        """Pre-resolve all binary paths to speed up future re-initializations"""
        if hasattr(self, 'binary_paths'):
            return
        if self._debug: print("[Apple Music Debug] Resolving binary paths...")

        advanced = self._read_orpheus_settings().get("global", {}).get("advanced", {})

        def resolve_binary_path(binary_name, configured):
            # A user-configured path (anything other than the plain default name) wins as-is
            if configured != binary_name:
                return configured

            search_paths = []
            if platform.system() == "Darwin":
                # macOS GUI builds keep binaries in Application Support
                app_support = os.path.expanduser("~/Library/Application Support/OrpheusDL GUI")
                search_paths.append(os.path.join(app_support, binary_name))
            if getattr(sys, 'frozen', False):
                app_dir = os.path.dirname(sys.executable)
                search_paths.append(os.path.join(app_dir, binary_name))
                if platform.system() == "Darwin" and ".app/Contents/MacOS" in sys.executable:
                    bundle_dir = os.path.dirname(os.path.dirname(os.path.dirname(sys.executable)))
                    search_paths.append(os.path.join(bundle_dir, binary_name))
                    search_paths.append(os.path.join(os.path.dirname(bundle_dir), binary_name))
            search_paths.append(os.path.join(os.getcwd(), binary_name))

            for path in search_paths:
                if os.path.isfile(path):
                    if not os.access(path, os.X_OK):
                        try:
                            os.chmod(path, 0o755)
                        except Exception:
                            pass
                    return path
            return shutil.which(binary_name) or binary_name

        def exe(name):
            return f"{name}.exe" if platform.system() == "Windows" else name

        self.binary_paths = {
            'ffmpeg': resolve_binary_path(exe("ffmpeg"), advanced.get("ffmpeg_path", "ffmpeg")),
            'mp4box': resolve_binary_path(exe("MP4Box"), advanced.get("mp4box_path", "MP4Box")),
            'mp4decrypt': resolve_binary_path(exe("mp4decrypt"), advanced.get("mp4decrypt_path", "mp4decrypt")),
            'nm3u8dlre': resolve_binary_path(exe("N_m3u8DL-RE"), self.settings.get('nm3u8dlre_path', 'N_m3u8DL-RE')),
        }

        if self._debug: print(f"[Apple Music Debug] Binary paths resolved: {self.binary_paths}")
