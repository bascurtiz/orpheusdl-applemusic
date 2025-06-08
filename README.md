# Apple Music Module for OrpheusDL

This module enables downloading music from Apple Music using OrpheusDL. It bridges the functionality of [gamdl](https://github.com/glomatico/gamdl) to work within the OrpheusDL framework.

## Features

- Download tracks, albums, playlists, and artist discographies from Apple Music
- Search Apple Music catalog directly from OrpheusDL GUI
- High-quality audio downloads (AAC 256kbps)
- Automatic metadata extraction and tagging
- Lyrics download support
- High-resolution cover art

## Prerequisites

### 1. Apple Music Subscription
You need an active Apple Music subscription to download content.

### 2. Cookies File
You need to export your Apple Music cookies to authenticate with the service.

**Steps to get cookies:**
1. Log in to [Apple Music Web](https://music.apple.com) in your browser
2. Make sure you're logged in and have an active subscription
3. Export cookies using a browser extension like:
   - **Chrome/Edge**: [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - **Firefox**: [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)
Save the exported cookies as `cookies.txt` in the `/config` folder of OrpheusDL.

## Setup

1. Go to your orpheusdl/ directory and run:
```bash
git clone https://github.com/bascurtiz/orpheusdl-applemusic modules/applemusic
```

2. cd into modules/applemusic/gamdl and run:
```bash
pip install -r requirements.txt
```

3. **Place your cookies file**:<br>
Put your `cookies.txt` file in the `/config` folder (next to settings.json)

4. Execute:
```bash
python orpheus.py
```
Now the config/settings.json file should be updated with the Apple Music settings.

5. Make sure FFmpeg path is set in settings.json, or put it to your OS environment.<br>
   Instructions for macOS: https://phoenixnap.com/kb/ffmpeg-mac<br>
   Instructions for Win: https://phoenixnap.com/kb/ffmpeg-windows<br>


## Configuration Options

| Setting | Default | Description |
|---------|---------|-------------|
| `cookies_path` | `./config/cookies.txt` | Path to your Apple Music cookies file |
| `language` | `en-US` | Language for metadata (ISO language code) |
| `codec` | `aac` | Preferred codec (`aac` or `alac`) |
| `quality` | `high` | Quality preference |

## Usage

### Downloading
The module supports standard Apple Music URLs:

- **Track**: `python orpheus.py https://music.apple.com/us/song/never-gonna-give-you-up-2022-remaster/1612648319`
- **Album**: `python orpheus.py https://music.apple.com/us/album/whenever-you-need-somebody-deluxe-edition-2022-remaster/1612648318`
- **Playlist**: `python orpheus.py https://music.apple.com/us/playlist/rick-astley-essentials/pl.504a9420747e43ec93e4faa999a8bef9`
- **Artist**: `python orpheus.py https://music.apple.com/us/artist/rick-astley/669771`

### Searching
Use the Search tab in OrpheusDL GUI to search Apple Music:
1. Select "Apple Music" as the platform
2. Choose search type (track, album, artist, playlist)
3. Enter your search query
4. Select results to download

## Audio Quality

- **AAC**: 256 kbps (standard Apple Music quality)
- **ALAC**: Lossless (when available and subscription supports it)
- **Sample Rate**: 44.1 kHz (standard) or 48 kHz (some content)

## Troubleshooting

### "media-user-token not found in cookies"
- Make sure you're logged in to Apple Music web
- Ensure you have an active subscription
- Re-export your cookies from the browser
- Check that the cookies file is not corrupted

### "Track is not streamable"
- The track might be region-locked
- Your subscription might not include this content
- The track might be removed from Apple Music

### "No stream URL available"
- This can happen with very new releases
- Try again later as Apple Music sometimes has delayed availability
- Check if the track is available in your region

### Import Errors
- Ensure the gamdl folder is in the correct location
- Check that all gamdl dependencies are installed
- Verify Python path includes the gamdl directory

## Notes

- This module requires the gamdl project to be present in the `gamdl/` folder
- DRM-protected content requires additional setup (Widevine CDM)
- Some content may require specific geographic regions
- Downloads are for personal use only - respect Apple Music's terms of service

## Credits

This module is a bridge between:
- [OrpheusDL](https://github.com/bascurtiz/orpheusdl) - The main download framework
- [gamdl](https://github.com/glomatico/gamdl) - Apple Music download implementation

All credit for the Apple Music download functionality goes to the gamdl project and its contributors. 