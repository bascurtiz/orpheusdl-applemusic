import os
print("[Apple Music Interface] *** MODULE LOADED - BUILD DATE: 2026-01-13 16:45 ***")
import re
import sys
from pathlib import Path
from typing import Dict, Any, Optional
import json
import requests
import tempfile
import platform
import shutil
from enum import Enum
from contextlib import contextmanager

# Add gamdl to the path
current_dir = Path(__file__).parent
gamdl_path = current_dir / "gamdl"
if str(gamdl_path) not in sys.path:
    sys.path.insert(0, str(gamdl_path))

# Initialize gamdl availability check
GAMDL_AVAILABLE = False

def _lazy_import_gamdl():
    """Lazy import gamdl components to avoid conflicts with GUI patches"""
    global GAMDL_AVAILABLE, AppleMusicApi, ItunesApi, GamdlSongCodec, GamdlRemuxMode, GamdlDownloadMode, Downloader, DownloaderSong, LEGACY_CODECS, DownloaderSongLegacy
    
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
    apple_music_api_file = gamdl_path / "gamdl" / "apple_music_api.py"
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
        from gamdl.apple_music_api import AppleMusicApi
        from gamdl.itunes_api import ItunesApi  
        from gamdl.enums import SongCodec as GamdlSongCodec, RemuxMode as GamdlRemuxMode, DownloadMode as GamdlDownloadMode
        from gamdl.downloader import Downloader
        from gamdl.downloader_song import DownloaderSong
        from gamdl.constants import LEGACY_CODECS
        from gamdl.downloader_song_legacy import DownloaderSongLegacy
        
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
Downloader = None
DownloaderSong = None
LEGACY_CODECS = None
DownloaderSongLegacy = None

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
        'quality': 'high'
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
        self._debug = settings.get('debug', False)  # Add debug setting
        
        if not _lazy_import_gamdl():
            raise self.exception("gamdl components not available - please check installation")
        
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
        
        # Initialize gamdl APIs
        try:
            # Control gamdl debug output via environment variable
            if not self._debug:
                os.environ['GAMDL_DEBUG'] = 'false'
            
            with suppress_gamdl_debug():
                self.apple_music_api = AppleMusicApi(
                    cookies_path=Path(cookies_path) if cookies_path else None,
                    language=self.settings.get('language', 'en-US')
                )
                
                self.itunes_api = ItunesApi(
                    self.apple_music_api.storefront if self.apple_music_api else 'us', # Fallback storefront
                    self.apple_music_api.language if self.apple_music_api else 'en-US'  # Fallback language
                )
            
            # Check for authentication token after initialization and set authentication status
            if self.apple_music_api and self.apple_music_api.session.headers.get('Media-User-Token'):
                self.is_authenticated = True
                if self._debug:
                    print("[Apple Music Debug] Successfully authenticated with Media-User-Token.")
            elif self._debug:
                print("[Apple Music Warning] Not authenticated. Media-User-Token not found. Downloads will likely fail.")
                if self.apple_music_api and self.apple_music_api.session:
                    # Log existing cookie keys for easier debugging
                    cookie_keys = list(self.apple_music_api.session.cookies.get_dict().keys())
                    print(f"[Apple Music Debug] Cookie keys loaded: {cookie_keys}")
                print('[Apple Music Debug] Tip: Ensure "cookies.txt" is in the Netscape format, e.g., by using a browser extension to export it.')

            if self._debug and self.apple_music_api:
                print(f"[Apple Music Debug] Initialized with storefront: {self.apple_music_api.storefront}")
            
            # Map codec setting to gamdl enum
            codec_setting = self.settings.get('codec', 'aac').lower()
            if codec_setting == 'aac':
                self.song_codec = GamdlSongCodec.AAC_LEGACY  # Use AAC_LEGACY to match standalone gamdl default
            elif codec_setting == 'alac':
                self.song_codec = GamdlSongCodec.ALAC
            else:
                self.song_codec = GamdlSongCodec.AAC_LEGACY  # Default to AAC_LEGACY
                
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

    def _set_storefront(self, country_code: Optional[str]):
        """Temporarily sets the storefront for API calls if a country code is provided."""
        if not country_code:
            return

        country_code_upper = country_code.upper()
        if self.apple_music_api and self.apple_music_api.storefront != country_code_upper:
            if self._debug:
                print(f"[Apple Music Debug] Switching storefront from {self.apple_music_api.storefront} to {country_code_upper}")
            
            # Update storefront for both Apple Music and iTunes APIs
            self.apple_music_api.storefront = country_code_upper
            if self.itunes_api:
                # gamdl's ItunesApi seems to expect lowercase for storefront
                self.itunes_api.storefront = country_code.lower()

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

    def _initialize_gamdl_components(self):
        print(f"[Apple Music] _initialize_gamdl_components CALLED. gamdl_downloader exists: {self.gamdl_downloader is not None}")
        if not self.gamdl_downloader: # Check for the main Downloader instance
            print("[Apple Music] Initializing gamdl_downloader...")
            try:
                orpheus_temp_path = Path(self.settings.get("temp_path", tempfile.gettempdir()))
                
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
                ffmpeg_path = main_settings.get("global", {}).get("advanced", {}).get("ffmpeg_path", "ffmpeg")
                mp4box_path = main_settings.get("global", {}).get("advanced", {}).get("mp4box_path", "MP4Box")
                mp4decrypt_path = main_settings.get("global", {}).get("advanced", {}).get("mp4decrypt_path", "mp4decrypt")
                
                # Helper to find and fix binaries
                def resolve_binary_path(binary_name, default_path):
                    # Always log to help debug path resolution issues
                    print(f"[Apple Music] resolve_binary_path called for: {binary_name}, default_path: {default_path}")
                    
                    # If the user specified a custom path (not the default name), verify it exists
                    if default_path != binary_name:
                        print(f"[Apple Music] Using custom path: {default_path}")
                        return default_path

                    # Search paths for local binaries
                    search_paths = []
                    
                    # 1. Always check Application Support on macOS first (most common user location)
                    if platform.system() == "Darwin":
                        app_support = os.path.expanduser("~/Library/Application Support/OrpheusDL GUI")
                        search_paths.append(os.path.join(app_support, binary_name))
                        print(f"[Apple Music] Added Application Support path: {os.path.join(app_support, binary_name)}")
                    
                    # 2. Check relative to executable (frozen app)
                    if getattr(sys, 'frozen', False):
                        app_dir = os.path.dirname(sys.executable)
                        search_paths.append(os.path.join(app_dir, binary_name))
                        print(f"[Apple Music] Added frozen app dir path: {os.path.join(app_dir, binary_name)}")
                        
                        # macOS Bundle logic
                        if platform.system() == "Darwin" and ".app/Contents/MacOS" in sys.executable:
                            bundle_dir = os.path.dirname(os.path.dirname(os.path.dirname(sys.executable)))
                            parent_dir = os.path.dirname(bundle_dir)
                            search_paths.append(os.path.join(bundle_dir, binary_name)) 
                            search_paths.append(os.path.join(parent_dir, binary_name))
                            print(f"[Apple Music] Added bundle paths: {os.path.join(bundle_dir, binary_name)}, {os.path.join(parent_dir, binary_name)}")

                    # 3. Check CWD
                    search_paths.append(os.path.join(os.getcwd(), binary_name))
                    print(f"[Apple Music] Added CWD path: {os.path.join(os.getcwd(), binary_name)}")
                    
                    print(f"[Apple Music] Searching {len(search_paths)} paths for {binary_name}...")

                    # Check all search paths
                    for path in search_paths:
                        exists = os.path.isfile(path)
                        print(f"[Apple Music] Checking: {path} -> {'EXISTS' if exists else 'NOT FOUND'}")
                        if exists:
                            # Ensure executable permissions
                            if not os.access(path, os.X_OK):
                                try:
                                    print(f"[Apple Music] Setting executable permissions for {path}")
                                    os.chmod(path, 0o755)
                                except Exception as e:
                                    print(f"[Apple Music Warning] Failed to set executable permissions for {path}: {e}")
                            
                            print(f"[Apple Music] FOUND {binary_name} at: {path}")
                            return path
                    
                    # Fallback to system PATH (shutil.which)
                    print(f"[Apple Music] Not found locally, trying shutil.which({binary_name})...")
                    system_path = shutil.which(binary_name)
                    if system_path:
                        print(f"[Apple Music] Found system {binary_name} at: {system_path}")
                        return system_path
                    
                    print(f"[Apple Music] WARNING: {binary_name} NOT FOUND anywhere!")
                    return binary_name

                # Resolve all binaries
                ffmpeg_name = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
                mp4box_name = "mp4box.exe" if platform.system() == "Windows" else "MP4Box"
                mp4decrypt_name = "mp4decrypt.exe" if platform.system() == "Windows" else "mp4decrypt"

                ffmpeg_path = resolve_binary_path(ffmpeg_name, ffmpeg_path)
                mp4box_path = resolve_binary_path(mp4box_name, mp4box_path)
                mp4decrypt_path = resolve_binary_path(mp4decrypt_name, mp4decrypt_path)

                print(f"[Apple Music] Final resolved paths:")
                print(f"[Apple Music]   ffmpeg: {ffmpeg_path}")
                print(f"[Apple Music]   mp4box: {mp4box_path}")
                print(f"[Apple Music]   mp4decrypt: {mp4decrypt_path}")
                
                self.gamdl_downloader = Downloader(
                    apple_music_api=self.apple_music_api,
                    itunes_api=self.itunes_api,
                    temp_path=orpheus_temp_path / "gamdl_temp",
                    silent=not self._debug,  # Only verbose if debug is enabled
                    ffmpeg_path=ffmpeg_path,
                    mp4box_path=mp4box_path,
                    mp4decrypt_path=mp4decrypt_path,
                )
                self.gamdl_downloader.set_cdm()
            except Exception as e:
                print(f"[Apple Music Error] Failed to initialize gamdl.downloader.Downloader: {e}")
                self.gamdl_downloader = None
                return # Can't proceed to DownloaderSong without gamdl_downloader

        if self.gamdl_downloader and not self.gamdl_downloader_song:
            try:
                self.gamdl_downloader_song = DownloaderSong(
                    downloader=self.gamdl_downloader, # Pass the correctly initialized Downloader instance
                    codec=self.song_codec
                )
            except Exception as e:
                print(f"[Apple Music Error] Failed to initialize gamdl.downloader_song.DownloaderSong: {e}")
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
            results = self.apple_music_api.search(query, types=search_type, limit=limit)
            
            search_results = []
            if search_type in results:
                for item in results[search_type]['data']:
                    # Extract artist information
                    artists = []
                    if query_type == DownloadTypeEnum.artist:
                        artists = [item['attributes']['name']]
                    elif 'artistName' in item['attributes']:
                        artists = [item['attributes']['artistName']]
                    elif 'curatorName' in item['attributes']:  # For playlists
                        artists = [item['attributes']['curatorName']]
                    
                    # Calculate duration for tracks
                    duration = None
                    if 'durationInMillis' in item['attributes']:
                        duration = item['attributes']['durationInMillis'] // 1000
                    
                    # Get additional info
                    additional = []
                    if 'contentRating' in item['attributes']:
                        additional.append(item['attributes']['contentRating'])
                    if 'trackCount' in item['attributes']:
                        additional.append(f"{item['attributes']['trackCount']} tracks")
                    
                    search_results.append(SearchResult(
                        result_id=item['id'],
                        name=item['attributes']['name'],
                        artists=artists,
                        duration=duration,
                        year=self._extract_year(item['attributes'].get('releaseDate')),
                        explicit=item['attributes'].get('contentRating') == 'explicit',
                        additional=additional,
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

    def get_track_info(self, track_id: str, quality_tier: QualityEnum, codec_options: CodecOptions, data: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[TrackInfo]:
        if self._debug:
            print(f"[Apple Music Debug] get_track_info called for track_id: {track_id}, quality_tier: {quality_tier.name}")

        # Extract country from either direct kwargs or the 'data' dict
        country = kwargs.get('country') or (data.get('country') if data else None)
        self._set_storefront(country)

        if not self.is_authenticated or not self.apple_music_api:
            if self._debug:
                print("[Apple Music Error] Not authenticated or API not initialized for get_track_info.")
            # Returning an error-state TrackInfo might be better than None if Orpheus expects an object
            return TrackInfo(name=f"Error: Not Authenticated for {track_id}", error="Not Authenticated", artists=["Unknown Artist"], album="", album_id=None, artist_id=None, duration=0, codec=CodecEnum.AAC, bitrate=0, sample_rate=0, release_year=None, cover_url=None, explicit=False, tags=Tags())

        try:
            # Check if we have raw_result from search - use it to avoid extra API call
            if 'raw_result' in kwargs and kwargs['raw_result']:
                track_api_data = kwargs['raw_result']
                if self._debug:
                    print(f"[Apple Music Debug] Using raw_result from search for track {track_id}")
            else:
                # Use data if provided (e.g., from album track list), otherwise fetch
                track_api_data = data if data and isinstance(data, dict) and data.get('id') == track_id else self.apple_music_api.get_song(track_id)

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
            if 'relationships' in track_api_data and 'artists' in track_api_data['relationships']:
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
            # Assume AAC 256kbps as a common display default. If user selected ALAC, reflect that.
            display_codec = CodecEnum.AAC
            display_bitrate = 256
            if self.song_codec == GamdlSongCodec.ALAC : # self.song_codec is GamdlSongCodec
                display_codec = CodecEnum.ALAC
                display_bitrate = 0 # Placeholder for lossless
            
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
            if 'relationships' in track_api_data and 'albums' in track_api_data['relationships']:
                album_data_rels = track_api_data['relationships']['albums'].get('data')
                if album_data_rels and len(album_data_rels) > 0:
                    album_id_from_rels = album_data_rels[0].get('id')

            # If album_id is not found from the relationships (e.g., from a search result),
            # fetch the full song data to get it.
            if not album_id_from_rels or not artist_id_from_rels:
                if self._debug:
                    print(f"[Apple Music Debug] Album or Artist ID not in relationships for track {track_id}. Fetching full song data.")
                full_track_data = self.apple_music_api.get_song(track_id)
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
                    
                    if self._debug:
                        print(f"[Apple Music Debug] Found IDs after full fetch: Album={album_id_from_rels}, Artist={artist_id_from_rels}")

            return TrackInfo(
                name=name,
                album=album_name,
                album_id=str(album_id_from_rels) if album_id_from_rels else None,
                artists=artists_list,
                artist_id=str(artist_id_from_rels) if artist_id_from_rels else None,
                duration=duration_sec,
                codec=display_codec,
                bitrate=display_bitrate,
                sample_rate=44100, # Typically for Apple Music
                release_year=year,
                cover_url=cover_url,
                explicit=explicit,
                tags=tags_obj,
                # download_extra_kwargs can store the raw API response if downloader needs more later
                download_extra_kwargs={'api_response': track_api_data, 'source_quality_tier': quality_tier.name}
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

    def get_track_download(self, track_id: str, quality_tier: QualityEnum, **kwargs) -> Optional[TrackDownloadInfo]:
        if self._debug:
            print(f"[Apple Music Debug] get_track_download called for track_id: {track_id}, quality_tier: {quality_tier.name}")

        # Reset rich tagging flag for each new download
        self._using_rich_tagging = False
        
        # Detect context by examining the kwargs and call stack
        import inspect

        # Default to single track indentation
        indent_spaces = "        "  # 8 spaces for single tracks

        try:
            # Check if we're in an album context by looking for album-specific indicators
            # This is more reliable than trying to detect artist context

            # Check for album context by looking for multi-track album indicators
            is_album_context = False

            # First check kwargs for album context
            if 'extra_kwargs' in kwargs and kwargs['extra_kwargs']:
                extra_kwargs = kwargs['extra_kwargs']
                if 'album_id' in extra_kwargs or 'album_name' in extra_kwargs:
                    is_album_context = True

            # If not found in kwargs, check call stack for album download functions
            if not is_album_context:
                stack = inspect.stack()
                for frame_info in stack:
                    function_name = frame_info.function
                    frame_locals = frame_info.frame.f_locals

                    # Look for album download indicators, but be more specific
                    if function_name == 'download_album':
                        # Check if this is a multi-track album by looking for track count
                        if 'album_info' in frame_locals:
                            album_info = frame_locals['album_info']
                            if self._debug:
                                print(f"[Apple Music Debug] Found album_info: {type(album_info)}")
                                if hasattr(album_info, '__dict__'):
                                    print(f"[Apple Music Debug] album_info attributes: {list(album_info.__dict__.keys())}")
                            # Only consider it an album context if it has multiple tracks
                            # Check for tracks attribute (list of tracks)
                            if hasattr(album_info, 'tracks') and album_info.tracks and len(album_info.tracks) > 1:
                                is_album_context = True
                                if self._debug:
                                    print(f"[Apple Music Debug] Multi-track album detected: {len(album_info.tracks)} tracks")
                                break
                            elif hasattr(album_info, 'tracks'):
                                if self._debug:
                                    track_count = len(album_info.tracks) if album_info.tracks else 0
                                    print(f"[Apple Music Debug] Single-track album detected: {track_count} tracks")
                            # Fallback: check for track_count attribute
                            elif hasattr(album_info, 'track_count') and album_info.track_count > 1:
                                is_album_context = True
                                if self._debug:
                                    print(f"[Apple Music Debug] Multi-track album detected (track_count): {album_info.track_count} tracks")
                                break
                            elif hasattr(album_info, 'track_count'):
                                if self._debug:
                                    print(f"[Apple Music Debug] Single-track album detected (track_count): {album_info.track_count} tracks")
                        else:
                            # If we can't determine track count, assume it's an album
                            is_album_context = True
                            if self._debug:
                                print(f"[Apple Music Debug] download_album function found, assuming album context")
                            break
                    elif 'album_info' in frame_locals:
                        album_info = frame_locals['album_info']
                        if self._debug:
                            print(f"[Apple Music Debug] Found album_info in frame: {type(album_info)}")
                        # Only consider it an album context if it has multiple tracks
                        # Check for tracks attribute (list of tracks)
                        if hasattr(album_info, 'tracks') and album_info.tracks and len(album_info.tracks) > 1:
                            is_album_context = True
                            if self._debug:
                                print(f"[Apple Music Debug] Multi-track album detected in frame: {len(album_info.tracks)} tracks")
                            break
                        # Fallback: check for track_count attribute
                        elif hasattr(album_info, 'track_count') and album_info.track_count > 1:
                            is_album_context = True
                            if self._debug:
                                print(f"[Apple Music Debug] Multi-track album detected in frame (track_count): {album_info.track_count} tracks")
                            break

            # Determine indentation based on context
            if self._debug:
                print(f"[Apple Music Debug] Context detection for track {track_id}:")
                print(f"[Apple Music Debug] is_album_context: {is_album_context}")

            if is_album_context:
                # Check if we're also in an artist context (for nested album in artist)
                is_artist_context = False
                stack = inspect.stack()
                for frame_info in stack:
                    function_name = frame_info.function
                    frame_locals = frame_info.frame.f_locals

                    if (function_name == 'download_artist' or
                        'artist_id' in frame_locals):
                        is_artist_context = True
                        break

                if self._debug:
                    print(f"[Apple Music Debug] is_artist_context: {is_artist_context}")

                if is_artist_context:
                    # Album track within artist download - use 8 spaces to match OrpheusDL's indentation system
                    indent_spaces = "        "  # 8 spaces (matches OrpheusDL track content indentation)
                    if self._debug:
                        print(f"[Apple Music Debug] Using 8 spaces (album within artist)")
                else:
                    # Regular album track - use 8 spaces to match OrpheusDL's indentation system
                    indent_spaces = "        "  # 8 spaces (matches OrpheusDL track content indentation)
                    if self._debug:
                        print(f"[Apple Music Debug] Using 8 spaces (regular album)")
            else:
                # Check for playlist context
                is_playlist_context = False
                stack = inspect.stack()
                for frame_info in stack:
                    function_name = frame_info.function
                    if function_name == 'download_playlist':
                        is_playlist_context = True
                        if self._debug:
                            print(f"[Apple Music Debug] Detected playlist context")
                        break

                if is_playlist_context:
                    # Playlist track - use same indentation as other track details (8 spaces)
                    indent_spaces = "        "  # 8 spaces
                    if self._debug:
                        print(f"[Apple Music Debug] Using 8 spaces (playlist track)")
                else:
                    # Single track (standalone or within artist)
                    indent_spaces = "        "  # 8 spaces
                    if self._debug:
                        print(f"[Apple Music Debug] Using 8 spaces (single track)")

        except:
            # If detection fails, use default single track indentation
            indent_spaces = "        "  # 8 spaces

        print(f"[Apple Music] Authentication check: is_authenticated={self.is_authenticated}")
        if not self.is_authenticated:
            raise AuthenticationError('"cookies.txt" not found, invalid, or expired.')

        # Ensure gamdl components are initialized (downloader and downloader_song)
        print(f"[Apple Music] Checking gamdl components: downloader_song={self.gamdl_downloader_song is not None}, downloader={self.gamdl_downloader is not None}")
        if not self.gamdl_downloader_song or not self.gamdl_downloader:
            print("[Apple Music] gamdl components not initialized, calling _initialize_gamdl_components...")
            self._initialize_gamdl_components() # This method should set up self.gamdl_downloader and self.gamdl_downloader_song
            print(f"[Apple Music] After init: downloader_song={self.gamdl_downloader_song is not None}, downloader={self.gamdl_downloader is not None}")
            if not self.gamdl_downloader_song or not self.gamdl_downloader:
                print("[Apple Music Error] gamdl components failed to initialize.")
                raise DownloadError("Apple Music: gamdl components could not be initialized for download.")
        
        if self._debug:
            print(f"[Apple Music Debug] Using gamdl song_codec: {self.gamdl_downloader_song.codec.name if hasattr(self.gamdl_downloader_song.codec, 'name') else self.gamdl_downloader_song.codec}")

        try:
            # 1. Get full track metadata (primarily for stream info URL and PSSH)
            # gamdl's get_song is robust for this, even if some data isn't directly used by Orpheus tags later
            if self._debug:
                print(f"[Apple Music Debug] Fetching full track metadata for {track_id} using apple_music_api.get_song...")
            
            with suppress_gamdl_debug():
                gamdl_track_metadata_full = self.apple_music_api.get_song(track_id)
            if not gamdl_track_metadata_full:
                print(f"[Apple Music Error] Failed to get full metadata for track {track_id} from AppleMusicApi.")
                raise DownloadError(f"Apple Music: Failed to get full metadata for track {track_id}")

            # 1.5. Get webplayback data (CRITICAL for correct PSSH extraction)
            # This is what standalone gamdl does and OrpheusDL was missing!
            if self._debug:
                print(f"[Apple Music Debug] Getting webplayback data for track {track_id} using apple_music_api.get_webplayback...")
            with suppress_gamdl_debug():
                webplayback_data = self.apple_music_api.get_webplayback(track_id)
            if not webplayback_data:
                print(f"[Apple Music Error] Failed to get webplayback data for track {track_id} from AppleMusicApi.")
                raise DownloadError(f"Apple Music: Failed to get webplayback data for track {track_id}")

            # 2. Get stream information from gamdl (contains stream_url, PSSH, etc.)
            # Use the original track metadata for get_stream_info, which is what standalone gamdl does
            if self._debug:
                print(f"[Apple Music Debug] Getting stream info for track {track_id} using gamdl_downloader_song.get_stream_info...")
            
            # Handle legacy vs regular codecs like standalone gamdl does
            if self.gamdl_downloader_song.codec in LEGACY_CODECS:
                # For legacy codecs, use DownloaderSongLegacy.get_stream_info() with webplayback data
                if self._debug:
                    print(f"[Apple Music Debug] Using legacy codec path for {self.gamdl_downloader_song.codec.name}")
                legacy_downloader_song = DownloaderSongLegacy(
                    downloader=self.gamdl_downloader,
                    codec=self.gamdl_downloader_song.codec
                )
                gamdl_stream_info = legacy_downloader_song.get_stream_info(webplayback_data)
            else:
                # For regular codecs, use track metadata
                if self._debug:
                    print(f"[Apple Music Debug] Using regular codec path for {self.gamdl_downloader_song.codec.name}")
                gamdl_stream_info = self.gamdl_downloader_song.get_stream_info(gamdl_track_metadata_full)
            
            if not gamdl_stream_info or not gamdl_stream_info.stream_url:
                print(f"[Apple Music Error] Failed to get stream_info or stream_url from gamdl for track {track_id}.")
                raise DownloadError(f"Apple Music: Failed to get stream_info or stream_url from gamdl for track {track_id}")
            if self._debug:
                print(f"[Apple Music Debug] Obtained stream_url: {gamdl_stream_info.stream_url}")
            if gamdl_stream_info.widevine_pssh:
                if self._debug:
                    print(f"[Apple Music Debug] Obtained PSSH (first 10 chars): {gamdl_stream_info.widevine_pssh[:10]}...")
            else:
                if self._debug:
                    print("[Apple Music Warning] No Widevine PSSH found in stream_info.")
                # Depending on content type (e.g. some ALAC might not be DRM'd, or DRM type differs), this might be an issue or not.
                # For typical AAC from Apple Music, PSSH is expected.

            # 3. Define encrypted file path (using gamdl's path generation for consistency)
            # temp_path for gamdl_downloader was set during _initialize_gamdl_components
            encrypted_path = self.gamdl_downloader_song.get_encrypted_path(track_id) # get_encrypted_path is method of DownloaderSong
            encrypted_path.parent.mkdir(parents=True, exist_ok=True) # Ensure temp directory exists
            if self._debug:
                print(f"[Apple Music Debug] Encrypted file will be at: {encrypted_path}")

            # 4. Download the encrypted stream using gamdl's Downloader instance
            # The actual download (e.g. YTDLP) happens inside gamdl_downloader.download()
            # self.gamdl_downloader.silent is False, so yt-dlp should be verbose.
            print(f"{indent_spaces}Downloading encrypted stream...")
            self.gamdl_downloader.download(encrypted_path, gamdl_stream_info.stream_url)
            
            # --- DEBUG LOGGING FOR ENCRYPTED FILE ---
            if self._debug:
                print(f"[Orpheus DEBUG] Post-download check for encrypted file: {encrypted_path}")
            if encrypted_path.exists():
                file_size = encrypted_path.stat().st_size
                if self._debug:
                    print(f"[Orpheus DEBUG] Encrypted file exists. Size: {file_size} bytes")
                if file_size == 0:
                    print("[Apple Music Error] Encrypted file is 0 bytes. Download failed.")
            else:
                print("[Apple Music Error] Encrypted file was not created after download attempt.")
                raise DownloadError(f"Apple Music: Encrypted file not created at {encrypted_path}")
            # --- END DEBUG LOGGING ---

            # 5. Get decryption key
            print(f"{indent_spaces}Getting decryption key...")
            
            # Handle legacy vs regular codec decryption and remuxing - they have different workflows
            if self.gamdl_downloader_song.codec in LEGACY_CODECS:
                # For legacy codecs, get decryption key using legacy method
                if self._debug:
                    print(f"[Apple Music Debug] Using legacy decryption key method for {self.gamdl_downloader_song.codec.name}")
                legacy_downloader_song = DownloaderSongLegacy(
                    downloader=self.gamdl_downloader,
                    codec=self.gamdl_downloader_song.codec
                )
                with suppress_gamdl_debug():
                    decryption_key = legacy_downloader_song.get_decryption_key(
                        gamdl_stream_info.widevine_pssh, track_id
                    )
                
                # For legacy codecs, the remux method handles both decryption and remuxing in one step
                if self._debug:
                    print(f"[Apple Music Debug] Using legacy codec flow for {self.gamdl_downloader_song.codec.name}")
                
                # Skip manual decryption for legacy codecs - remux handles it
                remuxed_path = self.gamdl_downloader_song.get_remuxed_path(track_id)
                if self._debug:
                    print(f"[Apple Music Debug] Remuxed file will be at: {remuxed_path}")
                    print(f"[Apple Music Debug] Legacy remux will handle both decryption and remuxing")
                
                # Legacy remux signature: (encrypted_path, decrypted_path, remuxed_path, decryption_key)
                # For FFMPEG mode, it uses encrypted_path and decryption_key directly
                # For MP4BOX mode, it first decrypts to decrypted_path, then remuxes
                print(f"{indent_spaces}Processing with legacy remux...")
                decrypted_path = self.gamdl_downloader_song.get_decrypted_path(track_id)
                legacy_downloader_song.remux(
                    encrypted_path, decrypted_path, remuxed_path, decryption_key
                )
                if self._debug:
                    print(f"[Apple Music Debug] Legacy remux call completed.")
                
                # Check remuxed file
                if remuxed_path.exists() and remuxed_path.stat().st_size > 1048576:  # At least 1MB for a valid song
                    if self._debug:
                        print(f"[Apple Music Success] Remuxed file created successfully: {remuxed_path}, Size: {remuxed_path.stat().st_size} bytes")
                    
                    # Apply gamdl's rich tagging system to preserve Apple Music metadata
                    try:
                        print(f"{indent_spaces}Applying Apple Music metadata...")
                        
                        # Extract rich metadata using gamdl's get_tags method
                        if self.gamdl_downloader_song.codec in LEGACY_CODECS:
                            # For legacy codecs, get tags from webplayback data
                            rich_tags = legacy_downloader_song.get_tags(webplayback_data, None)  # No lyrics for now
                        else:
                            # For regular codecs, get tags from webplayback data
                            rich_tags = self.gamdl_downloader_song.get_tags(webplayback_data, None)  # No lyrics for now
                        
                        if self._debug:
                            print(f"[Apple Music Metadata] Extracted {len(rich_tags)} rich metadata fields")
                        
                        # Apply rich metadata using gamdl's apply_tags method
                        # Get cover URL from track metadata for artwork
                        cover_url = None
                        if 'attributes' in gamdl_track_metadata_full and 'artwork' in gamdl_track_metadata_full['attributes']:
                            artwork_template = gamdl_track_metadata_full['attributes']['artwork'].get('url')
                            if artwork_template:
                                cover_url = artwork_template.replace('{w}x{h}bb.jpg', '1400x1400bb.jpg')
                        
                        # Apply the rich tags to the remuxed file
                        self.gamdl_downloader.apply_tags(
                            remuxed_path,
                            rich_tags,
                            cover_url
                        )
                        
                        if self._debug:
                            print(f"[Apple Music Metadata] Successfully applied rich Apple Music metadata")
                        
                        # Set flag to prevent OrpheusDL from overwriting rich metadata
                        self._using_rich_tagging = True
                        
                        # Return as TEMP_FILE_PATH since DIRECT_FILE_PATH doesn't exist
                        # The rich metadata has been applied, so OrpheusDL should preserve it
                        return TrackDownloadInfo(
                            download_type=DownloadEnum.TEMP_FILE_PATH,
                            temp_file_path=str(remuxed_path)
                        )
                        
                    except Exception as tagging_error:
                        if self._debug:
                            print(f"[Apple Music Metadata] Rich tagging failed: {tagging_error}")
                        # Fall back to OrpheusDL tagging
                        print(f"{indent_spaces}Rich tagging failed, using OrpheusDL tagging")
                    
                    # Fallback to temp file for OrpheusDL tagging if rich tagging failed
                    return TrackDownloadInfo(
                        download_type=DownloadEnum.TEMP_FILE_PATH,
                        temp_file_path=str(remuxed_path)
                    )
            else:
                # For regular codecs, get decryption key using regular method
                if self._debug:
                    print(f"[Apple Music Debug] Using regular decryption key method for {self.gamdl_downloader_song.codec.name}")
                processed_pssh_b64 = gamdl_stream_info.widevine_pssh
                if processed_pssh_b64.startswith("data:"):
                    try:
                        # Expected format: data:[<mediatype>][;base64],<data>
                        # We need to get the part after "base64,"
                        processed_pssh_b64 = processed_pssh_b64.split(';base64,')[1]
                        if self._debug:
                            print(f"[Apple Music Debug] Extracted base64 PSSH from data URI (first 10 chars): {processed_pssh_b64[:10]}...")
                    except IndexError:
                        print(f"[Apple Music Error] Could not parse base64 PSSH from data URI: {gamdl_stream_info.widevine_pssh}")
                        raise DownloadError("Apple Music: Malformed PSSH data URI.")

                with suppress_gamdl_debug():
                    decryption_key = self.gamdl_downloader.get_decryption_key( # get_decryption_key is method of Downloader
                        processed_pssh_b64, # Pass the processed PSSH
                        track_id,
                        gamdl_stream_info.stream_url # Pass the HLS manifest URL
                    )

                # --- DEBUG LOGGING FOR DECRYPTION KEY ---
                if self._debug:
                    print(f"[Orpheus DEBUG] Obtained decryption key: {'*' * len(decryption_key) if decryption_key else 'None'}") # Avoid logging full key
                    # Accessing DEFAULT_DECRYPTION_KEY from DownloaderSong class
                    if decryption_key == DownloaderSong.DEFAULT_DECRYPTION_KEY:
                        print(f"[Orpheus DEBUG] WARNING: Using gamdl's default (likely incorrect) decryption key! Real key acquisition failed.")
                    elif not decryption_key:
                         print(f"[Orpheus DEBUG] ERROR: Decryption key is None or empty. Cannot decrypt.")
                         raise DownloadError("Apple Music: Failed to obtain a valid decryption key.")
                # --- END DEBUG LOGGING ---

                # 6. Define decrypted file path for regular codecs
                decrypted_path = self.gamdl_downloader_song.get_decrypted_path(track_id)
                if self._debug:
                    print(f"[Apple Music Debug] Decrypted file will be at: {decrypted_path}")

                # 7. Decrypt the file using gamdl's DownloaderSong instance
                print(f"{indent_spaces}Decrypting file...")
                self.gamdl_downloader_song.decrypt(encrypted_path, decrypted_path, decryption_key)
                if self._debug:
                    print(f"[Apple Music Debug] Decryption call completed.")

                # 8. Remux the decrypted file
                remuxed_path = self.gamdl_downloader_song.get_remuxed_path(track_id)
                if self._debug:
                    print(f"[Apple Music Debug] Remuxed file will be at: {remuxed_path}")
                    print(f"[Apple Music Debug] Using regular remux method for {self.gamdl_downloader_song.codec.name}")
                print(f"{indent_spaces}Remuxing audio file...")
                self.gamdl_downloader_song.remux(decrypted_path, remuxed_path, gamdl_stream_info.codec)
                if self._debug:
                    print(f"[Apple Music Debug] Remux call completed.")

                # 9. Check remuxed file and return TrackDownloadInfo
                if remuxed_path.exists() and remuxed_path.stat().st_size > 1048576:  # At least 1MB for a valid song
                    if self._debug:
                        print(f"[Apple Music Success] Remuxed file created successfully: {remuxed_path}, Size: {remuxed_path.stat().st_size} bytes")
                    
                    # Apply gamdl's rich tagging system to preserve Apple Music metadata
                    try:
                        print(f"{indent_spaces}Applying Apple Music metadata...")
                        
                        # Extract rich metadata using gamdl's get_tags method
                        if self.gamdl_downloader_song.codec in LEGACY_CODECS:
                            # For legacy codecs, get tags from webplayback data
                            rich_tags = legacy_downloader_song.get_tags(webplayback_data, None)  # No lyrics for now
                        else:
                            # For regular codecs, get tags from webplayback data
                            rich_tags = self.gamdl_downloader_song.get_tags(webplayback_data, None)  # No lyrics for now
                        
                        if self._debug:
                            print(f"[Apple Music Metadata] Extracted {len(rich_tags)} rich metadata fields")
                        
                        # Apply rich metadata using gamdl's apply_tags method
                        # Get cover URL from track metadata for artwork
                        cover_url = None
                        if 'attributes' in gamdl_track_metadata_full and 'artwork' in gamdl_track_metadata_full['attributes']:
                            artwork_template = gamdl_track_metadata_full['attributes']['artwork'].get('url')
                            if artwork_template:
                                cover_url = artwork_template.replace('{w}x{h}bb.jpg', '1400x1400bb.jpg')
                        
                        # Apply the rich tags to the remuxed file
                        self.gamdl_downloader.apply_tags(
                            remuxed_path,
                            rich_tags,
                            cover_url
                        )
                        
                        if self._debug:
                            print(f"[Apple Music Metadata] Successfully applied rich Apple Music metadata")
                        
                        # Set flag to prevent OrpheusDL from overwriting rich metadata
                        self._using_rich_tagging = True
                        
                        # Return as TEMP_FILE_PATH since DIRECT_FILE_PATH doesn't exist
                        # The rich metadata has been applied, so OrpheusDL should preserve it
                        return TrackDownloadInfo(
                            download_type=DownloadEnum.TEMP_FILE_PATH,
                            temp_file_path=str(remuxed_path)
                        )
                        
                    except Exception as tagging_error:
                        if self._debug:
                            print(f"[Apple Music Metadata] Rich tagging failed: {tagging_error}")
                        # Fall back to OrpheusDL tagging
                        print(f"{indent_spaces}Rich tagging failed, using OrpheusDL tagging")
                    
                    # Fallback to temp file for OrpheusDL tagging if rich tagging failed
                    return TrackDownloadInfo(
                        download_type=DownloadEnum.TEMP_FILE_PATH,
                        temp_file_path=str(remuxed_path)
                    )
                else:
                    file_size = remuxed_path.stat().st_size if remuxed_path.exists() else 0
                    if self._debug:
                        print(f"[Apple Music Warning] Remuxed file {remuxed_path} is too small ({file_size} bytes) or missing after legacy remux attempt.")
                    
                    # For legacy codecs, manually decrypt as fallback since legacy remux failed
                    print(f"{indent_spaces}Remux failed, trying manual decryption fallback...")
                    
                    # Check if encrypted file is still valid
                    if encrypted_path.exists() and encrypted_path.stat().st_size > 1048576:
                        try:
                            # Manually decrypt using the legacy downloader_song decrypt method
                            legacy_downloader_song.decrypt(encrypted_path, decrypted_path, decryption_key)
                            if self._debug:
                                print(f"[Apple Music Fallback] Manual decryption completed.")
                            
                            # Check if decrypted file was created successfully
                            if decrypted_path.exists() and decrypted_path.stat().st_size > 1048576:
                                if self._debug:
                                    print(f"[Apple Music Fallback] Manual decryption successful. Now attempting to remux decrypted file...")
                                
                                # Try to remux the decrypted file using FFmpeg directly
                                try:
                                    import subprocess
                                    ffmpeg_path = self.gamdl_downloader.ffmpeg_path_full
                                    if not ffmpeg_path:
                                        ffmpeg_path = "ffmpeg"  # Fallback to system PATH
                                    
                                    # Create a final remuxed path
                                    final_remuxed_path = decrypted_path.parent / f"{track_id}_final_remuxed.m4a"
                                    
                                    # Use FFmpeg to remux the decrypted HLS fragment into proper MP4
                                    cmd = [
                                        ffmpeg_path,
                                        "-i", str(decrypted_path),
                                        "-c", "copy",  # Copy streams without re-encoding
                                        "-movflags", "+faststart",  # Optimize for streaming
                                        "-y",  # Overwrite output file
                                        str(final_remuxed_path)
                                    ]
                                    
                                    if self._debug:
                                        print(f"[Apple Music Fallback] Running FFmpeg remux: {' '.join(cmd[:3])} ... {final_remuxed_path}")
                                    result = subprocess.run(cmd, capture_output=True, text=True, **self.gamdl_downloader.subprocess_additional_args)
                                    
                                    if result.returncode == 0 and final_remuxed_path.exists() and final_remuxed_path.stat().st_size > 1048576:
                                        if self._debug:
                                            print(f"[Apple Music Fallback] FFmpeg remux successful: {final_remuxed_path}, Size: {final_remuxed_path.stat().st_size} bytes")
                                        return TrackDownloadInfo(
                                            download_type=DownloadEnum.TEMP_FILE_PATH,
                                            temp_file_path=str(final_remuxed_path)
                                        )
                                    else:
                                        if self._debug:
                                            print(f"[Apple Music Fallback] FFmpeg remux failed. Return code: {result.returncode}")
                                        if result.stderr and self._debug:
                                            print(f"[Apple Music Fallback] FFmpeg error: {result.stderr}")
                                        # Fall back to using decrypted file directly
                                        if self._debug:
                                            print(f"[Apple Music Fallback] Using decrypted file directly (may have playback issues): {decrypted_path}, Size: {decrypted_path.stat().st_size} bytes")
                                        return TrackDownloadInfo(
                                            download_type=DownloadEnum.TEMP_FILE_PATH,
                                            temp_file_path=str(decrypted_path)
                                        )
                                        
                                except Exception as remux_error:
                                    if self._debug:
                                        print(f"[Apple Music Fallback] FFmpeg remux failed with error: {remux_error}")
                                    # Fall back to using decrypted file directly
                                    if self._debug:
                                        print(f"[Apple Music Fallback] Using decrypted file directly (may have playback issues): {decrypted_path}, Size: {decrypted_path.stat().st_size} bytes")
                                    return TrackDownloadInfo(
                                        download_type=DownloadEnum.TEMP_FILE_PATH,
                                        temp_file_path=str(decrypted_path)
                                    )
                            else:
                                if self._debug:
                                    print(f"[Apple Music Error] Manual decryption failed - decrypted file too small or missing.")
                        except Exception as decrypt_error:
                            if self._debug:
                                print(f"[Apple Music Error] Manual decryption failed with error: {decrypt_error}")
                    
                    print(f"[Apple Music Error] All fallback attempts failed for legacy codec.")
                    raise DownloadError(f"Apple Music: All decryption and remux attempts failed for legacy codec.")

        except AuthenticationError: # Re-raise auth errors
            raise
        except DownloadError as de: # Re-raise download errors with context
            if self._debug:
                print(f"[Apple Music DownloadError] {de}")
            raise
        except Exception as e:
            error_str = str(e)
            # Check for specific "song unavailable" error from Apple Music
            if '"failureType":"3076"' in error_str:
                customer_message = "This song is unavailable." # Default message
                try:
                    # Attempt to parse the JSON part of the error string for a better message
                    json_str = error_str[error_str.find('{'):error_str.rfind('}')+1]
                    error_json = json.loads(json_str)
                    message_from_api = error_json.get('customerMessage') or error_json.get('dialog', {}).get('message')
                    if message_from_api:
                        # Replace Apple's message with our shorter version
                        if "currently unavailable" in message_from_api.lower():
                            customer_message = "This song is unavailable."
                        else:
                            customer_message = message_from_api
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass # Fallback to default message if parsing fails
                raise TrackUnavailableError(customer_message) from e

            if '"failureType":"2002"' in error_str or "Your session has ended" in error_str:
                raise DownloadError('"cookies.txt" not found, invalid, or expired.')
            
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
                print(f"[Apple Music Error] An unexpected error occurred in get_track_download for track {track_id}: {e}")
                print(traceback.format_exc())
            raise DownloadError(f"Apple Music: Unexpected error during download of track {track_id} - {error_msg}")

    def get_album_info(self, album_id: str, **kwargs) -> Optional[AlbumInfo]:
        """Get album information"""
        try:
            # Extract country from kwargs and set storefront
            country = kwargs.get('data', {}).get('country')
            self._set_storefront(country)

            album_data = self.apple_music_api.get_album(album_id)
            attrs = album_data['attributes']
            
            # Extract track IDs
            track_ids = []
            if 'relationships' in album_data and 'tracks' in album_data['relationships']:
                track_ids = [track['id'] for track in album_data['relationships']['tracks']['data']]
            
            return AlbumInfo(
                name=attrs['name'],
                artist=attrs.get('artistName', ''),
                artist_id='',
                cover_url=self._get_cover_url(attrs.get('artwork', {}).get('url')),
                release_year=self._extract_year(attrs.get('releaseDate')),
                tracks=track_ids,
                track_extra_kwargs={'country': country}
            )
            
        except Exception as e:
            raise self.exception(f"Failed to get album info: {e}")

    def get_playlist_info(self, playlist_id, data: dict = None, **kwargs):
        """Get playlist information"""
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
                    playlist_data = self.apple_music_api.get_playlist(playlist_id)
            else:
                playlist_data = self.apple_music_api.get_playlist(playlist_id)
            
            attrs = playlist_data['attributes']
            
            # Extract track IDs
            track_ids = []
            if 'relationships' in playlist_data and 'tracks' in playlist_data['relationships']:
                track_ids = [track['id'] for track in playlist_data['relationships']['tracks']['data']]
            
            return PlaylistInfo(
                name=attrs.get('name', 'Unknown Playlist'),
                creator=attrs.get('curatorName', 'Unknown Creator'),
                release_year=self._extract_year(attrs.get('lastModifiedDate')),
                tracks=track_ids,
                cover_url=self._get_cover_url(attrs.get('artwork', {}).get('url')),
                track_extra_kwargs={'data': {}}
            )
            
        except Exception as e:
            raise self.exception(f"Failed to get playlist info: {e}")

    def get_artist_info(self, artist_id, get_credited_albums=True, data: dict = None, **kwargs):
        """Get artist information"""
        # Extract country from kwargs and set storefront
        country = kwargs.get('country') or (data.get('country') if data else None)
        self._set_storefront(country)
        
        # Reverting to the call without 'include' which seems to be more robust
        artist_data = self.apple_music_api.get_artist(artist_id)
        
        # Defensive check for API response structure. Expecting a dict.
        if not artist_data or not isinstance(artist_data, dict) or 'attributes' not in artist_data:
            if self._debug:
                print(f"[Apple Music Debug] Unexpected artist data response for ID {artist_id} on storefront '{self.apple_music_api.storefront}': {artist_data}")
            raise self.exception(f"No data returned for artist ID {artist_id}. They may not be available on the '{self.apple_music_api.storefront}' storefront.")

        attrs = artist_data['attributes']
        
        # Extract album IDs safely from relationships on the main object
        albums = []
        if 'relationships' in artist_data and 'albums' in artist_data['relationships']:
            albums_data = artist_data['relationships']['albums'].get('data', [])
            albums = [album['id'] for album in albums_data if 'id' in album]
        
        return ArtistInfo(
            name=attrs.get('name', 'Unknown Artist'),
            artist_id=artist_id,
            albums=albums,
            album_extra_kwargs={'country': country}
        )

    def _get_cover_url(self, artwork_template):
        """Build a full cover URL from a template"""
        if not artwork_template:
            return None
        # Replace template with high resolution
        return artwork_template.replace('{w}x{h}bb.jpg', '1400x1400bb.jpg') 
