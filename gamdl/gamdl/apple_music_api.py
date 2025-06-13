from __future__ import annotations

import functools
import re
import time
import typing
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import requests

from .utils import raise_response_exception


class AppleMusicApi:
    APPLE_MUSIC_HOMEPAGE_URL = "https://beta.music.apple.com"
    AMP_API_URL = "https://amp-api.music.apple.com"
    WEBPLAYBACK_API_URL = (
        "https://play.itunes.apple.com/WebObjects/MZPlay.woa/wa/webPlayback"
    )
    LICENSE_API_URL = "https://play.itunes.apple.com/WebObjects/MZPlay.woa/wa/acquireWebPlaybackLicense"
    WAIT_TIME = 2

    def __init__(
        self,
        cookies_path: Path | None = Path("./cookies.txt"),
        storefront: None | str = None,
        language: str = "en-US",
    ):
        self.cookies_path = cookies_path
        self.storefront = storefront
        self.language = language
        self._set_session()

    def _set_session(self):
        self.session = requests.Session()
        media_user_token_found = False
        if self.cookies_path and self.cookies_path.exists():
            cookies = MozillaCookieJar(self.cookies_path)
            cookies.load(ignore_discard=True, ignore_expires=True)
            self.session.cookies.update(cookies)
            media_user_token = self.session.cookies.get_dict().get("media-user-token")
            if media_user_token:
                media_user_token_found = True
                print("[gamdl AppleMusicApi DEBUG] Media-User-Token found in cookies.")
            else:
                print("[gamdl AppleMusicApi WARNING] cookies.txt loaded, but Media-User-Token not found within.")
        else:
            print(f"[gamdl AppleMusicApi WARNING] cookies_path is None or file does not exist: {self.cookies_path}. Media-User-Token will be empty.")
            media_user_token = ""
        
        if not media_user_token_found and self.cookies_path:
             print("[gamdl AppleMusicApi ERROR] media-user-token not found in provided cookies. This is critical for authenticated requests.")

        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:95.0) Gecko/20100101 Firefox/95.0",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "content-type": "application/json",
                "Media-User-Token": media_user_token,
                "x-apple-renewal": "true",
                "DNT": "1",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
                "origin": self.APPLE_MUSIC_HOMEPAGE_URL,
            }
        )
        home_page = self.session.get(self.APPLE_MUSIC_HOMEPAGE_URL).text
        index_js_uri = re.search(
            r"/(assets/index-legacy-[^/]+\.js)",
            home_page,
        ).group(1)
        index_js_page = self.session.get(
            f"{self.APPLE_MUSIC_HOMEPAGE_URL}/{index_js_uri}"
        ).text
        token = re.search('(?=eyJh)(.*?)(?=")', index_js_page).group(1)
        self.session.headers.update({"authorization": f"Bearer {token}"})
        self.session.params = {"l": self.language}
        self._set_storefront()

    def _check_amp_api_response(self, response: requests.Response):
        try:
            response.raise_for_status()
            response_dict = response.json()
            assert response_dict.get("data") or response_dict.get("results") is not None
        except (
            requests.HTTPError,
            requests.exceptions.JSONDecodeError,
            AssertionError,
        ):
            raise_response_exception(response)

    def _set_storefront(self):
        if self.cookies_path:
            self.storefront = (
                self.session.cookies.get_dict().get("itua")
                or self.get_user_storefront()["id"]
            )
        else:
            self.storefront = self.storefront or "us"

    def get_user_storefront(
        self,
    ) -> dict:
        response = self.session.get(f"{self.AMP_API_URL}/v1/me/storefront")
        self._check_amp_api_response(response)
        return response.json()["data"][0]

    def get_artist(
        self,
        artist_id: str,
        include: str = "albums,music-videos",
        limit: int = 100,
        fetch_all: bool = True,
    ) -> dict:
        response = self.session.get(
            f"{self.AMP_API_URL}/v1/catalog/{self.storefront}/artists/{artist_id}",
            params={
                "include": include,
                **{f"limit[{_include}]": limit for _include in include.split(",")},
            },
        )
        self._check_amp_api_response(response)
        artist = response.json()["data"][0]
        if fetch_all:
            for _include in include.split(","):
                for additional_data in self._extend_api_data(
                    artist["relationships"][_include],
                    limit,
                ):
                    artist["relationships"][_include]["data"].extend(additional_data)
        return artist

    def get_song(
        self,
        song_id: str,
        extend: str = "extendedAssetUrls",
        include: str = "lyrics,albums",
    ) -> dict:
        response = self.session.get(
            f"{self.AMP_API_URL}/v1/catalog/{self.storefront}/songs/{song_id}",
            params={
                "include": include,
                "extend": extend,
            },
        )
        self._check_amp_api_response(response)
        return response.json()["data"][0]

    def get_music_video(
        self,
        music_video_id: str,
        include: str = "albums",
    ) -> dict:
        response = self.session.get(
            f"{self.AMP_API_URL}/v1/catalog/{self.storefront}/music-videos/{music_video_id}",
            params={
                "include": include,
            },
        )
        self._check_amp_api_response(response)
        return response.json()["data"][0]

    def get_post(
        self,
        post_id: str,
    ) -> dict:
        response = self.session.get(
            f"{self.AMP_API_URL}/v1/catalog/{self.storefront}/uploaded-videos/{post_id}"
        )
        self._check_amp_api_response(response)
        return response.json()["data"][0]

    @functools.lru_cache()
    def get_album(
        self,
        album_id: str,
        extend: str = "extendedAssetUrls",
    ) -> dict:
        response = self.session.get(
            f"{self.AMP_API_URL}/v1/catalog/{self.storefront}/albums/{album_id}",
            params={
                "extend": extend,
            },
        )
        self._check_amp_api_response(response)
        return response.json()["data"][0]

    def get_playlist(
        self,
        playlist_id: str,
        limit_tracks: int = 300,
        extend: str = "extendedAssetUrls",
        fetch_all: bool = True,
    ) -> dict:
        response = self.session.get(
            f"{self.AMP_API_URL}/v1/catalog/{self.storefront}/playlists/{playlist_id}",
            params={
                "extend": extend,
                "limit[tracks]": limit_tracks,
            },
        )
        self._check_amp_api_response(response)
        playlist = response.json()["data"][0]
        if fetch_all:
            for additional_data in self._extend_api_data(
                playlist["relationships"]["tracks"],
                limit_tracks,
            ):
                playlist["relationships"]["tracks"]["data"].extend(additional_data)
        return playlist

    def search(
        self,
        term: str,
        types: str = "songs,albums,artists,playlists",
        limit: int = 25,
        offset: int = 0,
    ) -> dict:
        # Apple Music API has a maximum limit of 50 results per request
        if limit > 50:
            limit = 50

        response = self.session.get(
            f"{self.AMP_API_URL}/v1/catalog/{self.storefront}/search",
            params={
                "term": term,
                "types": types,
                "limit": limit,
                "offset": offset,
            },
        )
        self._check_amp_api_response(response)
        return response.json()["results"]

    def _extend_api_data(
        self,
        api_response: dict,
        limit: int,
    ) -> typing.Generator[list[dict], None, None]:
        next_uri = api_response.get("next")
        while next_uri:
            playlist_next = self._get_next_uri_response(next_uri, limit)
            yield playlist_next["data"]
            next_uri = playlist_next.get("next")
            time.sleep(self.WAIT_TIME)

    def _get_next_uri_response(self, next_uri: str, limit: int) -> dict:
        response = self.session.get(
            self.AMP_API_URL + next_uri,
            params={
                "limit": limit,
            },
        )
        self._check_amp_api_response(response)
        return response.json()

    def get_webplayback(
        self,
        track_id: str,
    ) -> dict:
        response = self.session.post(
            self.WEBPLAYBACK_API_URL,
            json={
                "salableAdamId": track_id,
                "language": self.language,
            },
        )
        try:
            response.raise_for_status()
            response_dict = response.json()
            webplayback = response_dict.get("songList")
            assert webplayback
        except (
            requests.HTTPError,
            requests.exceptions.JSONDecodeError,
            AssertionError,
        ):
            raise_response_exception(response)
        return webplayback[0]

    def get_widevine_license(
        self,
        track_id: str,
        track_uri: str,
        challenge: str,
    ) -> str:
        print("[gamdl AppleMusicApi DEBUG] Entering get_widevine_license method.")
        # Force re-initialization of the session to ensure all tokens and headers are fresh
        print("[gamdl AppleMusicApi DEBUG] Attempting to re-run _set_session() for freshness...")
        try:
            self._set_session() # Call _set_session to refresh cookies, bearer token, and headers
            print("[gamdl AppleMusicApi DEBUG] _set_session() completed.")
        except Exception as e_set_session:
            print(f"[gamdl AppleMusicApi ERROR] Failed to re-run _set_session in get_widevine_license: {e_set_session}. Proceeding with existing session.")
            # If _set_session fails here, we'll proceed with the potentially stale session, 
            # but this indicates a deeper problem with re-initializing.

        print(f"[gamdl AppleMusicApi DEBUG] get_widevine_license called for track_id: {track_id}") # Duplicating for clarity after potential re-init
        print(f"[gamdl AppleMusicApi DEBUG] Current storefront for this API session: {self.storefront}")
        print(f"[gamdl AppleMusicApi DEBUG] track_uri (to be used in payload): {track_uri}")

        payload = {
            "challenge": challenge,
            "key-system": "com.widevine.alpha",
            "uri": track_uri,
            "adamId": track_id,
            "isLibrary": False,
            "user-initiated": True,
        }
        print(f"[gamdl AppleMusicApi DEBUG] License request payload: {payload}")

        relevant_headers = {
            "Authorization": self.session.headers.get("Authorization"),
            "Media-User-Token": self.session.headers.get("Media-User-Token"),
            "Origin": self.session.headers.get("origin"),
            "Content-Type": self.session.headers.get("content-type")
        }
        print(f"[gamdl AppleMusicApi DEBUG] Relevant session headers for license request: {relevant_headers}")

        response = self.session.post(
            self.LICENSE_API_URL,
            json=payload,
        )
        try:
            response.raise_for_status()
            response_dict = response.json()
            widevine_license = response_dict.get("license")
            assert widevine_license
        except (
            requests.HTTPError,
            requests.exceptions.JSONDecodeError,
            AssertionError,
        ):
            raise_response_exception(response)
        return widevine_license
