import os
import sys
import time
import re
from pathlib import Path
from typing import Dict, Any, Optional
import json
import requests
import tempfile
import platform
import shutil
from enum import Enum
from contextlib import contextmanager
import ssl
import urllib.request
import asyncio
import concurrent.futures
import datetime
import threading

# Add gamdl to the path
current_dir = Path(__file__).parent
gamdl_path = current_dir / "gamdl"
if str(gamdl_path) not in sys.path:
    sys.path.insert(0, str(gamdl_path))

# Initialize gamdl availability check
GAMDL_AVAILABLE = False
LAST_GAMDL_ERROR = None

def _lazy_import_gamdl():
    """Lazy import gamdl components to avoid conflicts with GUI patches"""
    global GAMDL_AVAILABLE, LAST_GAMDL_ERROR, AppleMusicApi, ItunesApi, GamdlSongCodec, GamdlRemuxMode, GamdlDownloadMode, \
        AppleMusicDownloader, AppleMusicBaseDownloader, AppleMusicSongDownloader, \
        AppleMusicInterface, AppleMusicSongInterface, \
        SyncedLyricsFormat
    
    if GAMDL_AVAILABLE:
        return True
    
    # --- Start of Patch ---
    # Create a universal mock class that can be used for any missing module.
    # It handles attribute access, calls, and iteration to satisfy the import system.
    class _UniversalMock:
        def __init__(self, *args, **kwargs): pass
        def __call__(self, *args, **kwargs): return self
        def __getattr__(self, name): return self
        def __iter__(self): yield from ()

    _mock_instance = _UniversalMock()

    # Pre-emptively place mocks for modules and any known submodules into sys.modules.
    # This is required to fool 'from ... import ...' statements for nested modules.
    modules_to_mock = [
        'click',
        'colorama',
        'InquirerPy',
        'inquirerpy',
        'InquirerPy.base',
        'InquirerPy.base.control',
        'inquirerpy.base',
        'inquirerpy.base.control'
    ]
    for mod_name in modules_to_mock:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = _mock_instance
    # --- End of Patch ---

    # Ensure gamdl path is in sys.path
    current_dir = Path(__file__).parent
    gamdl_path = current_dir / "gamdl"
    if str(gamdl_path) not in sys.path:
        sys.path.insert(0, str(gamdl_path))
    
    # Debug: Check if gamdl directory exists
    if not gamdl_path.exists():
        print(f"[Apple Music Error] gamdl directory NOT found at: {gamdl_path}")
        globals()['LAST_GAMDL_ERROR'] = f"gamdl directory NOT found at: {gamdl_path}"
        return False
    
    # Debug: Check if key files exist
    apple_music_api_file = gamdl_path / "gamdl" / "api" / "apple_music_api.py"
    if not apple_music_api_file.exists():
        msg = f"apple_music_api.py NOT found at: {apple_music_api_file}"
        print(f"[Apple Music Error] {msg}")
        globals()['LAST_GAMDL_ERROR'] = msg
        return False
    
    # Debug: Show path info
    # print(f"[Apple Music] gamdl path: {gamdl_path}")
    # print(f"[Apple Music] gamdl path exists: {gamdl_path.exists()}")
    # gamdl_paths_in_sys = [p for p in sys.path if 'gamdl' in p]
    # print(f"[Apple Music] Current sys.path entries containing 'gamdl': {gamdl_paths_in_sys}")
    
    # Temporarily fix subprocess.Popen to avoid conflicts with yt-dlp
    original_popen = None
    current_popen = None
    subprocess_module = sys.modules.get('subprocess')
    
    if subprocess_module and hasattr(subprocess_module, 'Popen'):
        current_popen = subprocess_module.Popen
        
        # Check if Popen has been patched (if it's not a class, it's been patched)
        if not isinstance(current_popen, type):
            # Create a temporary class that yt-dlp can subclass
            # This wraps the patched function to make it look like a class
            class TempPopen:
                def __new__(cls, *args, **kwargs):
                    # Call the patched function
                    return current_popen(*args, **kwargs)
                
                # Copy some attributes that might be expected
                def __init__(self, *args, **kwargs):
                    pass
            
            print(f"[Apple Music Debug] Temporarily replacing patched subprocess.Popen with class wrapper for yt-dlp compatibility")
            subprocess_module.Popen = TempPopen
            original_popen = current_popen
    
    try:
        from gamdl.api import AppleMusicApi, ItunesApi
        from gamdl.downloader import (
            AppleMusicBaseDownloader,
            AppleMusicDownloader,
            AppleMusicSongDownloader,
        )
        from gamdl.interface import (
            AppleMusicInterface,
            AppleMusicSongInterface,
        )
        from gamdl.interface.enums import (
            SongCodec as GamdlSongCodec,
            SyncedLyricsFormat
        )
        from gamdl.downloader.enums import (
            ArtistAutoSelect,
            DownloadMode as GamdlDownloadMode,
            RemuxMode as GamdlRemuxMode
        )

        globals()['AppleMusicApi'] = AppleMusicApi
        globals()['ItunesApi'] = ItunesApi
        globals()['GamdlSongCodec'] = GamdlSongCodec
        globals()['GamdlRemuxMode'] = GamdlRemuxMode
        globals()['GamdlDownloadMode'] = GamdlDownloadMode
        globals()['ArtistAutoSelect'] = ArtistAutoSelect
        globals()['AppleMusicDownloader'] = AppleMusicDownloader
        globals()['AppleMusicBaseDownloader'] = AppleMusicBaseDownloader
        globals()['AppleMusicSongDownloader'] = AppleMusicSongDownloader
        globals()['AppleMusicInterface'] = AppleMusicInterface
        globals()['AppleMusicSongInterface'] = AppleMusicSongInterface
        globals()['SyncedLyricsFormat'] = SyncedLyricsFormat
        from gamdl.interface.constants import LEGACY_SONG_CODECS
        globals()['LEGACY_SONG_CODECS'] = LEGACY_SONG_CODECS

        class OrpheusAppleMusicSongInterface(AppleMusicSongInterface):
            def __init__(self, interface: AppleMusicInterface, quality_tier: QualityEnum = None, debug: bool = False):
                super().__init__(interface)
                self.quality_tier = quality_tier
                self._debug = debug

            def _get_playlist_from_codec(self, m3u8_data: dict, codec: 'GamdlSongCodec') -> dict | None:
                from gamdl.interface.constants import SONG_CODEC_REGEX_MAP
                import re
                
                # Check for Atmos first with a more inclusive logic if it's the requested codec
                if codec.value == 'atmos':
                    # First try standard "atmos" regex
                    matching_playlists = [
                        playlist for playlist in m3u8_data["playlists"]
                        if re.fullmatch(SONG_CODEC_REGEX_MAP['atmos'], playlist["stream_info"]["audio"])
                    ]
                    
                    # If no "atmos" found, try "ac3" as a fallback for Atmos/Surround
                    if not matching_playlists:
                        if self._debug:
                            print(f"[Apple Music Debug] No 'audio-atmos' found. Trying 'audio-ac3' as fallback for Atmos/Surround.")
                        matching_playlists = [
                            playlist for playlist in m3u8_data["playlists"]
                            if re.fullmatch(SONG_CODEC_REGEX_MAP['ac3'], playlist["stream_info"]["audio"])
                        ]
                else:
                    matching_playlists = [
                        playlist
                        for playlist in m3u8_data["playlists"]
                        if re.fullmatch(
                            SONG_CODEC_REGEX_MAP[codec.value], playlist["stream_info"]["audio"]
                        )
                    ]

                if not matching_playlists:
                    if self._debug:
                        flavors = [p["stream_info"]["audio"] for p in m3u8_data["playlists"]]
                        print(f"[Apple Music Debug] No matching playlist for codec '{codec.value}'. Available flavors: {flavors}")
                    return None

                # Filter for LOSSLESS (Standard Lossless) to avoid HI-RES (96k+)
                
                if codec.value == "alac" and (self.quality_tier == QualityEnum.LOSSLESS or self.quality_tier == QualityEnum.HIFI):
                    # We always want to check for Hi-Res if we haven't explicitly requested it via HIFI+SomeOtherFlag?
                    # Wait, if the user hit ALAC, they likely want standard lossless.
                    # If they hit HI-RES, they want Hi-Res.
                    
                    # If quality_tier is LOSSLESS, we definitely want to limit to 48k.
                    # If quality_tier is HIFI, we usually want max, UNLESS it's an "ALAC" button that passes HIFI?
                    
                    # Let's check bitwise for LOSSLESS specifically.
                    if self.quality_tier == QualityEnum.LOSSLESS:
                        filtered = []
                        for p in matching_playlists:
                            audio_id = p["stream_info"]["audio"] 
                            try:
                                parts = audio_id.split('-')
                                if len(parts) >= 4:
                                    sample_rate = int(parts[-2])
                                    if sample_rate <= 48000:
                                        filtered.append(p)
                                else:
                                    filtered.append(p)
                            except:
                                filtered.append(p)
                        
                        if filtered:
                            matching_playlists = filtered
                        elif self.quality_tier == QualityEnum.LOSSLESS:
                            print(f"[Apple Music Debug] No playlists matched sample_rate <= 48000. Returning best available.")

                return max(
                    matching_playlists,
                    key=lambda x: x["stream_info"]["average_bandwidth"],
                )

        globals()['OrpheusAppleMusicSongInterface'] = OrpheusAppleMusicSongInterface
        globals()['GAMDL_AVAILABLE'] = True
        LAST_GAMDL_ERROR = None
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
        # Restore the patched subprocess.Popen if we temporarily changed it
        if original_popen and subprocess_module:
            print(f"[Apple Music Debug] Restoring patched subprocess.Popen")
            subprocess_module.Popen = original_popen

# Initialize global variables for lazy imports
AppleMusicApi = None
ItunesApi = None  
GamdlSongCodec = None
GamdlRemuxMode = None
GamdlDownloadMode = None
AppleMusicDownloader = None
AppleMusicBaseDownloader = None
AppleMusicSongDownloader = None
AppleMusicMusicVideoDownloader = None
AppleMusicUploadedVideoDownloader = None
AppleMusicInterface = None
AppleMusicSongInterface = None
AppleMusicMusicVideoInterface = None
AppleMusicUploadedVideoInterface = None
LEGACY_SONG_CODECS = None
SyncedLyricsFormat = None
RemuxFormatMusicVideo = None

from utils.models import *
from utils.utils import create_temp_filename, download_to_temp

from utils.models import (
    TrackInfo, AlbumInfo, ArtistInfo, PlaylistInfo, LyricsInfo, 
    DownloadTypeEnum, QualityEnum,
    DownloadEnum as OrpheusDownloadEnum,
    ModuleInformation, ModuleModes, ManualEnum, Tags, CodecEnum,
    TrackDownloadInfo
)
from utils.exceptions import AuthenticationError, DownloadError, TrackUnavailableError

module_information = ModuleInformation(
    service_name='Apple Music',
    module_supported_modes=ModuleModes.download,
    session_settings={
        'cookies_path': './config/cookies.txt',
        'language': 'en-US',
        'use_wrapper': False,
        'wrapper_decrypt_ip': '127.0.0.1:10020'
    },
    netlocation_constant='music.apple',
    test_url='https://music.apple.com/us/album/1989-taylors-version/1708308989',
    url_decoding=ManualEnum.manual,
    login_behaviour=ManualEnum.manual
)

@contextmanager
def suppress_gamdl_debug():
    """Context manager to suppress verbose gamdl debug messages"""
    import threading
    
    # Use thread-local storage to avoid conflicts during concurrent downloads
    if not hasattr(threading.current_thread(), '_original_stdout'):
        threading.current_thread()._original_stdout = sys.stdout
    
    original_stdout = threading.current_thread()._original_stdout
    
    # Use devnull to completely suppress output
    devnull = open(os.devnull, 'w')
    
    try:
        sys.stdout = devnull
        yield
    finally:
        devnull.close()
        sys.stdout = original_stdout

class ModuleInterface:
    def __init__(self, module_controller: ModuleController):
        self.exception = module_controller.module_error
        settings = module_controller.module_settings
        self.module_controller = module_controller
        self.settings = settings
        self.printer = module_controller.printer_controller
        self.gamdl_downloader_song = None
        self.gamdl_downloader = None # To store the gamdl.Downloader instance
        self.is_authenticated = False  # Default to not authenticated
        self._using_rich_tagging = False  # Track when we're using gamdl's rich tagging to prevent OrpheusDL overwriting
        # Consolidate debug setting from module-specific and global settings
        self._debug = settings.get('debug', False) or (
            hasattr(module_controller, 'settings') and 
            module_controller.settings.get('global', {}).get('advanced', {}).get('debug_mode', False)
        )
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
        
        if not _lazy_import_gamdl():
            detail = f": {LAST_GAMDL_ERROR}" if LAST_GAMDL_ERROR else ""
            raise self.exception(f"gamdl components not available - please check installation{detail}")
        
        # Get cookies path from settings
        cookies_path = self.settings.get('cookies_path', './config/cookies.txt')
        if cookies_path and not os.path.exists(cookies_path):
            # Try default location
            default_cookies = Path('./config/cookies.txt')
            if default_cookies.exists():
                cookies_path = str(default_cookies)
            else:
                if self._debug:
                    print(f"[Apple Music Warning] Cookies file not found at specified/default path: {cookies_path}. Downloads may fail if authentication is required.")
                cookies_path = None
        
        if self._debug:
            print(f"[Apple Music Debug] Using cookies_path: {os.path.abspath(cookies_path) if cookies_path else 'None'}")
        
        # Initialize gamdl APIs
        try:
            # Control gamdl debug output via environment variable
            if not self._debug:
                os.environ['GAMDL_DEBUG'] = 'false'
            
            self._run_async(self._setup_api_clients, allow_reinit=False)
        except Exception as e:
            print(f"[Apple Music Error] Initial initialization failed: {e}")
            # We'll try again during the first actual operation

        except Exception as e:
            # Check for SSL certificate errors
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
                else:
                    raise self.exception(
                        f"SSL Certificate Error detected!\n\n"
                        f"Try updating certificates with:\n"
                        f"pip3 install --upgrade certifi\n\n"
                        f"Original error: {e}"
                    )
            else:
                if self._debug:
                    print(f"[Apple Music Error] API Setup failed: {e}")
                raise self.exception(f"Failed to initialize Apple Music API: {e}")

    def _start_background_loop(self):
        """Start or restart the background event loop thread."""
        with self._lock:
            if self.loop_thread and self.loop_thread.is_alive() and self.loop and not self.loop.is_closed():
                if self._debug:
                    print(f"[Apple Music Debug] Background loop already healthy (Loop ID: {id(self.loop)})")
                return
            
            # Clear caches whenever the loop starts or restarts to avoid "different event loop" errors
            self._clear_gamdl_caches()
            
            # Null out API clients and interfaces to force re-initialization in the new loop
            self.apple_music_api = None
            self.itunes_api = None
            self.gamdl_interface = None
            self.gamdl_song_interface = None
            self.gamdl_music_video_interface = None
            self.gamdl_uploaded_video_interface = None
            
            self._wrapper_offline = False 
            
            if self._debug:
                print(f"[Apple Music Debug] Starting background event loop thread...")
                
            self._loop_ready.clear()
            self.loop_thread = threading.Thread(target=self._run_loop, daemon=True)
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
            if self._debug:
                print(f"[Apple Music Debug] Background loop created and set. Loop id: {id(self.loop)}")
            self._loop_ready.set()
            self.loop.run_forever()
        except Exception as e:
            print(f"[Apple Music Error] Background loop thread crashed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self._debug:
                print("[Apple Music Debug] Background loop thread exiting...")
            self._loop_ready.clear()
            # Don't close it here if we want it to persist even if run_forever returns (which it shouldn't)

    def _run_async(self, func, *args, **kwargs):
        """Run an async function or lambda in the internal event loop thread and return the result."""
        allow_reinit = kwargs.pop('allow_reinit', True)
        
        # Detect if we're already running in the background thread to avoid deadlocks
        if threading.current_thread() == self.loop_thread:
            # We are already in the background loop thread. 
            # We cannot call future.result() here as it would block the loop.
            # We must call the function directly.
            if asyncio.iscoroutinefunction(func):
                # Nested async calls from within the loop thread should be awaited directly.
                # Since _run_async is sync, this is a bit of a hack, but we'll try to run it.
                if self._debug:
                    print("[Apple Music Warning] Nested async _run_async call detected! Attempting to run in current loop...")
                # Note: This will only work if called from a context that can block or if we use run_coroutine_threadsafe correctly.
                # However, for our self-healing logic, we should try to avoid this.
                return asyncio.run_coroutine_threadsafe(func(*args, **kwargs), self.loop).result()
            else:
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
                    if self._debug:
                        print("[Apple Music Debug] Re-establishing API clients for operation...")
                    await self._setup_api_clients()

                am_api = getattr(self, 'apple_music_api', None)
                it_api = getattr(self, 'itunes_api', None)
                
                sf = target_sf or getattr(am_api, 'storefront', None) or getattr(self, 'account_storefront', 'us')
                
                try:
                    # Update storefront if different
                    if am_api and sf and getattr(am_api, 'storefront', None) != sf:
                        am_api.storefront = sf
                    if it_api and sf and getattr(am_api, 'storefront', None) != sf:
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
                if self._debug:
                    print(f"[Apple Music Debug] Scheduling coroutine on loop {id(self.loop)} (Thread: {self.loop_thread.name if self.loop_thread else 'None'})")
                
                future = asyncio.run_coroutine_threadsafe(wrapper(), self.loop)
                # Significantly increase timeout to 1200s (20 mins) to support extremely heavy operations if needed
                result = future.result(timeout=1200) 
                
                # 3. Handle propagated exceptions
                if isinstance(result, Exception):
                    # Handle Apple Music API Rate Limits (429)
                    is_rate_limit = False
                    # Check specifically for status code 429 if it's an ApiError (from gamdl)
                    if 'ApiError' in type(result).__name__ and getattr(result, 'status_code', None) == 429:
                        is_rate_limit = True
                    # Fallback check for other error types that might indicate rate limiting
                    elif 'TooManyRequests' in type(result).__name__:
                        is_rate_limit = True

                    if is_rate_limit and attempt < 3:
                        backoff_times = [2, 5, 10]
                        wait_time = backoff_times[attempt]
                        if self._debug:
                            print(f"[Apple Music Warning] Rate limit (429) detected. Retrying in {wait_time}s... (Attempt {attempt+1}/4)")
                        time.sleep(wait_time)
                        continue

                    result_str = str(result)
                    if "Multiple cookies exist with name=" in result_str and attempt < 3:
                        cookie_name = result_str.split("name=")[-1].strip().strip("'\"")
                        if self._debug:
                            print(f"[Apple Music Warning] Cookie conflict detected for '{cookie_name}'. Attempting self-healing...")
                        try:
                            am_api = getattr(self, 'apple_music_api', None)
                            if am_api and hasattr(am_api, 'client') and hasattr(am_api.client, 'cookies'):
                                jar = am_api.client.cookies.jar
                                cookies_to_remove = [c for c in jar if c.name == cookie_name]
                                for c in cookies_to_remove:
                                    try:
                                        jar.clear(c.domain, c.path, c.name)
                                    except: pass
                                if self._debug:
                                    print(f"[Apple Music Debug] Cleared {len(cookies_to_remove)} conflicting '{cookie_name}' cookies.")
                        except Exception as ce:
                            if self._debug:
                                print(f"[Apple Music Error] Failed to clear conflicting cookies: {ce}")
                        continue

                    if "closed" in result_str.lower() and isinstance(result, RuntimeError):
                        if self._debug:
                            print(f"[Apple Music Warning] background thread returned closed loop error: {result}")
                        self.loop = None
                        self.loop_thread = None
                        continue
                    raise result
                return result
                
            except (RuntimeError, TimeoutError, concurrent.futures.TimeoutError, AttributeError) as e:
                # If it's an AttributeError involving 'send', it's likely a dead transport on a closed loop
                is_dead_transport = isinstance(e, AttributeError) and ('send' in str(e) or 'recv' in str(e))
                
                if self._debug:
                    print(f"[Apple Music Debug] _run_async caught {type(e).__name__}: {e}")
                
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
        # Clear OLD instances if they exist
        interfaces = [
            getattr(self, 'gamdl_interface', None),
            getattr(self, 'gamdl_song_interface', None),
            getattr(self, 'gamdl_music_video_interface', None),
            getattr(self, 'gamdl_uploaded_video_interface', None)
        ]
        
        # Null out interfaces on self to force recreation with new APIs
        self.gamdl_interface = None
        self.gamdl_song_interface = None
        self.gamdl_music_video_interface = None
        self.gamdl_uploaded_video_interface = None
        
        # ALSO clear the classes directly because alru_cache on methods shares the cache across all instances
        # and it's easier to reach the internal _LRUCacheWrapper via the class attribute.
        interface_classes = []
        if GAMDL_AVAILABLE:
            try:
                from gamdl.interface.interface import AppleMusicInterface
                from gamdl.interface.interface_song import AppleMusicSongInterface
                from gamdl.interface.interface_music_video import AppleMusicMusicVideoInterface
                interface_classes = [AppleMusicInterface, AppleMusicSongInterface, AppleMusicMusicVideoInterface]
            except:
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
        """Temporarily sets the storefront for API calls if a country code is provided."""
        if not country_code:
            return

        country_code_lower = country_code.lower()
        if self.apple_music_api and self.apple_music_api.storefront != country_code_lower:
            if self._debug:
                print(f"[Apple Music Debug] Switching storefront from {self.apple_music_api.storefront} to {country_code_lower}")
            
            # Update storefront for both Apple Music and iTunes APIs
            self.apple_music_api.storefront = country_code_lower
            if self.itunes_api:
                self.itunes_api.storefront = country_code_lower

    def _get_gamdl_codec(self, codec_str: str):
        """Map codec string to gamdl SongCodec enum"""
        if not codec_str:
            return GamdlSongCodec.AAC_LEGACY
            
        codec_lower = codec_str.lower()
        if codec_lower == 'aac':
            return GamdlSongCodec.AAC_LEGACY
        elif codec_lower == 'alac' or 'alac-' in codec_lower:
            return GamdlSongCodec.ALAC
        elif codec_lower == 'atmos':
            return GamdlSongCodec.ATMOS
        else:
            return GamdlSongCodec.AAC_LEGACY

    def _quality_to_codec(self, quality_tier: QualityEnum):
        """Map OrpheusDL QualityEnum to gamdl SongCodec enum"""
        if not quality_tier:
            return None
            
        if quality_tier & QualityEnum.ATMOS:
            return GamdlSongCodec.ATMOS
        elif quality_tier & (QualityEnum.LOSSLESS | QualityEnum.HIFI):
            return GamdlSongCodec.ALAC
        elif quality_tier & QualityEnum.MINIMUM:
            return GamdlSongCodec.AAC_LEGACY
        else:
            # LOW, MEDIUM, HIGH all map to standard AAC 256
            return GamdlSongCodec.AAC_LEGACY

    def _is_ssl_certificate_error(self, exception):
        """Check if an exception is related to SSL certificate verification"""
        error_str = str(exception).lower()
        ssl_error_indicators = [
            "certificate verify failed",
            "ssl: certificate_verify_failed",
            "unable to get local issuer certificate",
            "certificate_verify_failed",
            "ssl certificate problem"
        ]
        return any(indicator in error_str for indicator in ssl_error_indicators)

    def _initialize_gamdl_components(self, song_codec=None, use_wrapper=None, force=False):
        # Clear any cached results in gamdl interfaces BEFORE replacing them.
        # This is CRITICAL because alru_cache shares the cache across instances,
        # so we must clear the existing stale state (bound to the old loop)
        # before we start new operations on them or replace them.
        self._clear_gamdl_caches()

        requested_codec = song_codec if song_codec is not None else self.song_codec
        requested_wrapper = use_wrapper if use_wrapper is not None else self.use_wrapper

        # Check if we need to re-initialize due to different settings or loop change
        needs_reinit = force
        if not needs_reinit and self.gamdl_downloader:
            if self.gamdl_base_downloader.use_wrapper != requested_wrapper:
                needs_reinit = True
            elif hasattr(self.gamdl_song_downloader, 'codec_priority') and self.gamdl_song_downloader.codec_priority != [requested_codec]:
                needs_reinit = True
            elif self.song_codec != requested_codec: # Also check module-level cached codec
                needs_reinit = True

        if not self.gamdl_downloader or needs_reinit:
            if self._debug:
                print(f"[Apple Music Debug] Initializing gamdl components (force={force})...")
            try:
                orpheus_temp_path = Path(self.settings.get("temp_path", tempfile.gettempdir()))
                
                # Setup gamdl base downloader
                self.gamdl_base_downloader = AppleMusicBaseDownloader(
                    output_path=str(orpheus_temp_path / "gamdl_out"),
                    temp_path=str(orpheus_temp_path / "gamdl_temp"),
                    ffmpeg_path=self.binary_paths.get('ffmpeg', 'ffmpeg'),
                    mp4box_path=self.binary_paths.get('mp4box', 'MP4Box'),
                    mp4decrypt_path=self.binary_paths.get('mp4decrypt', 'mp4decrypt'),
                    nm3u8dlre_path=self.binary_paths.get('nm3u8dlre', 'N_m3u8DL-RE'),
                    use_wrapper=requested_wrapper,
                    wrapper_decrypt_ip=self.settings.get('wrapper_decrypt_ip', '127.0.0.1:10020'),
                    overwrite=True,
                    download_mode=self.settings.get('download_mode', GamdlDownloadMode.YTDLP),
                    silent=not self._debug
                )
                
                # Setup gamdl interfaces
                self.gamdl_interface = AppleMusicInterface(self.apple_music_api, self.itunes_api)
                # Use our customized subclass to handle quality-aware ALAC selection
                self.gamdl_song_interface = OrpheusAppleMusicSongInterface(
                    self.gamdl_interface, 
                    quality_tier=self.quality_tier,
                    debug=self._debug
                )
                
                # Setup sub-downloaders
                self.gamdl_song_downloader = AppleMusicSongDownloader(
                    base_downloader=self.gamdl_base_downloader,
                    interface=self.gamdl_song_interface,
                    codec_priority=[requested_codec]
                )
                
                # Setup main gamdl downloader
                self.gamdl_downloader = AppleMusicDownloader(
                    interface=self.gamdl_interface,
                    base_downloader=self.gamdl_base_downloader,
                    song_downloader=self.gamdl_song_downloader,
                    artist_auto_select=None # Can be extended later if we add a setting
                )
                
                # Alias for backward compatibility in some methods
                self.gamdl_downloader_song = self.gamdl_song_downloader
                
                if self._debug:
                    print("[Apple Music Debug] gamdl_downloader components initialized successfully.")
            except Exception as e:
                print(f"[Apple Music Error] Failed to initialize gamdl components: {e}")
                import traceback
                if self._debug:
                    print(traceback.format_exc())
                self.gamdl_downloader = None
                self.gamdl_downloader_song = None

    def custom_url_parse(self, link):
        """Parse Apple Music URLs and determine media type and ID"""
        try:
            # Parse Apple Music URL
            url_info = self._parse_apple_music_url(link)
            
            # Map types to OrpheusDL types
            type_mapping = {
                'song': DownloadTypeEnum.track,
                'album': DownloadTypeEnum.album,
                'playlist': DownloadTypeEnum.playlist,
                'artist': DownloadTypeEnum.artist,
                'music-video': DownloadTypeEnum.track
            }
            
            media_type = type_mapping.get(url_info['type'], DownloadTypeEnum.track)
            
            # Priority: If authenticated, use account storefront. If not, use URL storefront.
            storefront = url_info['country']
            if self.is_authenticated and self.account_storefront:
                if self._debug:
                    print(f"[Apple Music Debug] Authenticated: Overriding URL storefront '{storefront}' with account storefront '{self.account_storefront}'")
                storefront = self.account_storefront
            
            return MediaIdentification(
                media_type=media_type,
                media_id=url_info['id'],
                extra_kwargs={'country': storefront}
            )
            
        except Exception as e:
            raise self.exception(f"Failed to parse Apple Music URL: {e}")

    def _parse_apple_music_url(self, url):
        """Parse Apple Music URL to extract type and ID"""
        from urllib.parse import urlparse, parse_qs
        
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split('/') if part]
        
        if len(path_parts) < 3:
            raise ValueError("Invalid Apple Music URL format")
        
        # Extract country, type, and name from URL
        country = path_parts[0]  # e.g., 'us'
        media_type = path_parts[1]  # e.g., 'song', 'album', 'playlist'
        
        # Check for song ID in query parameter first
        query_params = parse_qs(parsed.query)
        if 'i' in query_params and query_params['i']:
            media_id = query_params['i'][0]
            media_type = 'song'  # If 'i' is present, it's a song
        else:
            # For URLs with format: /country/type/name/id or /country/type/name-id
            # Check if we have 4+ parts (separate name and ID)
            if len(path_parts) >= 4:
                # ID is the last part
                potential_id = path_parts[-1]
                
                # Check if it's a playlist ID (pl.xxxxx format)
                if potential_id.startswith('pl.'):
                    media_id = potential_id
                # Check if it's a numeric ID
                elif potential_id.isdigit():
                    media_id = potential_id
                else:
                    raise ValueError(f"Could not parse ID from last path part: {potential_id}")
            else:
                # Fallback to existing path-based extraction for older URL formats
                name_and_id = path_parts[2]  # e.g., 'song-name/1234567890'
                
                # Extract ID from the end of the URL
                id_match = re.search(r'/(\d+)(?:\?|$)', url)
                if not id_match:
                    # Try to get ID from the last part if no slash
                    id_match = re.search(r'(\d+)(?:\?|$)', name_and_id)
                
                # If no numeric ID found, check for playlist format (pl.xxxxx)
                if not id_match:
                    pl_match = re.search(r'/(pl\.[a-f0-9]+)(?:\?|$)', url)
                    if not pl_match:
                        pl_match = re.search(r'(pl\.[a-f0-9]+)(?:\?|$)', name_and_id)
                    if pl_match:
                        media_id = pl_match.group(1)
                    else:
                        raise ValueError("Could not extract ID from Apple Music URL")
                else:
                    media_id = id_match.group(1)
        
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
            results = self._run_async(lambda s: s.apple_music_api.get_search_results(query, types=search_type, limit=limit))
            
            # Map 'results' structure to what the rest of the method expects
            if 'results' in results:
                results = results['results']
            
            search_results = []
            if search_type in results:
                for item in results[search_type]['data']:
                    attrs = item.get('attributes', {})
                    
                    # Extract artist information
                    artists = []
                    if query_type == DownloadTypeEnum.artist:
                        artists = [attrs.get('name', '')]
                    elif 'artistName' in attrs:
                        artists = [attrs['artistName']]
                    elif 'curatorName' in attrs:  # For playlists
                        artists = [attrs['curatorName']]
                    
                    # Calculate duration for tracks
                    duration = None
                    if 'durationInMillis' in attrs:
                        duration = attrs['durationInMillis'] // 1000
                    
                    # Get additional info (Tracks first, then content rating, then audio traits)
                    additional = []
                    if 'trackCount' in attrs:
                        tc = attrs['trackCount']; additional.append(f"1 track" if tc == 1 else f"{tc} tracks")
                    # Only hide playlists that explicitly have 0 tracks; show playlists when trackCount is missing (API may omit it)
                    if query_type == DownloadTypeEnum.playlist and attrs.get('trackCount') == 0:
                        continue

                        
                    formatted_traits = self._format_audio_traits(attrs, item_type=item.get('type'))
                    if formatted_traits:
                        additional.append(formatted_traits)
                    
                    # Extract cover URL from artwork template (small size for search results)
                    cover_url = None
                    artwork = attrs.get('artwork', {})
                    if artwork and artwork.get('url'):
                        # Use 56x56 for search result thumbnails
                        cover_url = artwork['url'].replace('{w}', '56').replace('{h}', '56')
                    
                    # Extract preview URL (Apple Music provides 30-second previews)
                    preview_url = None
                    previews = attrs.get('previews', [])
                    if previews and len(previews) > 0:
                        preview_url = previews[0].get('url')
                    
                    # For playlists, use lastModifiedDate for year when releaseDate is not set
                    year_val = self._extract_year(attrs.get('releaseDate'))
                    if year_val is None and query_type == DownloadTypeEnum.playlist:
                        year_val = self._extract_year(attrs.get('lastModifiedDate'))
                    if 'url' in attrs:
                        attrs['url'] = self._localize_url(attrs['url'])
                    
                    search_results.append(SearchResult(
                        result_id=item['id'],
                        name=attrs.get('name', ''),
                        artists=artists,
                        duration=duration,
                        year=year_val,
                        explicit=attrs.get('contentRating') == 'explicit',
                        additional=additional,
                        image_url=cover_url,
                        preview_url=preview_url,
                        extra_kwargs={'raw_result': item}
                    ))
            
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
        Require valid cookies (authenticated session) before download/metadata.
        Without this, we would return 'Not Authenticated' and fail later. Matches
        Spotify/Qobuz/Deezer: show what's missing and where to fill it in.
        """
        self._check_and_reload_cookies()
        if self.is_authenticated and self.apple_music_api:
            return
        cookies_path_str = self.settings.get('cookies_path', './config/cookies.txt')
        cookies_path = Path(cookies_path_str) if cookies_path_str else None
        if not cookies_path or not cookies_path.exists():
            default_cookies = Path('./config/cookies.txt')
            if default_cookies.exists():
                cookies_path = default_cookies
        error_msg = (
            'Apple Music credentials are missing in settings.json. '
            'Please add a cookies.txt file (Netscape format) at the path set in cookies_path (e.g. config/cookies.txt), '
            'or set cookies_path in the OrpheusDL GUI Settings tab (Apple Music) or edit config/settings.json directly.'
        )
        raise self.exception(error_msg)

    async def _setup_api_clients(self):
        """
        Initialize or re-initialize AppleMusicApi and ItunesApi based on current settings.
        This is extracted to be callable from both __init__ and _run_async for self-healing.
        This MUST be called within the background loop thread.
        """
        cookies_path_str = self.settings.get('cookies_path', './config/cookies.txt')
        cookies_path = Path(cookies_path_str) if cookies_path_str else None
        if cookies_path and not cookies_path.exists():
            default_cookies = Path('./config/cookies.txt')
            if default_cookies.exists():
                cookies_path = default_cookies
        
        with suppress_gamdl_debug():
            self.use_wrapper = self.settings.get('use_wrapper', False)
            language = self.settings.get('language', 'en-US')

            try:
                if self.use_wrapper:
                    wrapper_account_url = self.settings.get('wrapper_account_url', "http://127.0.0.1:20020")
                    try:
                        if getattr(self, '_wrapper_offline', False):
                            raise Exception("Wrapper offline")
                        
                        self.apple_music_api = await AppleMusicApi.create_from_wrapper(
                            wrapper_account_url=wrapper_account_url, language=language
                        )
                    except Exception:
                        self._wrapper_offline = True
                        kwargs = {'language': language}
                        if cookies_path and cookies_path.exists():
                            kwargs['cookies_path'] = str(cookies_path)
                            self.apple_music_api = await AppleMusicApi.create_from_netscape_cookies(**kwargs)
                        else:
                            self.apple_music_api = await AppleMusicApi.create(**kwargs)
                else:
                    kwargs = {'language': language}
                    if cookies_path and cookies_path.exists():
                        kwargs['cookies_path'] = str(cookies_path)
                        self.apple_music_api = await AppleMusicApi.create_from_netscape_cookies(**kwargs)
                    else:
                        self.apple_music_api = await AppleMusicApi.create(**kwargs)
                
                if self.apple_music_api:
                    self.itunes_api = ItunesApi(self.apple_music_api.storefront)
                    self.account_storefront = self.apple_music_api.storefront
                    self.is_authenticated = self.apple_music_api.active_subscription
                
                # Ensure other components are resolved
                self._resolve_all_binary_paths()
                self.song_codec = self._get_gamdl_codec(self.settings.get('codec', 'aac'))
                
            except Exception as e:
                if self._debug:
                    print(f"[Apple Music Error] API Setup failed: {e}")
                raise

    def _check_and_reload_cookies(self):
        """
        Checks if cookies.txt exists and reloads the session if needed.
        This allows the user to add cookies.txt while the app is running.
        """
        # Get configured path
        cookies_path_str = self.settings.get('cookies_path', './config/cookies.txt')
        cookies_path = Path(cookies_path_str) if cookies_path_str else None
        
        # If configured path doesn't exist, try default
        if not cookies_path or not cookies_path.exists():
            default_cookies = Path('./config/cookies.txt')
            if default_cookies.exists():
                cookies_path = default_cookies
        
        # If we found a cookies file
        if cookies_path and cookies_path.exists():
            if self.apple_music_api:
                try:
                    if self._debug:
                        print(f"[Apple Music Debug] Reloading cookies from {cookies_path}...")
                    
                    with self._lock:
                        # Re-initialize the API with new cookies using the background loop
                        self.apple_music_api = self._run_async(lambda s: AppleMusicApi.create_from_netscape_cookies(
                            cookies_path=str(cookies_path),
                            language=self.settings.get('language', 'en-US')
                        ))
                    
                    self.is_authenticated = self.apple_music_api.active_subscription
                    if self.is_authenticated:
                        if self._debug:
                            print("[Apple Music Debug] Successfully authenticated after reloading cookies.")
                    else:
                        if self._debug:
                            print("[Apple Music Warning] Reloaded cookies but active subscription still missing.")
                except Exception as e:
                    if self._debug:
                        print(f"[Apple Music Error] Failed to reload cookies: {e}")

    def _get_equivalent_track_id(self, isrc: str, target_storefront: str, title: str = None, artist: str = None) -> Optional[str]:
        """
        Search for a track by ISRC (or Title/Artist) in the target storefront and return its ID.
        Useful when the original track ID is from a different region and 404s in the user's region.
        """
        if not target_storefront:
            return None
            
        if self._debug:
            print(f"[Apple Music Debug] Searching for equivalent track in storefront '{target_storefront}'...")
            
        current_storefront = self.apple_music_api.storefront
        # Set storefront BEFORE _run_async so it's captured by the preservation logic
        self.apple_music_api.storefront = target_storefront
        
        try:
            # 1. Search by ISRC if available
            if isrc:
                if self._debug:
                    print(f"[Apple Music Debug] Trying ISRC search: {isrc}")
                results = self._run_async(lambda s: s.apple_music_api.get_search_results(term=isrc, types="songs", limit=5), storefront=target_storefront)
                
                if results and 'results' in results and 'songs' in results['results']:
                    songs = results['results']['songs'].get('data', [])
                    for song in songs:
                        song_isrc = song.get('attributes', {}).get('isrc')
                        if song_isrc and song_isrc.lower() == isrc.lower():
                            new_id = song.get('id')
                            if self._debug:
                                print(f"[Apple Music Debug] Found equivalent track ID {new_id} via ISRC {isrc}")
                            return new_id

            # 2. Fallback to Search by Title and Artist if ISRC failed or wasn't provided
            if title and artist:
                query = f"{title} {artist}"
                if self._debug:
                    print(f"[Apple Music Debug] Trying semantic search: {query}")
                results = self._run_async(lambda s: s.apple_music_api.get_search_results(term=query, types="songs", limit=10), storefront=target_storefront)
                
                if results and 'results' in results and 'songs' in results['results']:
                    songs = results['results']['songs'].get('data', [])
                    
                    # Pass 1: Look for exact title match
                    for song in songs:
                        attrs = song.get('attributes', {})
                        if title.lower() == attrs.get('name', '').lower() and \
                           (artist.lower() in attrs.get('artistName', '').lower() or any(artist.lower() in a.lower() for a in attrs.get('artistNames', []))):
                            new_id = song.get('id')
                            if self._debug:
                                print(f"[Apple Music Debug] Found equivalent track ID {new_id} via exact semantic search")
                            return new_id
                            
                    # Pass 2: Look for fuzzy title match
                    for song in songs:
                        attrs = song.get('attributes', {})
                        # If original title doesn't have remix/version but result does, skip it to avoid getting remixes
                        result_name = attrs.get('name', '').lower()
                        original_clean = "remix" not in title.lower() and "version" not in title.lower()
                        if original_clean and ("remix" in result_name or "version" in result_name):
                            continue
                            
                        if title.lower() in result_name and \
                           (artist.lower() in attrs.get('artistName', '').lower() or any(artist.lower() in a.lower() for a in attrs.get('artistNames', []))):
                            new_id = song.get('id')
                            if self._debug:
                                print(f"[Apple Music Debug] Found equivalent track ID {new_id} via fuzzy semantic search")
                            return new_id
                        
            if self._debug:
                print(f"[Apple Music Debug] No equivalent track found in {target_storefront}")
            return None
            
        except Exception as e:
            if self._debug:
                print(f"[Apple Music Debug] Error searching for equivalent track: {e}")
            return None
        finally:
            self.apple_music_api.storefront = current_storefront

    def get_track_info(self, track_id: str, quality_tier: QualityEnum, codec_options: CodecOptions, data: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[TrackInfo]:
        if getattr(self, '_debug', False):
            print(f"[{self.module_information.service_name} DEBUG] get_track_info called for track_id: {track_id}")
            print(f"[{self.module_information.service_name} DEBUG] kwargs keys: {list(kwargs.keys())}")
            print(f"[{self.module_information.service_name} DEBUG] country in kwargs: {kwargs.get('country')}")
        
        # Re-evaluate settings from config to ensure we catch changes from the GUI
        self.song_codec = self._get_gamdl_codec(self.settings.get('codec', 'aac'))
        self.use_wrapper = self.settings.get('use_wrapper', False)
        
        # allow_refetch=True will trigger a full API fetch if essential IDs are missing.
        # We default to True now to ensure complete metadata during downloads, 
        # but Orpheus search/listing usually passes 'data' which we use first.
        allow_refetch = kwargs.get('allow_refetch', True)
        
        # Handle case where track_id is passed as a dictionary (from album/playlist track lists)
        if isinstance(track_id, dict):
            if data is None:
                data = track_id
            track_id = str(data.get('id'))
            
        # Handle stringified dictionary if passed as string (e.g. from music_downloader.py)
        # This occurs when playlist track dicts are cast to strings somewhere in the pipeline
        elif isinstance(track_id, str) and (track_id.strip().startswith('{') or "%7B" in track_id):
            import ast
            import urllib.parse
            try:
                # Try to clean up URL encoding if present
                clean_id = track_id
                if "%7B" in clean_id:
                    clean_id = urllib.parse.unquote(clean_id)
                
                # Check again if it looks like a dict after decoding
                if clean_id.strip().startswith('{'):
                    try:
                        # Safely evaluate string as python dict
                        potential_data = ast.literal_eval(clean_id)
                        if isinstance(potential_data, dict) and 'id' in potential_data:
                            # Use the extracted ID
                            extracted_id = potential_data.get('id')
                            if extracted_id:
                                track_id = str(extracted_id)
                                # Also use the data if we don't have any
                                if data is None: 
                                    data = potential_data
                                if self._debug:
                                    print(f"[Apple Music Debug] Successfully parsed stringified dict ID: {track_id}")
                    except (ValueError, SyntaxError):
                        # Fallback: simple string extraction if eval fails
                        if "'id': '" in clean_id:
                            start = clean_id.find("'id': '") + 7
                            end = clean_id.find("'", start)
                            if start > 6 and end > start:
                                track_id = clean_id[start:end]
                                if self._debug:
                                    print(f"[Apple Music Debug] Extracted ID via string manipulation: {track_id}")
            except Exception as e:
                if self._debug:
                    print(f"[Apple Music Debug] Failed to parse stringified track_id: {e}")
                # Continue with original track_id if parsing fails, let API handle error
            
        if getattr(self, '_debug', False):
            print(f"[{self.module_information.service_name} DEBUG] kwargs keys: {list(kwargs.keys())}")
            
        self._ensure_credentials()

        try:
            # Ensure we have a valid country code for _set_storefront
            country = kwargs.get('country') or (data.get('country') if data else None)
            if getattr(self, '_debug', False):
                print(f"[{self.module_information.service_name} DEBUG] Extracted country: {country}")
            self._set_storefront(country)

            # Try to extract country if URL is provided
            country = None
            if 'url' in kwargs and kwargs['url']:
                country = self._parse_apple_music_url(kwargs['url']).get('country')
                if self._debug:
                    print(f"[Apple Music Debug] Parsed country '{country}' from URL: {kwargs['url']}")
            
            # Check if we have raw_result from search
            if 'raw_result' in kwargs and kwargs['raw_result']:
                track_api_data = kwargs['raw_result']
                if self._debug:
                    print(f"[Apple Music Debug] Using raw_result from search for track {track_id}")
            else:
                # Use data if provided (e.g., from album track list), otherwise fetch
                async def _fetch_with_logging(s, sid):
                    try:
                        return await s.apple_music_api.get_song(sid)
                    except Exception as fe:
                        if getattr(s, '_debug', False):
                            print(f"[Apple Music Debug] API fetch failed for {sid}: {fe}")
                        return None

                track_api_data = data if data and isinstance(data, dict) and data.get('id') == track_id and 'attributes' in data else self._run_async(lambda s: _fetch_with_logging(s, track_id), storefront=country)
                
                # Unwrap from 'data' list if present
                if track_api_data and 'data' in track_api_data and len(track_api_data['data']) > 0:
                    track_api_data = track_api_data['data'][0]
                
                # Fallback to account storefront if url-based storefront fails
                if (not track_api_data or 'attributes' not in track_api_data) and country and self.account_storefront.lower() != country.lower():
                    if self._debug:
                        print(f"[Apple Music Debug] Fetch failed for storefront '{country}'. Retrying with account storefront '{self.account_storefront}'...")
                    track_api_data = self._run_async(lambda s: _fetch_with_logging(s, track_id), storefront=self.account_storefront)
                    # Unwrap from 'data' list if present
                    if track_api_data and 'data' in track_api_data and len(track_api_data['data']) > 0:
                        track_api_data = track_api_data['data'][0]
                    
                # If still failed, try a "guest" fetch (without user token) for metadata
                if (not track_api_data or 'attributes' not in track_api_data):
                    if self._debug:
                        print(f"[Apple Music Debug] Initial fetch failed. Attempting guest fetch for metadata...")
                    
                    async def _fetch_guest(s, sid, st):
                        # Temporarily remove tokens to avoid storefront restrictions on metadata
                        original_headers = dict(s.apple_music_api.client.headers)
                        original_cookies = dict(s.apple_music_api.client.cookies)
                        if "media-user-token" in s.apple_music_api.client.cookies:
                            del s.apple_music_api.client.cookies["media-user-token"]
                        
                        # Set requested storefront
                        orig_st = s.apple_music_api.storefront
                        s.apple_music_api.storefront = st
                        
                        try:
                            return await s.apple_music_api.get_song(sid)
                        finally:
                            s.apple_music_api.storefront = orig_st
                            s.apple_music_api.client.headers.update(original_headers)
                            s.apple_music_api.client.cookies.update(original_cookies)
                    
                    track_api_data = self._run_async(lambda s: _fetch_guest(s, track_id, country or self.account_storefront), storefront=country or self.account_storefront)
                    # Unwrap from 'data' list if present
                    if track_api_data and 'data' in track_api_data and len(track_api_data['data']) > 0:
                        track_api_data = track_api_data['data'][0]
                
                # If everything else failed, try iTunes Search API (lookup)
                if (not track_api_data or 'attributes' not in track_api_data):
                    if self._debug:
                        print(f"[Apple Music Debug] Apple Music API failed. Trying iTunes Search API fallback...")
                    
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
                                        'genreNames': [itunes_track.get('primaryGenreName')],
                                        'trackNumber': itunes_track.get('trackNumber'),
                                        'discNumber': itunes_track.get('discNumber'),
                                        'contentRating': itunes_track.get('contentAdvisoryRating', '').lower()
                                    }
                                }
                        except Exception as ie:
                            if getattr(s, '_debug', False):
                                print(f"[Apple Music Debug] iTunes lookup failed: {ie}")
                        return None
                    
                    track_api_data = self._run_async(lambda s: _fetch_itunes(s, track_id), storefront=country)

            if not track_api_data or 'attributes' not in track_api_data:
                if self._debug:
                    print(f"[Apple Music Error] Could not fetch track data for {track_id} from AppleMusicApi.")
                return TrackInfo(name=f"Error: Fetch failed for {track_id}", error="API Fetch Failed", artists=["Unknown Artist"], album="", album_id=None, artist_id=None, duration=0, codec=CodecEnum.AAC, bitrate=0, sample_rate=0, release_year=None, cover_url=None, explicit=False, tags=Tags())

            attrs = track_api_data['attributes']
            
            # Helper to get IDs from relationships
            def get_ids(d):
                aid = None
                arid = None
                if d.get('relationships'):
                    if 'albums' in d['relationships']:
                        rel_data = d['relationships']['albums'].get('data')
                        if rel_data: aid = rel_data[0].get('id')
                    if 'artists' in d['relationships']:
                        rel_data = d['relationships']['artists'].get('data')
                        if rel_data: arid = rel_data[0].get('id')
                return aid, arid

            album_id_from_rels, artist_id_from_rels = get_ids(track_api_data)

            # --- Supplemental Metadata Fetch (if IDs or lyrics flags missing) ---
            # ONLY do this if explicitly allowed (e.g. during download) or if we have NO IDs at all
            if allow_refetch and (not album_id_from_rels or not artist_id_from_rels or 'hasLyrics' not in attrs or 'audioTraits' not in attrs):
                if self._debug:
                    print(f"[Apple Music Debug] Incomplete metadata (Album={album_id_from_rels}, Artist={artist_id_from_rels}, hasLyrics={'hasLyrics' in attrs}, audioTraits={'audioTraits' in attrs}) for track {track_id}. Fetching full song data.")
                full_track_data = self._run_async(lambda s: s.apple_music_api.get_song(track_id), storefront=country)
                
                if full_track_data and 'data' in full_track_data and len(full_track_data['data']) > 0:
                    track_api_data = full_track_data['data'][0]
                    attrs = track_api_data['attributes']
                    album_id_from_rels, artist_id_from_rels = get_ids(track_api_data)
                    if self._debug:
                        print(f"[Apple Music Debug] Metadata updated after full fetch.")

            # --- Storefront Mismatch / Equivalent Check ---
            actual_download_id = track_id
            user_storefront = getattr(self, 'account_storefront', None)
            api_storefront = country.lower() if country else (self.apple_music_api.storefront if self.apple_music_api else 'us')
            
            if self.is_authenticated and user_storefront and api_storefront and user_storefront.lower() != api_storefront.lower():
                track_isrc = attrs.get('isrc')
                name_for_search = attrs.get('name')
                artist_name_for_search = attrs.get('artistName')
                if track_isrc or (name_for_search and artist_name_for_search):
                    if self._debug:
                        print(f"[Apple Music Debug] ID {track_id} -> Storefronts: User={user_storefront}, API={api_storefront}. Result equivalent_id={equivalent_id}")
                    
                    if equivalent_id:
                        actual_download_id = equivalent_id
                        if self._debug:
                            print(f"[Apple Music Debug] Using equivalent ID {actual_download_id} for storefront {user_storefront}")
                            print(f"[Apple Music Debug] Found equivalent track {actual_download_id} in {user_storefront}. Fetching its metadata...")
                        
                        # Re-fetch metadata for the equivalent ID in the user's storefront to ensure downloader has working info
                        equiv_metadata = self._run_async(lambda s: s.apple_music_api.get_song(actual_download_id), storefront=user_storefront)
                        if equiv_metadata and 'data' in equiv_metadata and len(equiv_metadata['data']) > 0:
                            track_api_data = equiv_metadata['data'][0]
                            # Update local attrs for any later logic in this method
                            attrs = track_api_data['attributes']
                            album_id_from_rels, artist_id_from_rels = get_ids(track_api_data)
                            if self._debug:
                                print(f"[Apple Music Debug] Successfully fetched metadata for equivalent track {actual_download_id}")

            # --- Final Consolidated Metadata Extraction ---
            name = attrs.get('name', 'Unknown Track')
            album_name = attrs.get('albumName', 'Unknown Album')
            artist_name = attrs.get('artistName', 'Unknown Artist')
            artists_list = [artist_name] if artist_name else ["Unknown Artist"]
            
            duration_ms = attrs.get('durationInMillis')
            duration_sec = duration_ms // 1000 if duration_ms is not None else 0
            release_date_str = attrs.get('releaseDate')
            year = self._extract_year(release_date_str)
            explicit = attrs.get('contentRating') == 'explicit'
            
            # Codec selection (indicative)
            override_song_codec = kwargs.get('song_codec')
            effective_codec = self._quality_to_codec(quality_tier) if quality_tier else (self._get_gamdl_codec(override_song_codec) if override_song_codec else self.song_codec)
            
            if self._debug:
                print(f"[{self.module_information.service_name} DEBUG] info effective_codec: {effective_codec.name if hasattr(effective_codec, 'name') else str(effective_codec)}")
            
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
                    # Fallback to AAC if requested quality is not available
                    if self._debug:
                        print(f"[Apple Music Debug] Requested {effective_codec.name} but track traits {traits} do not support it. Falling back to AAC.")
                    display_codec = CodecEnum.AAC
                    display_bitrate = 256
                    display_bit_depth = 16
                    display_sample_rate = 44100

            tags_obj = Tags(
                album_artist=attrs.get('albumArtistName', artist_name),
                track_number=attrs.get('trackNumber'),
                disc_number=attrs.get('discNumber'),
                release_date=release_date_str,
                genres=attrs.get('genreNames', []),
                isrc=attrs.get('isrc'),
                composer=attrs.get('composerName'),
            )

            cover_url = self._get_cover_url(attrs.get('artwork', {}).get('url'))

            download_extra_kwargs = {
                'track_id': actual_download_id,
                'api_response': track_api_data, 
                'quality_tier': quality_tier,
                'source_quality_tier': quality_tier.name if hasattr(quality_tier, 'name') else str(quality_tier),
                'original_id': track_id,
                'effective_storefront': user_storefront if actual_download_id != track_id else api_storefront
            }
            if override_song_codec: download_extra_kwargs['song_codec'] = override_song_codec
            if kwargs.get('use_wrapper') is not None: download_extra_kwargs['use_wrapper'] = kwargs.get('use_wrapper')

            return TrackInfo(
                name=name, album=album_name, album_id=str(album_id_from_rels) if album_id_from_rels else None,
                artists=artists_list, artist_id=str(artist_id_from_rels) if artist_id_from_rels else None,
                duration=duration_sec, codec=display_codec, bitrate=display_bitrate, bit_depth=display_bit_depth,
                sample_rate=display_sample_rate // 1000 if display_sample_rate else None, release_year=year,
                cover_url=cover_url, explicit=explicit, tags=tags_obj, id=actual_download_id,
                download_extra_kwargs=download_extra_kwargs
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
        if self._debug:
            print(f"[Apple Music Debug] get_track_download called for track_id: {track_id}")
            print(f"[Apple Music Debug] quality_tier: {quality_tier} (Type: {type(quality_tier)})")

        # Re-evaluate settings from config to ensure we catch changes from the GUI
        self.song_codec = self._get_gamdl_codec(self.settings.get('codec', 'aac'))
        self.use_wrapper = self.settings.get('use_wrapper', False)

        self._ensure_credentials()
        self._using_rich_tagging = False
        
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

        # Always log important selection info to GUI output when debug is on
        if self._debug:
            msg = f"[Apple Music Debug] Download: id={track_id}, tier={quality_tier}, codec={effective_codec.name if hasattr(effective_codec, 'name') else str(effective_codec)}"
            print(msg)
            if self.printer:
                self.printer.oprint(f"       Debug: {msg}", 0)

        if self._debug:
            print(f"[Apple Music Debug] download effective_codec: {effective_codec.name if hasattr(effective_codec, 'name') else str(effective_codec)}")
        # Detect context for indentation
        import inspect
        indent_spaces = "        " 
        try:
            is_album_context = False
            if 'extra_kwargs' in kwargs and kwargs['extra_kwargs']:
                extra_kwargs = kwargs['extra_kwargs']
                if 'album_id' in extra_kwargs or 'album_name' in extra_kwargs:
                    is_album_context = True

            if not is_album_context:
                stack = inspect.stack()
                for frame_info in stack:
                    if frame_info.function in ['download_album', 'download_playlist']:
                        is_album_context = True
                        break
        except:
            pass

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
                with suppress_gamdl_debug():
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
                if self._debug:
                    print(f"[Apple Music Debug] Downgrading codec to AAC as ALAC is unavailable for this track (Traits: {traits})")
                local_effective_codec = GamdlSongCodec.AAC_LEGACY
            elif local_effective_codec == GamdlSongCodec.ATMOS and not ('atmos' in traits or 'spatial' in traits):
                if 'lossless' in traits or 'hi-res-lossless' in traits:
                    if self._debug:
                        print(f"[Apple Music Debug] Downgrading codec to ALAC as ATMOS is unavailable for this track (Traits: {traits})")
                    local_effective_codec = GamdlSongCodec.ALAC
                else:
                    if self._debug:
                        print(f"[Apple Music Debug] Downgrading codec to AAC as ATMOS and ALAC are unavailable for this track (Traits: {traits})")
                    local_effective_codec = GamdlSongCodec.AAC_LEGACY

            # 3. Ensure gamdl components are initialized, passing overrides if present
            self._initialize_gamdl_components(song_codec=local_effective_codec, use_wrapper=override_use_wrapper)
            
            # Update quality_tier on our custom interface before each download
            if hasattr(self.gamdl_song_interface, 'quality_tier'):
                self.gamdl_song_interface.quality_tier = quality_tier

            if not self.gamdl_downloader_song or not self.gamdl_downloader:
                raise DownloadError("Apple Music: gamdl components could not be initialized.")
            
            # Sanitize song_data: Ensure relationships is a dict, not None, to avoid TypeError in gamdl/tagging
            if song_data and song_data.get('relationships') is None:
                song_data['relationships'] = {}
            
            # 2. Get download item
            if self._debug:
                print(f"[Apple Music Debug] Getting download item for track {song_data.get('id')}...")
            
            try:
                download_item = await self.gamdl_song_downloader.get_download_item(song_data)
            except StopIteration as si:
                if self._debug:
                    print(f"[Apple Music Error] StopIteration during get_download_item: {si}")
                    # Try to extract context from gamdl if possible (e.g., flavors)
                    try:
                        attrs = song_data.get('attributes', {})
                        ext_assets = attrs.get('extendedAssetUrls', {})
                        hls_url = ext_assets.get('enhancedHls')
                        print(f"[Apple Music Debug] Enhanced HLS URL present: {bool(hls_url)}")
                        if hls_url:
                            # Log available flavors to help debug StopIteration (usually codec mismatch)
                            try:
                                from modules.applemusic.gamdl.gamdl.utils import get_response
                                import m3u8
                                m3u8_master = m3u8.loads((await get_response(hls_url)).text)
                                flavors = [p['stream_info']['audio'] for p in m3u8_master.data.get('playlists', [])]
                                print(f"[Apple Music Debug] Available flavors in playlist: {flavors}")
                                print(f"[Apple Music Debug] Requested codec: {self.song_codec}")
                            except:
                                print("[Apple Music Debug] Could not fetch/parse HLS flavors for diagnostics.")
                    except:
                        pass
                raise DownloadError(f"Apple Music: Download failed - StopIteration: {si}. This often means the requested quality/flavor is unavailable for this track.") from si
            except Exception as e:
                if self._debug:
                    print(f"[Apple Music Error] Failed to get download item: {type(e).__name__}: {e}")
                raise DownloadError(f"Apple Music: Failed to prepare download - {type(e).__name__}: {e}") from e
            
            if download_item.error:
                if self._debug:
                    print(f"[Apple Music Error] download_item contains error: {download_item.error}")
                raise download_item.error

            # 4. Check for silent quality fallback (e.g. ALAC/Atmos requested but AAC returned)
            # Use local_effective_codec here as it reflects what we asked gamdl for
            requested_codec_val = local_effective_codec.value if hasattr(local_effective_codec, 'value') else str(local_effective_codec)
            
            # Extract stream info to check what we're actually getting
            stream_info = download_item.stream_info.audio_track if download_item.stream_info else None
            actual_codec_val = stream_info.codec if stream_info else None
            
            if self._debug:
                print(f"[Apple Music Debug] internal stream codec (actual_codec_val): {actual_codec_val}")
            
            if requested_codec_val in ['alac', 'atmos'] and (actual_codec_val is None or 'aac' in actual_codec_val):
                # Wrapper might be required but failed or unavailable
                wrapper_enabled = override_use_wrapper if override_use_wrapper is not None else getattr(self.gamdl_base_downloader, 'use_wrapper', False)
                if not wrapper_enabled:
                    raise DownloadError(f"Apple Music: {requested_codec_val.upper()} requires the 'Use Wrapper' setting to be enabled.")
                else:
                    raise DownloadError(f"Apple Music: Could not obtain {requested_codec_val.upper()} stream. Please ensure your Docker/Wrapper container is running and correctly configured.")

            # 5. Download and process
            codec_name = local_effective_codec.name if hasattr(local_effective_codec, 'name') else str(local_effective_codec)
            
            # Print accurate stream info if available
            stream_info = download_item.stream_info.audio_track if download_item.stream_info else None
            if stream_info and getattr(stream_info, 'width', None) and getattr(stream_info, 'height', None):
                # This is likely a video stream if it has width/height, but for audio we just print codec
                print(f"{indent_spaces}Detected Stream: {codec_name} ({stream_info.width}x{stream_info.height})")
            
            print(f"{indent_spaces}Downloading and processing {codec_name} track...")
            
            try:
                await self.gamdl_downloader.download(download_item)
                
                # Sanity check for extremely small files (e.g. 1.5MB for multi-minute ALAC)
                final_path = Path(download_item.final_path)
                if final_path.exists():
                    file_size = final_path.stat().st_size
                    duration_sec = 0
                    try:
                        attrs = download_item.media_metadata.get('attributes', {})
                        duration_ms = attrs.get('durationInMillis')
                        if duration_ms: duration_sec = duration_ms // 1000
                    except: pass
                    
                    # 1.5MB is ~50kbps for 4 mins. Even AAC 256 is ~8MB. ALAC is ~30MB+. 
                    # If it's less than 2MB and duration is significant, something is fundamentally wrong.
                    if requested_codec_val in ['alac', 'atmos'] and duration_sec > 30 and file_size < 2000000:
                         isrc = download_item.media_metadata.get('attributes', {}).get('isrc')
                         if isrc and not kwargs.get('_is_retry'):
                             if self._debug:
                                 print(f"[Apple Music Warning] Downloaded file is too small ({file_size} bytes). Likely a preview.")
                                 print(f"                     Attempting to find a better ID for ISRC {isrc} in {self.account_storefront}...")
                             
                             # Try to find the track again in our account storefront specifically
                             equiv_id = self._get_equivalent_track_id(isrc, self.account_storefront)
                             if equiv_id and equiv_id != track_id:
                                 if self._debug:
                                     print(f"[Apple Music Debug] Found different ID {equiv_id} for ISRC {isrc}. Retrying download...")
                                 # Cleanup the small file
                                 try: final_path.unlink()
                                 except: pass
                                 # Recursive call with retry flag
                                 new_kwargs = kwargs.copy()
                                 new_kwargs['_is_retry'] = True
                                 new_kwargs['api_response'] = None # Force fresh lookup
                                 return await self.get_track_download(equiv_id, quality_tier, codec_options, **new_kwargs)

                         if self._debug:
                             print(f"[Apple Music Error] Downloaded file is suspiciously small ({file_size} bytes for {duration_sec}s). Likely a preview.")
                             print(f"                   Storefront used: {getattr(self.apple_music_api, 'storefront', 'unknown')}")
                         raise DownloadError(f"Apple Music: The downloaded {requested_codec_val.upper()} file is corrupt or a preview (too small). This often happens if the track is region-locked or your IP region ({os.environ.get('GEO', 'unknown')}) doesn't match your account region.")
            except Exception as e:
                error_str = str(e)
                # Check for amdecrypt connection error (agent not running)
                conn_indicators = ["10020", "10061", "127.0.0.1", "connectionrefused", "refused", "geweigerd", "dial tcp"]
                if any(ind in error_str.lower() for ind in conn_indicators) or isinstance(e, ConnectionRefusedError):
                    raise DownloadError("Apple Music: Could not connect to the local decryption service. Please ensure your Docker/Wrapper container on (127.0.0.1:10020) is started and running.") from e
                
                if self._debug:
                    print(f"[Apple Music Error] gamdl download failed: {type(e).__name__}: {e}")
                raise DownloadError(f"Apple Music: Download execution failed - {type(e).__name__}: {e}") from e
            
            return download_item

        try:
            # Explicitly pass target storefront to ensure background loop worker sets it correctly
            target_st = kwargs.get('effective_storefront') or kwargs.get('country') or self.account_storefront
            if self._debug:
                print(f"[Apple Music Debug] Starting download async for {track_id} on storefront '{target_st}'")
                
            download_item = self._run_async(lambda s: _download_async(), storefront=target_st)
            
            # Set flag for rich tagging
            self._using_rich_tagging = True
            
            if self._debug:
                print(f"[Apple Music Success] Download completed: {download_item.final_path}")
                
            return TrackDownloadInfo(
                download_type=DownloadEnum.TEMP_FILE_PATH,
                temp_file_path=str(download_item.final_path)
            )

        except AuthenticationError:
            raise
        except TrackUnavailableError:
            raise
        except DownloadError:
            raise
        except Exception as e:
            error_str = str(e)
            
            if self._debug:
                print(f"[Apple Music Error] Final catch in get_track_download for {track_id}: {type(e).__name__}: {e}")
            
            # Check for generic amdecrypt connection error strings in cases where it wasn't caught earlier
            if "dial tcp" in error_str and ("10020" in error_str or "refused" in error_str.lower() or "geweigerd" in error_str.lower() or "127.0.0.1" in error_str):
                raise DownloadError("Apple Music: amdecrypt could not connect to the local decryption service (127.0.0.1:10020). Please ensure your decryption agent is running.") from e
            
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
            requested_codec_name = codec_name if 'codec_name' in locals() else (override_song_codec or self.song_codec)
            requested_codec_str = str(requested_codec_name.value if hasattr(requested_codec_name, 'value') else requested_codec_name).lower()
            
            conn_keywords = ["dial tcp", "refused", "geweigerd", "10020", "10061", "127.0.0.1", "connectionrefused"]
            if "FormatNotAvailable" in str(type(e)) or any(k in final_msg.lower() for k in conn_keywords) or "connectionrefused" in str(type(e)).lower():
                if requested_codec_str in ['atmos', 'alac']:
                    wrapper_enabled = override_use_wrapper if override_use_wrapper is not None else getattr(self.gamdl_base_downloader, 'use_wrapper', False)
                    if not wrapper_enabled:
                        final_msg = f"This {requested_codec_str.upper()} track requires the 'Use Wrapper' setting to be enabled in your Apple Music credentials."
                    else:
                        final_msg = f"Could not connect to the local decryption service. Please ensure your Docker/Wrapper container on (127.0.0.1:10020) is started and running."
                        
            raise DownloadError(f"Apple Music: Download failed - {final_msg}") from e

    def get_album_info(self, album_id: str, data: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[AlbumInfo]:
        """Get album information (catalog works without cookies; download requires credentials)."""
        try:
            # Extract country from kwargs/data and set storefront
            country = kwargs.get('country') or (data.get('country') if data else None) or (kwargs.get('data', {}).get('country') if isinstance(kwargs.get('data'), dict) else None)
            self._set_storefront(country)

            # Check if full album data was passed in kwargs (from get_artist_info) or data
            album_data = kwargs.get('data') or data
            
            # If data is a list (API response wrapper), unwrap it
            if isinstance(album_data, list) and len(album_data) > 0:
                album_data = album_data[0]
            elif isinstance(album_data, dict) and 'data' in album_data:
                album_data = album_data['data'][0]
                
            # If we don't have valid attributes, fetch from API
            if not album_data or not isinstance(album_data, dict) or 'attributes' not in album_data:
                if self._debug:
                    print(f"[Apple Music Debug] Fetching full album info for {album_id}")
                album_data = self._run_async(lambda s: s.apple_music_api.get_album(album_id), storefront=country)
                if album_data and 'data' in album_data:
                    album_data = album_data['data'][0]
            elif self._debug:
                print(f"[Apple Music Debug] Using provided album data for {album_id}")

            attrs = album_data['attributes']
            if 'url' in attrs:
                attrs['url'] = self._localize_url(attrs['url'])
                
            album_artist = attrs.get('artistName', '')
            cover_url = self._get_cover_url(attrs.get('artwork', {}).get('url'))
            release_year = self._extract_year(attrs.get('releaseDate'))
            # Use full track data from API when available to avoid N get_track_info calls in GUI
            tracks_out = []
            rel_tracks = (album_data.get('relationships') or {}).get('tracks', {}).get('data', [])
            for idx, track in enumerate(rel_tracks, start=1):
                t_attrs = track.get('attributes') or {}
                if t_attrs:
                    if 'url' in t_attrs:
                        t_attrs['url'] = self._localize_url(t_attrs['url'])
                    
                    name = t_attrs.get('name') or f'Track {idx}'
                    dur_ms = t_attrs.get('durationInMillis')
                    duration_sec = (dur_ms // 1000) if isinstance(dur_ms, (int, float)) else None
                    artist = t_attrs.get('artistName') or album_artist # Use track artist if available, else album artist
                    
                    additional = self._format_audio_traits(t_attrs, item_type='songs')

                    # Extract preview URL (Apple Music provides 30-second previews)
                    preview_url = None
                    previews = t_attrs.get('previews', [])
                    if previews and len(previews) > 0:
                        preview_url = previews[0].get('url')

                    tracks_out.append({
                        'id': track.get('id', ''),
                        'name': name,
                        'duration': duration_sec,
                        'artists': [artist],
                        'release_year': release_year,
                        'cover_url': cover_url,
                        'preview_url': preview_url,
                        # Pass full API data so get_track_info doesn't need to refetch
                        'attributes': t_attrs,
                        'relationships': track.get('relationships') or {},
                        'type': track.get('type'),
                        'additional': additional
                    })
                else:
                    tracks_out.append(track.get('id', ''))
            # Extract artist ID from relationships
            artist_id = ''
            if album_data.get('relationships') and 'artists' in album_data['relationships']:
                artist_rels = album_data['relationships']['artists'].get('data', [])
                if artist_rels:
                    artist_id = artist_rels[0].get('id', '')

            return AlbumInfo(
                name=attrs['name'],
                artist=album_artist,
                artist_id=str(artist_id) if artist_id else None,
                cover_url=cover_url,
                release_year=release_year,
                tracks=tracks_out,
                track_extra_kwargs={**kwargs, 'country': country}
            )
            
        except Exception as e:
            raise self.exception(f"Failed to get album info: {e}")

    def get_playlist_info(self, playlist_id, data: dict = None, **kwargs):
        """Get playlist information (catalog works without cookies; download requires credentials)."""
        try:
            # Extract country from kwargs and set storefront
            country = kwargs.get('country') or (data.get('country') if data else None)
            self._set_storefront(country)

            # Check if we have raw_result from search - use it for basic info but check for track relationships
            if 'raw_result' in kwargs and kwargs['raw_result']:
                playlist_data = kwargs['raw_result']
                if self._debug:
                    print(f"[Apple Music Debug] Using raw_result from search for playlist {playlist_id}")
                
                # Check if search result has track relationships - if not, fetch full data
                rels = playlist_data.get('relationships')
                if (not rels or 
                    'tracks' not in rels or 
                    not rels['tracks'].get('data')):
                    if self._debug:
                        print(f"[Apple Music Debug] Search result missing track data, fetching full playlist info...")
                    playlist_data = self._run_async(lambda s: s.apple_music_api.get_playlist(playlist_id), storefront=country)
            else:
                playlist_data = self._run_async(lambda s: s.apple_music_api.get_playlist(playlist_id), storefront=country)
            
            if playlist_data and 'data' in playlist_data:
                playlist_data = playlist_data['data'][0]
            
            attrs = playlist_data['attributes']
            if 'url' in attrs:
                attrs['url'] = self._localize_url(attrs['url'])
            
            cover_url = self._get_cover_url(attrs.get('artwork', {}).get('url'))
            release_year = self._extract_year(attrs.get('lastModifiedDate'))
            creator = attrs.get('curatorName', 'Unknown Creator')
            # Use full track data from API when available to avoid N get_track_info calls in GUI (same as album)
            tracks_out = []
            rel_tracks = (playlist_data.get('relationships') or {}).get('tracks', {}).get('data', [])
            for idx, track in enumerate(rel_tracks, start=1):
                t_attrs = track.get('attributes') or {}
                if t_attrs:
                    if 'url' in t_attrs:
                        t_attrs['url'] = self._localize_url(t_attrs['url'])
                    
                    name = t_attrs.get('name') or f'Track {idx}'
                    dur_ms = t_attrs.get('durationInMillis')
                    duration_sec = (dur_ms // 1000) if isinstance(dur_ms, (int, float)) else None
                    artist = t_attrs.get('artistName') or creator
                    
                    additional = self._format_audio_traits(t_attrs, item_type='songs')

                    # Extract preview URL
                    preview_url = None
                    previews = t_attrs.get('previews', [])
                    if previews and len(previews) > 0:
                        preview_url = previews[0].get('url')

                    tracks_out.append({
                        'id': track.get('id', ''),
                        'name': name,
                        'duration': duration_sec,
                        'artists': [artist],
                        'release_year': release_year,
                        'cover_url': cover_url,
                        'preview_url': preview_url,
                        # Pass full API data so get_track_info doesn't need to refetch
                        'attributes': t_attrs,
                        'relationships': track.get('relationships') or {},
                        'type': track.get('type'),
                        'additional': additional
                    })
                else:
                    tracks_out.append(track.get('id', ''))
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
        artist_data = self._run_async(lambda s: s.apple_music_api.get_artist(artist_id), storefront=country)
        if artist_data and 'data' in artist_data:
            artist_data = artist_data['data'][0]
        
        # Defensive check for API response structure. Expecting a dict.
        if not artist_data or not isinstance(artist_data, dict) or 'attributes' not in artist_data:
            if self._debug:
                print(f"[Apple Music Debug] Unexpected artist data response for ID {artist_id} on storefront '{self.apple_music_api.storefront}': {artist_data}")
            raise self.exception(f"No data returned for artist ID {artist_id}. They may not be available on the '{self.apple_music_api.storefront}' storefront.")

        attrs = artist_data['attributes']
        if 'url' in attrs:
            attrs['url'] = self._localize_url(attrs['url'])
            
        artist_name = attrs.get('name', 'Unknown Artist')
        cover_url_default = self._get_cover_url(attrs.get('artwork', {}).get('url'))
        
        albums_out = []
        tracks_out = []
        
        # Helper to process album data
        def process_album_item(album_item):
            a_attrs = album_item.get('attributes') or {}
            if a_attrs:
                if 'url' in a_attrs:
                    a_attrs['url'] = self._localize_url(a_attrs['url'])
                
                name = a_attrs.get('name') or 'Unknown Album'
                release_year = self._extract_year(a_attrs.get('releaseDate'))
                album_artist = a_attrs.get('artistName') or artist_name
                item_cover_url = self._get_cover_url(a_attrs.get('artwork', {}).get('url')) or cover_url_default
                
                additional_parts = []
                tc = a_attrs.get('trackCount')
                if tc is not None and tc > 0:
                    additional_parts.append("1 track" if tc == 1 else f"{tc} tracks")
                
                formatted_traits = self._format_audio_traits(a_attrs, item_type='albums')
                if formatted_traits:
                    additional_parts.append(formatted_traits)
                    
                additional = " · ".join(additional_parts)
                
                return {
                    'id': album_item.get('id', ''),
                    'name': name,
                    'artist': album_artist,
                    'release_year': release_year,
                    'cover_url': item_cover_url,
                    'additional': additional,
                    # Pass full API data so get_album_info doesn't need to refetch
                    'attributes': a_attrs,
                    'relationships': album_item.get('relationships'),
                    'type': album_item.get('type')
                }
            return album_item.get('id', '')

        # Process standard albums relationship
        rel_albums = (artist_data.get('relationships') or {}).get('albums', {}).get('data', [])
        for album in rel_albums:
            albums_out.append(process_album_item(album))
            
        # Process extended views if available (GAMDL 2.8.5+)
        views = artist_data.get('views', {})
        
        # Categorize other views as albums if they aren't 'top-songs'
        for view_name, view_data in views.items():
            view_items = view_data.get('data', [])
            if view_name == 'top-songs':
                for song_item in view_items:
                    s_attrs = song_item.get('attributes') or {}
                    if s_attrs:
                        if 'url' in s_attrs:
                            s_attrs['url'] = self._localize_url(s_attrs['url'])
                        
                        s_name = s_attrs.get('name') or 'Unknown Track'
                        s_artist = s_attrs.get('artistName') or artist_name
                        dur_ms = s_attrs.get('durationInMillis')
                        s_duration_sec = (dur_ms // 1000) if isinstance(dur_ms, (int, float)) else 0
                        s_cover_url = self._get_cover_url(s_attrs.get('artwork', {}).get('url')) or cover_url_default
                        
                        s_additional = self._format_audio_traits(s_attrs, item_type='songs')
                        
                        tracks_out.append({
                            'id': song_item.get('id', ''),
                            'name': s_name,
                            'artists': [s_artist],
                            'duration': s_duration_sec,
                            'release_year': self._extract_year(s_attrs.get('releaseDate')),
                            'cover_url': s_cover_url,
                            'additional': s_additional,
                            'attributes': s_attrs,
                            'relationships': song_item.get('relationships'),
                            'type': song_item.get('type')
                        })
            else:
                # Add a separator or label for different categories in the name if desired
                # For now, just add them all to albums_out as Orpheus expects
                category_prefix = ""
                if view_name == 'compilation-albums': category_prefix = "[Compilation] "
                elif view_name == 'live-albums': category_prefix = "[Live] "
                elif view_name == 'singles': category_prefix = "[Single/EP] "
                
                for album_item in view_items:
                    processed = process_album_item(album_item)
                    if isinstance(processed, dict) and category_prefix:
                        processed['name'] = category_prefix + processed['name']
                    albums_out.append(processed)

        return ArtistInfo(
            name=artist_name,
            artist_id=artist_id,
            albums=albums_out,
            album_extra_kwargs={**kwargs, 'country': country},
            tracks=tracks_out,
            track_extra_kwargs={**kwargs, 'country': country}
        )

    def _localize_url(self, url):
        """Replace the country code in an Apple Music URL with the account storefront."""
        if not url or not self.account_storefront:
            return url
        # Replace /us/, /gb/, /nl/ etc. with /account_storefront/
        # Matches any 2-letter country code following music.apple.com/
        import re
        return re.sub(r'music\.apple\.com/[a-z]{2}/', f'music.apple.com/{self.account_storefront}/', url)

    def _get_cover_url(self, artwork_template):
        """Build a full cover URL from a template"""
        if not artwork_template:
            return None
        # Replace template with high resolution
        return artwork_template.replace('{w}x{h}bb.jpg', '1400x1400bb.jpg')

    def _format_audio_traits(self, attrs, item_type=None):
        """Format audio traits according to GUI display rules"""
        if 'audioTraits' not in attrs:
            return ""
            
        traits = []
        has_atmos = False
        is_lossless = False
        
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
            else:
                traits.append(trait.replace('-', ' ').title())
                
        if not is_lossless and item_type in ('songs', 'music-videos'):
            traits.append('AAC only')
                
        if has_atmos:
            # Add Atmos trait if detected, always first
            traits.insert(0, '◗◖ ATMOS')
            
        return " · ".join(traits)

    def _get_precise_alac_info(self, attrs, codec, quality_tier: QualityEnum = None):
        """Fetch HLS manifest and parse audio group ID for exact bit depth and sample rate"""
        # Lazy imports for gamdl logic
        try:
            from modules.applemusic.gamdl.gamdl.utils import get_response
            from modules.applemusic.gamdl.gamdl.interface.constants import SONG_CODEC_REGEX_MAP
            import m3u8
            import re
        except ImportError:
            return None

        hls_url = attrs.get('extendedAssetUrls', {}).get('enhancedHls')
        if not hls_url:
            return None

        async def _fetch_manifest():
            try:
                # Use gamdl's get_response utility (uses httpx)
                response = await get_response(hls_url)
                m3u8_obj = m3u8.loads(response.text)
                m3u8_data = m3u8_obj.data
                
                # Use gamdl's codec matching logic
                codec_regex = SONG_CODEC_REGEX_MAP.get(codec.value)
                if not codec_regex:
                    return None
                    
                matching_playlists = [
                    p for p in m3u8_data.get('playlists', [])
                    if re.fullmatch(codec_regex, p["stream_info"]["audio"])
                ]
                
                if not matching_playlists:
                    return None
                
                # Filter for LOSSLESS (Standard Lossless) to avoid HI-RES (96k+) if requested
                if codec.value == "alac" and quality_tier == QualityEnum.LOSSLESS:
                    filtered = []
                    for p in matching_playlists:
                        audio_id = p["stream_info"]["audio"] 
                        try:
                            parts = audio_id.split('-')
                            if len(parts) >= 4:
                                sample_rate = int(parts[-2])
                                if sample_rate <= 48000:
                                    filtered.append(p)
                            else:
                                filtered.append(p)
                        except:
                            filtered.append(p)
                    
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
            except Exception as e:
                if getattr(self, '_debug', False):
                    print(f"[Apple Music Debug] Precise info fetch failed: {e}")
            return None

        # Run in our background event loop
        return self._run_async(lambda s: _fetch_manifest())

    def _resolve_all_binary_paths(self):
        """Pre-resolve all binary paths to speed up future re-initializations"""
        if hasattr(self, 'binary_paths'):
            return
            
        if self._debug:
            print("[Apple Music Debug] Resolving binary paths...")
            
        # Read main OrpheusDL settings.json for binary paths
        main_settings = {}
        settings_file = Path("./config/settings.json")
        if settings_file.exists():
            try:
                with open(settings_file, 'r') as f:
                    main_settings = json.load(f)
            except Exception as e:
                if self._debug:
                    print(f"[Apple Music Debug] Could not read main settings.json: {e}")
        
        # Extract binary paths from main settings, fallback to defaults
        ffmpeg_spec = main_settings.get("global", {}).get("advanced", {}).get("ffmpeg_path", "ffmpeg")
        mp4box_spec = main_settings.get("global", {}).get("advanced", {}).get("mp4box_path", "MP4Box")
        mp4decrypt_spec = main_settings.get("global", {}).get("advanced", {}).get("mp4decrypt_path", "mp4decrypt")
        
        # Helper to find and fix binaries
        def resolve_binary_path(binary_name, default_path):
            # If the user specified a custom path (not the default name), verify it exists
            if default_path != binary_name:
                return default_path

            # Search paths for local binaries
            search_paths = []
            
            # 1. Always check Application Support on macOS first
            if platform.system() == "Darwin":
                app_support = os.path.expanduser("~/Library/Application Support/OrpheusDL GUI")
                search_paths.append(os.path.join(app_support, binary_name))
            
            # 2. Check relative to executable (frozen app)
            if getattr(sys, 'frozen', False):
                app_dir = os.path.dirname(sys.executable)
                search_paths.append(os.path.join(app_dir, binary_name))
                if platform.system() == "Darwin" and ".app/Contents/MacOS" in sys.executable:
                    bundle_dir = os.path.dirname(os.path.dirname(os.path.dirname(sys.executable)))
                    parent_dir = os.path.dirname(bundle_dir)
                    search_paths.append(os.path.join(bundle_dir, binary_name)) 
                    search_paths.append(os.path.join(parent_dir, binary_name))

            # 3. Check CWD
            search_paths.append(os.path.join(os.getcwd(), binary_name))
            
            # Check all search paths
            for path in search_paths:
                if os.path.isfile(path):
                    if not os.access(path, os.X_OK):
                        try:
                            os.chmod(path, 0o755)
                        except:
                            pass
                    return path
            
            # Fallback to system PATH
            system_path = shutil.which(binary_name)
            if system_path:
                return system_path
            
            return binary_name

        # Resolve all binaries
        f_name = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
        mb_name = "mp4box.exe" if platform.system() == "Windows" else "MP4Box"
        md_name = "mp4decrypt.exe" if platform.system() == "Windows" else "mp4decrypt"
        nm_name = "N_m3u8DL-RE.exe" if platform.system() == "Windows" else "N_m3u8DL-RE"

        self.binary_paths = {
            'ffmpeg': resolve_binary_path(f_name, ffmpeg_spec),
            'mp4box': resolve_binary_path(mb_name, mp4box_spec),
            'mp4decrypt': resolve_binary_path(md_name, mp4decrypt_spec),
            'nm3u8dlre': resolve_binary_path(nm_name, self.settings.get('nm3u8dlre_path', 'N_m3u8DL-RE'))
        }


        if self._debug:
            print(f"[Apple Music Debug] Binary paths resolved: {self.binary_paths}")
