import os
import sys
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
import threading

# Add gamdl to the path
current_dir = Path(__file__).parent
gamdl_path = current_dir / "gamdl"
if str(gamdl_path) not in sys.path:
    sys.path.insert(0, str(gamdl_path))

# Initialize gamdl availability check
GAMDL_AVAILABLE = False

def _lazy_import_gamdl():
    """Lazy import gamdl components to avoid conflicts with GUI patches"""
    global GAMDL_AVAILABLE, AppleMusicApi, ItunesApi, GamdlSongCodec, GamdlRemuxMode, GamdlDownloadMode, \
        AppleMusicDownloader, AppleMusicBaseDownloader, AppleMusicSongDownloader, AppleMusicMusicVideoDownloader, \
        AppleMusicUploadedVideoDownloader, AppleMusicInterface, AppleMusicSongInterface, \
        AppleMusicMusicVideoInterface, AppleMusicUploadedVideoInterface, LEGACY_SONG_CODECS, \
        SyncedLyricsFormat, RemuxFormatMusicVideo
    
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
        'InquirerPy.base',
        'InquirerPy.base.control'
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
        print(f"[Apple Music Error] gamdl directory not found at: {gamdl_path}")
        return False
    
    # Debug: Check if key files exist
    apple_music_api_file = gamdl_path / "gamdl" / "api" / "apple_music_api.py"
    if not apple_music_api_file.exists():
        print(f"[Apple Music Error] apple_music_api.py not found at: {apple_music_api_file}")
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
        from gamdl.api.apple_music_api import AppleMusicApi
        from gamdl.api.itunes_api import ItunesApi
        from gamdl.interface.enums import SongCodec as GamdlSongCodec, SyncedLyricsFormat
        from gamdl.downloader.enums import DownloadMode as GamdlDownloadMode, RemuxMode as GamdlRemuxMode, RemuxFormatMusicVideo
        from gamdl.downloader.downloader import AppleMusicDownloader
        from gamdl.downloader.downloader_base import AppleMusicBaseDownloader
        from gamdl.downloader.downloader_song import AppleMusicSongDownloader
        from gamdl.downloader.downloader_music_video import AppleMusicMusicVideoDownloader
        from gamdl.downloader.downloader_uploaded_video import AppleMusicUploadedVideoDownloader
        from gamdl.interface.interface import AppleMusicInterface
        from gamdl.interface.interface_song import AppleMusicSongInterface
        from gamdl.interface.interface_music_video import AppleMusicMusicVideoInterface
        from gamdl.interface.interface_uploaded_video import AppleMusicUploadedVideoInterface
        from gamdl.interface.constants import LEGACY_SONG_CODECS
        
        GAMDL_AVAILABLE = True
        return True
        
    except ImportError as e:
        print(f"[Apple Music] Warning: Could not import gamdl components: {e}")
        print("[Apple Music] Module will run with limited functionality")
        return False
    except Exception as e:
        print(f"[Apple Music] Error during gamdl import: {e}")
        import traceback
        traceback.print_exc()
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
        'codec': 'aac',
        'quality': 'high',
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
        self.gamdl_downloader_song = None
        self.gamdl_downloader = None # To store the gamdl.Downloader instance
        self.is_authenticated = False  # Default to not authenticated
        self._using_rich_tagging = False  # Track when we're using gamdl's rich tagging to prevent OrpheusDL overwriting
        # Consolidate debug setting from module-specific and global settings
        self._debug = settings.get('debug', False) or (
            hasattr(module_controller, 'settings') and 
            module_controller.settings.get('global', {}).get('advanced', {}).get('debug_mode', False)
        )
        
        # Lock for synchronizing async operations across threads
        self._lock = threading.Lock()
        
        if not _lazy_import_gamdl():
            raise self.exception("gamdl components not available - please check installation")
        
        import asyncio
        
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
            
            with suppress_gamdl_debug():
                # Check for use_wrapper setting (from CLI or settings.json)
                self.use_wrapper = self.settings.get('use_wrapper', False)
                wrapper_decrypt_ip = self.settings.get('wrapper_decrypt_ip')
                language = self.settings.get('language', 'en-US')

                if self.use_wrapper:
                    wrapper_account_url = self.settings.get('wrapper_account_url')
                    if not wrapper_account_url:
                        # Try to get it from a common place or default
                        wrapper_account_url = "http://127.0.0.1:20020"
                    
                    if self._debug:
                        print(f"[Apple Music Debug] Initializing with wrapper: {wrapper_account_url}")
                    
                    try:
                        self.apple_music_api = asyncio.run(AppleMusicApi.create_from_wrapper(
                            wrapper_account_url=wrapper_account_url,
                            language=language,
                            wrapper_decrypt_ip=wrapper_decrypt_ip
                        ))
                    except Exception as wrapper_err:
                        # Fallback to cookies if wrapper cannot be reached
                        import logging
                        logging.warning(f"Failed to connect to Apple Music wrapper at {wrapper_account_url}: {wrapper_err}")
                        logging.warning("Falling back to cookies.json for session initialization...")
                        kwargs = {'language': language, 'wrapper_decrypt_ip': wrapper_decrypt_ip}
                        if cookies_path:
                            kwargs['cookies_path'] = str(cookies_path)
                        self.apple_music_api = asyncio.run(AppleMusicApi.create_from_netscape_cookies(**kwargs))
                else:
                    if self._debug:
                        print(f"[Apple Music Debug] Initializing with cookies: {cookies_path}")
                    
                    kwargs = {'language': language, 'wrapper_decrypt_ip': wrapper_decrypt_ip}
                    if cookies_path:
                        kwargs['cookies_path'] = str(cookies_path)
                    self.apple_music_api = asyncio.run(AppleMusicApi.create_from_netscape_cookies(**kwargs))
                
                self.itunes_api = ItunesApi(
                    self.apple_music_api.storefront if self.apple_music_api else 'us', # Fallback storefront
                    self.apple_music_api.language if self.apple_music_api else 'en-US'  # Fallback language
                )
            
            # Check for authentication token after initialization and set authentication status
            self.is_authenticated = self.apple_music_api.active_subscription
            if self.is_authenticated:
                if self._debug:
                    print("[Apple Music Debug] Successfully authenticated with active subscription.")
            elif self._debug:
                print("[Apple Music Warning] Not authenticated or no active subscription. Downloads will likely fail.")

            if self._debug and self.apple_music_api:
                print(f"[Apple Music Debug] Initialized with storefront: {self.apple_music_api.storefront}")
            
            # Save the account's native storefront for fallbacks
            self.account_storefront = self.apple_music_api.storefront if self.apple_music_api else 'us'
            
            # Resolve binary paths once to speed up re-initialization
            self._resolve_all_binary_paths()
            
            # Map codec setting to gamdl enum
            self.song_codec = self._get_gamdl_codec(self.settings.get('codec', 'aac'))
                
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
                raise self.exception(f"Failed to initialize Apple Music API: {e}")

    def _run_async(self, func, storefront: Optional[str] = None):
        """Helper to run API methods in a separate loop and ensure the client is initialized for that loop"""
        async def wrapper():
            # Force re-initialization of gamdl components for the new loop FIRST
            # This is essential since gamdl components might hold loop-bound state (locks, sessions, etc.)
            # We do this before storefront restoration so we don't overwrite it immediately after
            self._initialize_gamdl_components(force=True)

            # Determine which storefront to use. Priority:
            # 1. Explicitly passed storefront parameter
            # 2. Current global storefront (if set)
            # 3. Account default storefront
            target_sf = storefront or getattr(self.apple_music_api, 'storefront', None) or self.account_storefront

            # Re-initialize the clients to ensure they're bound to the current event loop
            if hasattr(self, 'apple_music_api') and self.apple_music_api:
                if getattr(self, '_debug', False):
                    print(f"[{self.module_information.service_name} DEBUG] _run_async: Preserving - Target: {target_sf}, Current AM: {self.apple_music_api.storefront}")
                
                if hasattr(self.apple_music_api, 'initialize'):
                    await self.apple_music_api.initialize()
                elif hasattr(self.apple_music_api, '_initialize_client'):
                    await self.apple_music_api._initialize_client()
                
                # Restore the storefront to the intended target (might have been reset by initialize())
                if target_sf:
                    self.apple_music_api.storefront = target_sf
            
            # Also re-initialize iTunes API if it exists
            if hasattr(self, 'itunes_api') and self.itunes_api:
                if hasattr(self.itunes_api, 'initialize'):
                    self.itunes_api.initialize()
                # Ensure it carries the target storefront
                if target_sf:
                    self.itunes_api.storefront = target_sf
            
            if getattr(self, '_debug', False):
                am_sf = getattr(self.apple_music_api, 'storefront', 'None')
                it_sf = getattr(self.itunes_api, 'storefront', 'None')
                print(f"[{self.module_information.service_name} DEBUG] _run_async: Restored - AM: {am_sf}, iTunes: {it_sf}")
            
            # Load ALL cookies from the session file to ensure explicit content permission (m-allowed)
            # and other session state is preserved across event loops
            cookies_path = self.settings.get('cookies_path')
            if cookies_path and Path(cookies_path).exists() and self.apple_music_api:
                from http.cookiejar import MozillaCookieJar
                try:
                    cj = MozillaCookieJar(cookies_path)
                    cj.load(ignore_discard=True, ignore_expires=True)
                    for cookie in cj:
                        # Skip media-user-token as AppleMusicApi handles it specially
                        if cookie.name == 'media-user-token':
                            continue
                        
                        # If the cookie already exists in the jar, skip it to avoid "Multiple cookies exist" collisions.
                        # This prioritizes the "fresh" session cookies set during initialize() while still
                        # loading the "missing" ones (like m-allowed for explicit content) from cookies.txt.
                        if cookie.name in self.apple_music_api.client.cookies:
                            continue

                        try:
                            self.apple_music_api.client.cookies.set(
                                cookie.name, cookie.value, domain=cookie.domain, path=cookie.path
                            )
                        except Exception as e:
                            if self._debug:
                                print(f"[Apple Music Debug] Could not set cookie {cookie.name}: {e}")
                except Exception as ce:
                    if self._debug:
                        print(f"[Apple Music Debug] Failed to load extra cookies from {cookies_path}: {ce}")

            # Clear any cached results in gamdl interfaces that might hold stale futures/loops
            self._clear_gamdl_caches()
            
            # Now execute the provided function with the initialized API(s)
            return await func(self)
        
        with self._lock:
            return asyncio.run(wrapper())

    def _clear_gamdl_caches(self):
        """Clear alru_cache in gamdl interfaces to prevent loop-mismatch errors"""
        interfaces = [
            getattr(self, 'gamdl_interface', None),
            getattr(self, 'gamdl_song_interface', None),
            getattr(self, 'gamdl_music_video_interface', None),
            getattr(self, 'gamdl_uploaded_video_interface', None)
        ]
        for interface in interfaces:
            if interface:
                for attr_name in dir(interface):
                    attr = getattr(interface, attr_name)
                    if hasattr(attr, 'cache_clear'):
                        try:
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
        elif codec_lower == 'alac':
            return GamdlSongCodec.ALAC
        elif codec_lower == 'alac-lossless':
            return GamdlSongCodec.ALAC_LOSSLESS
        elif codec_lower == 'alac-hi-res':
            return GamdlSongCodec.ALAC_HI_RES
        elif codec_lower == 'atmos':
            return GamdlSongCodec.ATMOS
        else:
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
        requested_codec = song_codec if song_codec is not None else self.song_codec
        requested_wrapper = use_wrapper if use_wrapper is not None else self.use_wrapper

        # Check if we need to re-initialize due to different settings or loop change
        needs_reinit = force
        if not needs_reinit and self.gamdl_downloader:
            if self.gamdl_base_downloader.use_wrapper != requested_wrapper:
                needs_reinit = True
            elif hasattr(self.gamdl_song_downloader, 'codec') and self.gamdl_song_downloader.codec != requested_codec:
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
                    ffmpeg_path=self.binary_paths['ffmpeg'],
                    mp4box_path=self.binary_paths['mp4box'],
                    mp4decrypt_path=self.binary_paths['mp4decrypt'],
                    amdecrypt_path=self.binary_paths['amdecrypt'],
                    nm3u8dlre_path=self.binary_paths['nm3u8dlre'],
                    use_wrapper=requested_wrapper,
                    wrapper_decrypt_ip=self.settings.get('wrapper_decrypt_ip', '127.0.0.1:10020'),
                    overwrite=True,
                    download_mode=self.settings.get('download_mode', GamdlDownloadMode.YTDLP),
                    remux_mode=self.settings.get('remux_mode', GamdlRemuxMode.FFMPEG),
                    silent=not self._debug
                )
                
                # Setup gamdl interfaces
                self.gamdl_interface = AppleMusicInterface(self.apple_music_api, self.itunes_api)
                self.gamdl_song_interface = AppleMusicSongInterface(self.gamdl_interface)
                self.gamdl_music_video_interface = AppleMusicMusicVideoInterface(self.gamdl_interface)
                self.gamdl_uploaded_video_interface = AppleMusicUploadedVideoInterface(self.gamdl_interface)
                
                # Setup sub-downloaders
                self.gamdl_song_downloader = AppleMusicSongDownloader(
                    base_downloader=self.gamdl_base_downloader,
                    interface=self.gamdl_song_interface,
                    codec=requested_codec
                )
                self.gamdl_music_video_downloader = AppleMusicMusicVideoDownloader(
                    base_downloader=self.gamdl_base_downloader,
                    interface=self.gamdl_music_video_interface
                )
                self.gamdl_uploaded_video_downloader = AppleMusicUploadedVideoDownloader(
                    base_downloader=self.gamdl_base_downloader,
                    interface=self.gamdl_uploaded_video_interface
                )
                
                # Setup main gamdl downloader
                self.gamdl_downloader = AppleMusicDownloader(
                    interface=self.gamdl_interface,
                    base_downloader=self.gamdl_base_downloader,
                    song_downloader=self.gamdl_song_downloader,
                    music_video_downloader=self.gamdl_music_video_downloader,
                    uploaded_video_downloader=self.gamdl_uploaded_video_downloader
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
            
            return MediaIdentification(
                media_type=media_type,
                media_id=url_info['id'],
                extra_kwargs={'country': url_info['country']}
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
                    if 'contentRating' in attrs:
                        additional.append(attrs['contentRating'])
                        
                    formatted_traits = self._format_audio_traits(attrs)
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
                        # Re-initialize the API with new cookies
                        self.apple_music_api = asyncio.run(AppleMusicApi.create_from_netscape_cookies(
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

        # Extract country from either direct kwargs or the 'data' dict
        country = kwargs.get('country') or (data.get('country') if data else None)
        
        if getattr(self, '_debug', False):
            print(f"[{self.module_information.service_name} DEBUG] Extracted country: {country}")
            
        self._set_storefront(country)

        try:
            # Check if we have raw_result from search - use it to avoid extra API call
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
                        if s._debug:
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
                    self._set_storefront(self.account_storefront)
                    track_api_data = self._run_async(lambda s: _fetch_with_logging(s, track_id), storefront=country)
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
                            if s._debug:
                                print(f"[Apple Music Debug] iTunes lookup failed: {ie}")
                        return None
                    
                    track_api_data = self._run_async(lambda s: _fetch_itunes(s, track_id), storefront=country)

            if not track_api_data or 'attributes' not in track_api_data:
                if self._debug:
                    print(f"[Apple Music Error] Could not fetch track data for {track_id} from AppleMusicApi.")
                return TrackInfo(name=f"Error: Fetch failed for {track_id}", error="API Fetch Failed", artists=["Unknown Artist"], album="", album_id=None, artist_id=None, duration=0, codec=CodecEnum.AAC, bitrate=0, sample_rate=0, release_year=None, cover_url=None, explicit=False, tags=Tags())

            attrs = track_api_data['attributes']
            
            name = attrs.get('name', 'Unknown Track')
            album_name = attrs.get('albumName', 'Unknown Album')
            artist_name = attrs.get('artistName', 'Unknown Artist')
            
            # Get primary artist ID if available (might need more complex logic for multiple artists/credits)
            # For now, try to get it from the relationships if they exist
            artist_id_from_rels = None
            if track_api_data.get('relationships') and 'artists' in track_api_data['relationships']:
                artist_data_rels = track_api_data['relationships']['artists'].get('data')
                if artist_data_rels and len(artist_data_rels) > 0:
                    artist_id_from_rels = artist_data_rels[0].get('id')

            # Artwork
            artwork_template = attrs.get('artwork', {}).get('url')
            cover_url = self._get_cover_url(artwork_template)

            # Duration
            duration_ms = attrs.get('durationInMillis')
            duration_sec = duration_ms // 1000 if duration_ms is not None else 0

            # Release Date & Year
            release_date_str = attrs.get('releaseDate')
            year = self._extract_year(release_date_str)

            # Codec & Bitrate (these are indicative, actual download format decided by get_track_download)
            # Use overrides from kwargs if present (passed from orpheus.py via extra_kwargs)
            override_song_codec = kwargs.get('song_codec')
            override_use_wrapper = kwargs.get('use_wrapper')
            effective_codec = self._get_gamdl_codec(override_song_codec) if override_song_codec else self.song_codec
            
            display_codec = CodecEnum.AAC
            display_bitrate = 256
            display_bit_depth = 16
            display_sample_rate = 44100

            if effective_codec in (GamdlSongCodec.ALAC, GamdlSongCodec.ALAC_HI_RES, GamdlSongCodec.ALAC_LOSSLESS, GamdlSongCodec.ATMOS):
                display_codec = CodecEnum.ALAC if effective_codec != GamdlSongCodec.ATMOS else CodecEnum.EAC3
                display_bitrate = 0 if effective_codec != GamdlSongCodec.ATMOS else 768
                
            if effective_codec in (GamdlSongCodec.ALAC, GamdlSongCodec.ALAC_HI_RES, GamdlSongCodec.ALAC_LOSSLESS, GamdlSongCodec.ATMOS):
                display_codec = CodecEnum.ALAC if effective_codec != GamdlSongCodec.ATMOS else CodecEnum.EAC3
                display_bitrate = 0 if effective_codec != GamdlSongCodec.ATMOS else 768
                
                # Simplified indicative display as requested by user
                traits = attrs.get('audioTraits', [])
                if effective_codec == GamdlSongCodec.ALAC_HI_RES:
                    display_bit_depth = 24
                    display_sample_rate = 96000
                elif effective_codec == GamdlSongCodec.ALAC_LOSSLESS:
                    # Default Lossless to 24/48 as requested (common AM standard)
                    display_bit_depth = 24
                    display_sample_rate = 48000
                elif 'hi-res-lossless' in traits:
                    display_bit_depth = 24
                    display_sample_rate = 96000
                elif 'lossless' in traits:
                    display_bit_depth = 24
                    display_sample_rate = 48000
                else:
                    # Fallback for generic ALAC or Atmos
                    display_bit_depth = 24 if effective_codec != GamdlSongCodec.ATMOS else 16
                    display_sample_rate = 48000
            
            # Explicit content
            explicit = attrs.get('contentRating') == 'explicit'

            # Tags
            tags_obj = Tags(
                album_artist=attrs.get('albumArtistName', artist_name),
                track_number=attrs.get('trackNumber'),
                # total_tracks= Need album context or separate fetch
                disc_number=attrs.get('discNumber'),
                # total_discs= Need album context
                release_date=release_date_str,
                genres=attrs.get('genreNames', []),
                isrc=attrs.get('isrc'),
                composer=attrs.get('composerName'),
                # title, album, artist, year, explicit are part of TrackInfo directly
            )

            # Orpheus expects a list of artists
            artists_list = [artist_name] if artist_name else ["Unknown Artist"]
            
            # Album ID might be part of a relationship or derivable if this track_info is part of an album call
            # For a standalone track_info call, it might not be directly available without extra context/calls
            album_id_from_rels = None
            if track_api_data.get('relationships') and 'albums' in track_api_data['relationships']:
                album_data_rels = track_api_data['relationships']['albums'].get('data')
                if album_data_rels and len(album_data_rels) > 0:
                    album_id_from_rels = album_data_rels[0].get('id')

            # If album_id is not found from the relationships (e.g., from a search result),
            # fetch the full song data to get it.
            if not album_id_from_rels or not artist_id_from_rels:
                if self._debug:
                    print(f"[Apple Music Debug] Album or Artist ID not in relationships for track {track_id}. Fetching full song data.")
                full_track_data = self._run_async(lambda s: s.apple_music_api.get_song(track_id), storefront=country)
                if full_track_data and 'relationships' in full_track_data:
                    # Try to get album_id again
                    if not album_id_from_rels and 'albums' in full_track_data['relationships']:
                        album_data_rels = full_track_data['relationships']['albums'].get('data')
                        if album_data_rels and len(album_data_rels) > 0:
                            album_id_from_rels = album_data_rels[0].get('id')

                    # Try to get artist_id again
                    if not artist_id_from_rels and 'artists' in full_track_data['relationships']:
                        artist_data_rels = full_track_data['relationships']['artists'].get('data')
                        if artist_data_rels and len(artist_data_rels) > 0:
                            artist_id_from_rels = artist_data_rels[0].get('id')
                    
                    # Update track_api_data with full metadata if we fetched it, so downloader has all info (like lyrics flags)
                    if full_track_data and 'data' in full_track_data and len(full_track_data['data']) > 0:
                        track_api_data = full_track_data['data'][0]
                    
                    if self._debug:
                        print(f"[Apple Music Debug] Found IDs after full fetch: Album={album_id_from_rels}, Artist={artist_id_from_rels}")

            # Check for storefront mismatch and try to find equivalent track ID in user's region
            actual_download_id = track_id
            
            # Use account_storefront from self if available
            user_storefront = getattr(self, 'account_storefront', None)
            
            # Determine effective storefront used for the API call
            # This logic mimics _set_storefront: if country was passed, API used it.
            # If not, API used whatever was set (likely user_storefront or 'us')
            api_storefront = country.lower() if country else (self.apple_music_api.storefront if self.apple_music_api else 'us')
            
            if self.is_authenticated and user_storefront and api_storefront and user_storefront.lower() != api_storefront.lower():
                track_isrc = tags_obj.isrc
                if track_isrc or (name and artists_list):
                    if self._debug:
                        print(f"[Apple Music Debug] Storefront mismatch detected (User: {user_storefront}, Source: {api_storefront}). Checking for equivalent track...")
                    
                    equivalent_id = self._get_equivalent_track_id(track_isrc, user_storefront, title=name, artist=artists_list[0] if artists_list else None)
                    if equivalent_id:
                        actual_download_id = equivalent_id
                        if self._debug:
                            print(f"[Apple Music Debug] Found equivalent track {actual_download_id} in {user_storefront}. Fetching its metadata...")
                        
                        # Re-fetch metadata for the equivalent ID in the user's storefront to ensure downloader has working info
                        equiv_metadata = self._run_async(lambda s: s.apple_music_api.get_song(actual_download_id), storefront=user_storefront)
                        if equiv_metadata and 'data' in equiv_metadata and len(equiv_metadata['data']) > 0:
                            track_api_data = equiv_metadata['data'][0]
                            # Update local attrs for any later logic in this method
                            attrs = track_api_data['attributes']
                            if self._debug:
                                print(f"[Apple Music Debug] Successfully fetched metadata for equivalent track {actual_download_id}")

            # download_extra_kwargs can store the raw API response if downloader needs more later
            download_extra_kwargs = {
                'track_id': actual_download_id,
                'api_response': track_api_data, 
                'source_quality_tier': quality_tier.name,
                'original_id': track_id,
                'effective_storefront': user_storefront if actual_download_id != track_id else api_storefront
            }
            
            # Persist flags for the downloader
            if override_song_codec: download_extra_kwargs['song_codec'] = override_song_codec
            if override_use_wrapper is not None: download_extra_kwargs['use_wrapper'] = override_use_wrapper
            
            return TrackInfo(
                name=name,
                album=album_name,
                album_id=str(album_id_from_rels) if album_id_from_rels else None,
                artists=artists_list,
                artist_id=str(artist_id_from_rels) if artist_id_from_rels else None,
                duration=duration_sec,
                codec=display_codec,
                bitrate=display_bitrate,
                bit_depth=display_bit_depth,
                sample_rate=display_sample_rate // 1000 if display_sample_rate else None,
                release_year=year,
                cover_url=cover_url,
                explicit=explicit,
                tags=tags_obj,
                id=actual_download_id,
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
            print(f"[Apple Music Debug] get_track_download called for track_id: {track_id}, quality_tier: {quality_tier.name if quality_tier else 'None'}")

        # Re-evaluate settings from config to ensure we catch changes from the GUI
        self.song_codec = self._get_gamdl_codec(self.settings.get('codec', 'aac'))
        self.use_wrapper = self.settings.get('use_wrapper', False)

        self._ensure_credentials()
        self._using_rich_tagging = False
        
        # Check for overrides from kwargs (passed from orpheus.py via extra_kwargs)
        override_song_codec = kwargs.get('song_codec')
        override_use_wrapper = kwargs.get('use_wrapper')
        
        # Map string override to enum if present
        effective_codec = self._get_gamdl_codec(override_song_codec) if override_song_codec else None
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
            # Ensure gamdl components are initialized, passing overrides if present
            self._initialize_gamdl_components(song_codec=effective_codec, use_wrapper=override_use_wrapper)

            if not self.gamdl_downloader_song or not self.gamdl_downloader:
                raise DownloadError("Apple Music: gamdl components could not be initialized.")

            # 1. Get metadata (use provided api_response if available to save a request)
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

            # 3. Download and process
            actual_codec = effective_codec if effective_codec else self.song_codec
            codec_name = actual_codec.name if hasattr(actual_codec, 'name') else str(actual_codec)
            
            # Print accurate stream info if available
            stream_info = download_item.stream_info.audio_track if download_item.stream_info else None
            if stream_info and stream_info.bit_depth:
                print(f"{indent_spaces}Detected Stream: {codec_name}, bit depth: {stream_info.bit_depth}bit, sample rate: {stream_info.sample_rate}Hz")
            
            print(f"{indent_spaces}Downloading and processing {codec_name} track...")
            
            try:
                await self.gamdl_downloader.download(download_item)
            except Exception as e:
                if self._debug:
                    print(f"[Apple Music Error] gamdl download failed: {type(e).__name__}: {e}")
                raise DownloadError(f"Apple Music: Download execution failed - {type(e).__name__}: {e}") from e
            
            return download_item

        try:
            download_item = self._run_async(lambda s: _download_async())
            
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
            
            # Check for amdecrypt connection error
            if "dial tcp" in error_str and ("10020" in error_str or "refused" in error_str.lower() or "geweigerd" in error_str.lower() or "127.0.0.1" in error_str):
                # This is a critical error for Atmos/ALAC downloads
                raise DownloadError("Apple Music: amdecrypt could not connect to the decryption agent (127.0.0.1:10020). Please ensure your CD/AM agent is running.") from e
            
            if '"failureType":"3076"' in error_str:
                raise TrackUnavailableError("This song is unavailable.") from e
            if '"failureType":"2002"' in error_str or "Your session has ended" in error_str:
                raise DownloadError('"cookies.txt" is invalid or expired.')
            
            if self._debug:
                import traceback
                print(f"[Apple Music Error] Download failed for track {track_id}: {type(e).__name__}: {e}")
                print(traceback.format_exc())
            
            # Use original exception message if descriptive, else add type
            final_msg = error_str if error_str and len(error_str) > 5 else f"{type(e).__name__}: {e}"
            
            if "FormatNotAvailable" in str(type(e)):
                requested_codec_name = codec_name if 'codec_name' in locals() else (override_song_codec or self.song_codec)
                if str(requested_codec_name).lower() in ['atmos', 'alac']:
                    wrapper_enabled = override_use_wrapper if override_use_wrapper is not None else getattr(self.gamdl_base_downloader, 'use_wrapper', False)
                    if not wrapper_enabled:
                        final_msg = f"This {str(requested_codec_name).upper()} track requires the 'Use Wrapper' setting to be enabled in your Apple Music credentials."
                        
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
            album_artist = attrs.get('artistName', '')
            cover_url = self._get_cover_url(attrs.get('artwork', {}).get('url'))
            release_year = self._extract_year(attrs.get('releaseDate'))
            # Use full track data from API when available to avoid N get_track_info calls in GUI
            tracks_out = []
            rel_tracks = (album_data.get('relationships') or {}).get('tracks', {}).get('data', [])
            for idx, track in enumerate(rel_tracks, start=1):
                t_attrs = track.get('attributes') or {}
                if t_attrs:
                    name = t_attrs.get('name') or f'Track {idx}'
                    dur_ms = t_attrs.get('durationInMillis')
                    duration_sec = (dur_ms // 1000) if isinstance(dur_ms, (int, float)) else None
                    artist = t_attrs.get('artistName') or album_artist # Use track artist if available, else album artist
                    
                    additional = self._format_audio_traits(t_attrs)

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
                        'relationships': track.get('relationships'),
                        'type': track.get('type'),
                        'additional': additional
                    })
                else:
                    tracks_out.append(track.get('id', ''))
            return AlbumInfo(
                name=attrs['name'],
                artist=album_artist,
                artist_id='',
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
                if ('relationships' not in playlist_data or 
                    'tracks' not in playlist_data.get('relationships', {}) or 
                    not playlist_data['relationships']['tracks'].get('data')):
                    if self._debug:
                        print(f"[Apple Music Debug] Search result missing track data, fetching full playlist info...")
                    playlist_data = self._run_async(lambda s: s.apple_music_api.get_playlist(playlist_id), storefront=country)
            else:
                playlist_data = self._run_async(lambda s: s.apple_music_api.get_playlist(playlist_id), storefront=country)
            
            if playlist_data and 'data' in playlist_data:
                playlist_data = playlist_data['data'][0]
            
            attrs = playlist_data['attributes']
            cover_url = self._get_cover_url(attrs.get('artwork', {}).get('url'))
            release_year = self._extract_year(attrs.get('lastModifiedDate'))
            creator = attrs.get('curatorName', 'Unknown Creator')
            # Use full track data from API when available to avoid N get_track_info calls in GUI (same as album)
            tracks_out = []
            rel_tracks = (playlist_data.get('relationships') or {}).get('tracks', {}).get('data', [])
            for idx, track in enumerate(rel_tracks, start=1):
                t_attrs = track.get('attributes') or {}
                if t_attrs:
                    name = t_attrs.get('name') or f'Track {idx}'
                    dur_ms = t_attrs.get('durationInMillis')
                    duration_sec = (dur_ms // 1000) if isinstance(dur_ms, (int, float)) else None
                    artist = t_attrs.get('artistName') or creator
                    
                    additional = self._format_audio_traits(t_attrs)

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
                        'relationships': track.get('relationships'),
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
        artist_name = attrs.get('name', 'Unknown Artist')
        cover_url_default = self._get_cover_url(attrs.get('artwork', {}).get('url'))
        # Use full album data from API when available to avoid N get_album_info calls in GUI
        albums_out = []
        rel_albums = (artist_data.get('relationships') or {}).get('albums', {}).get('data', [])
        for album in rel_albums:
            a_attrs = album.get('attributes') or {}
            if a_attrs:
                name = a_attrs.get('name') or 'Unknown Album'
                release_year = self._extract_year(a_attrs.get('releaseDate'))
                album_artist = a_attrs.get('artistName') or artist_name
                cover_url = self._get_cover_url(a_attrs.get('artwork', {}).get('url')) or cover_url_default
                
                additional_parts = []
                tc = a_attrs.get('trackCount')
                if tc is not None and tc > 0:
                    additional_parts.append("1 track" if tc == 1 else f"{tc} tracks")
                
                formatted_traits = self._format_audio_traits(a_attrs)
                if formatted_traits:
                    additional_parts.append(formatted_traits)
                    
                additional = " | ".join(additional_parts)
                
                albums_out.append({
                    'id': album.get('id', ''),
                    'name': name,
                    'artist': album_artist,
                    'release_year': release_year,
                    'cover_url': cover_url,
                    'additional': additional,
                    # Pass full API data so get_album_info doesn't need to refetch
                    'attributes': a_attrs,
                    'relationships': album.get('relationships'),
                    'type': album.get('type')
                })
            else:
                albums_out.append(album.get('id', ''))
        return ArtistInfo(
            name=artist_name,
            artist_id=artist_id,
            albums=albums_out,
            album_extra_kwargs={**kwargs, 'country': country}
        )

    def _get_cover_url(self, artwork_template):
        """Build a full cover URL from a template"""
        if not artwork_template:
            return None
        # Replace template with high resolution
        return artwork_template.replace('{w}x{h}bb.jpg', '1400x1400bb.jpg')

    def _format_audio_traits(self, attrs):
        """Format audio traits according to GUI display rules"""
        if 'audioTraits' not in attrs:
            return ""
            
        traits = []
        has_atmos = False
        
        for trait in attrs['audioTraits']:
            # 'lossy-stereo' is standard
            # 'lossless' is extremely common, so we omit it
            if trait in ('lossy-stereo', 'lossless'):
                continue
            elif trait in ('atmos', 'spatial'):
                has_atmos = True
            elif trait == 'hi-res-lossless':
                traits.append('Hi Res Lossless')
            else:
                traits.append(trait.replace('-', ' ').title())
                
        if has_atmos:
            traits.insert(0, 'Dolby Atmos')
            
        return " | ".join(traits)

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
        ad_name = "amdecrypt.exe" if platform.system() == "Windows" else "amdecrypt"
        nm_name = "N_m3u8DL-RE.exe" if platform.system() == "Windows" else "N_m3u8DL-RE"

        self.binary_paths = {
            'ffmpeg': resolve_binary_path(f_name, ffmpeg_spec),
            'mp4box': resolve_binary_path(mb_name, mp4box_spec),
            'mp4decrypt': resolve_binary_path(md_name, mp4decrypt_spec),
            'amdecrypt': resolve_binary_path(ad_name, self.settings.get('amdecrypt_path', 'amdecrypt')),
            'nm3u8dlre': resolve_binary_path(nm_name, self.settings.get('nm3u8dlre_path', 'N_m3u8DL-RE'))
        }


        if self._debug:
            print(f"[Apple Music Debug] Binary paths resolved: {self.binary_paths}")
