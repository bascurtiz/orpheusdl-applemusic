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

### 1. OrpheusDL
[My fork](https://github.com/bascurtiz/orpheusdl) is needed to make Apple Music module work.

### 2. Apple Music Subscription
You need an active Apple Music subscription to download content.

### 3. FFmpeg
For default AAC 256kbps downloads, **only FFmpeg is required** (mp4decrypt and MP4Box are not needed).

You can provide FFmpeg in any of these ways:
- Place the `ffmpeg` binary (or `ffmpeg.exe` on Windows) in the OrpheusDL folder (next to `orpheus.py`) — the module will find it automatically
- Set the path in `config/settings.json` under `global.advanced.ffmpeg_path`
- Install FFmpeg to your system PATH

Instructions for installing FFmpeg:<br>
- macOS: https://phoenixnap.com/kb/ffmpeg-mac<br>
- Windows: https://phoenixnap.com/kb/ffmpeg-windows<br>

### 4. Cookies File
You need to export your Apple Music cookies to authenticate with the service.

**Steps to get cookies:**
1. Log in to [Apple Music Web](https://music.apple.com) in your browser
2. Make sure you're logged in and have an active subscription
3. Export cookies using a browser extension like:
   - **Chrome/Edge**: [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - **Firefox**: [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)<br>
Save the exported cookies as `cookies.txt` in the `/config` folder of OrpheusDL.<br>

## Installation

See video tutorial (for Windows): https://www.youtube.com/watch?v=ejHePonY4e8 <br>
See video tutorial (for macOS): https://www.youtube.com/watch?v=twrPwPjXVDw <br>

### macOS Users - Important SSL Setup!
Before using this module, you **must** install SSL certificates to avoid connection errors:

#### **Quick Method** (Recommended):
1. Open Terminal
2. Make sure certificates for Python are up to date:
```bash
pip3 install --upgrade certifi
```

### All Platforms Setup (Windows/macOS/Linux)

1. **Go to your orpheusdl/ directory and run**:
```bash
git clone https://github.com/bascurtiz/orpheusdl-applemusic modules/applemusic
```

2. **Place your cookies file**:<br>
Put your `cookies.txt` file in the `/config` folder (next to settings.json)

3. **Run orpheus.py**:<br>
Now the config/settings.json file should be updated with the Apple Music settings.

## Usage

### Downloading
The module supports standard Apple Music URLs:

- **Track**: `python orpheus.py https://music.apple.com/us/song/trackname/id`
- **Album**: `python orpheus.py https://music.apple.com/us/album/albumname/id`
- **Playlist**: `python orpheus.py https://music.apple.com/us/playlist/playlistname/pl.hashstring`
- **Artist**: `python orpheus.py https://music.apple.com/us/artist/artistname/id`

### Searching
- **Track**: `python orpheus.py search applemusic track Never gonna give you up`
- **Album**: `python orpheus.py search applemusic album Whenever You Need Somebody`
- **Playlist**: `python orpheus.py search applemusic playlist Rick Astley essentials`
- **Artist**: `python orpheus.py search applemusic artist Rick Astley`<br>

Or use the Search tab in OrpheusDL GUI to search Apple Music:
1. Select "Apple Music" as the platform
2. Choose search type (track, album, artist, playlist)
3. Enter your search query
4. Select results to download

## Audio Quality

- **AAC**: 256 kbps (standard Apple Music quality)
- **Sample Rate**: 44.1 kHz (standard) or 48 kHz (some content)

## Troubleshooting

### SSL Certificate Errors (macOS)
**Error**: `certificate verify failed: unable to get local issuer certificate`

**Solution**:
1. **Quick Fix**: Run this in Terminal (replace `3.11` with your Python version):
   ```bash
   open "/Applications/Python 3.11/Install Certificates.command"
   ```

2. **Alternative**: Update certificates via pip:
   ```bash
   pip3 install --upgrade certifi
   ```

3. **Manual**: If automation fails:
   ```bash
   /Applications/Python\ 3.11/Install\ Certificates.command
   ```

**Why this happens**: macOS Python installations don't use system certificates by default. This is a known issue that affects all HTTPS connections in Python on macOS.

### SSL Certificate Errors (Other Platforms)
**Error**: SSL-related connection errors

**Solution**:
```bash
pip3 install --upgrade certifi
```

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

### FFmpeg Required / Not Found Error
**Error**: `Apple Music streaming error (FFmpeg required for processing)` or similar

**Solution**:
1. Download FFmpeg and place the `ffmpeg` binary (or `ffmpeg.exe` on Windows) in the OrpheusDL folder, next to `orpheus.py`
2. Or set the correct path in `config/settings.json`:
   ```json
   {
     "global": {
       "advanced": {
         "ffmpeg_path": "/path/to/your/ffmpeg"
       }
     }
   }
   ```
3. Or install FFmpeg to your system PATH

**macOS only**: If you placed ffmpeg in the OrpheusDL folder and it still fails, macOS may be blocking the binary (Gatekeeper quarantine). Run:
   ```bash
   xattr -d com.apple.quarantine /path/to/OrpheusDL/ffmpeg
   ```
   Or right-click the ffmpeg file → Open (once) to clear the quarantine.

### Import Errors
- Ensure the gamdl folder is in the correct location
- Check that all gamdl dependencies are installed
- Verify Python path includes the gamdl directory

## Known Issues

### macOS Specific
- **SSL Certificates**: Must be installed before first use (see installation section)
- **FFmpeg**: Place ffmpeg in the OrpheusDL folder or set path in settings. If downloads fail despite ffmpeg being present, remove the quarantine flag: `xattr -d com.apple.quarantine /path/to/ffmpeg`
- **Homebrew Python**: If using Homebrew Python, certificate installation may differ

### General
- Some very new releases may not be immediately available
- Region-locked content requires VPN or different account region
- Large downloads may timeout and require retry

## Notes

- This module requires the gamdl project to be present in the `gamdl/` folder
- DRM-protected content requires additional setup (Widevine CDM)
- Some content may require specific geographic regions
- Downloads are for personal use only - respect Apple Music's terms of service
- **macOS users must install SSL certificates before first use**

## Credits

This module is a bridge between:
- [OrpheusDL](https://github.com/bascurtiz/orpheusdl) - The main download framework
- [gamdl](https://github.com/glomatico/gamdl) - Apple Music download implementation

All credit for the Apple Music download functionality goes to the gamdl project and its contributors. 