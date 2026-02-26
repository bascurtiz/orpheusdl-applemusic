# Apple Music Module for OrpheusDL

This module enables downloading music from Apple Music using OrpheusDL. It bridges the functionality of [gamdl](https://github.com/glomatico/gamdl) (v2.9.1) to work within the OrpheusDL framework.

## Features

- Download **tracks, albums, playlists, and artist discographies**
- **Standard Quality**: AAC 256kbps
- **Lossless Audio**: Apple Lossless (ALAC) up to 24-bit/48kHz
- **Hi-Res Lossless**: Apple Lossless (ALAC) up to 24-bit/192kHz
- **Dolby Atmos**: Spatial Audio support
- Search Apple Music catalog directly from OrpheusDL GUI
- Automatic metadata extraction and rich tagging
- Lyrics download support
- High-resolution cover art

## Prerequisites

### 1. OrpheusDL
[My fork](https://github.com/bascurtiz/orpheusdl) is needed to make Apple Music module work.

### 2. Apple Music Subscription
You need an active Apple Music subscription to download content.

### 3. FFmpeg
**FFmpeg is the only binary requirement** for all formats (AAC, ALAC, and Atmos). The decryption functionality is now built-in, so only [Docker](https://www.docker.com/products/docker-desktop/) & [wrapper](https://github.com/WorldObservationLog/wrapper) are required for Dolby Atmos / ALAC.

You can provide FFmpeg in any of these ways:
- Place the `ffmpeg` binary (or `ffmpeg.exe` on Windows) in the OrpheusDL folder (next to `orpheus.py`) — the module will find it automatically
- Set the path in `config/settings.json` under `global.advanced.ffmpeg_path`
- Install FFmpeg to your system PATH

Instructions for installing FFmpeg:<br>
- macOS: https://phoenixnap.com/kb/ffmpeg-mac<br>
- Windows: https://phoenixnap.com/kb/ffmpeg-windows<br>

### 4. Cookies File
You need to export your Apple Music cookies to authenticate.

**Steps to get cookies:**
1. Log in to [Apple Music Web](https://music.apple.com) in your browser.
2. Export cookies using a browser extension like **Get cookies.txt LOCALLY**.
3. Save as `cookies.txt` in the `/config` folder of OrpheusDL.

## Installation

See video tutorial (for Windows): https://www.youtube.com/watch?v=ejHePonY4e8 <br>
See video tutorial (for macOS): https://www.youtube.com/watch?v=twrPwPjXVDw <br>

### macOS Users - Important SSL Setup!
Before using this module, you **must** install SSL certificates:
```bash
pip3 install --upgrade certifi
```
And run the "Install Certificates.command" found in your Python application folder.

## Usage

### Downloading
The module supports standard Apple Music URLs:

- **Track**: `python orpheus.py https://music.apple.com/us/song/trackname/id`
- **Album**: `python orpheus.py https://music.apple.com/us/album/albumname/id`
- **Playlist**: `python orpheus.py https://music.apple.com/us/playlist/playlistname/pl.hashstring`
- **Artist**: `python orpheus.py https://music.apple.com/us/artist/artistname/id`

## Audio Quality

- **AAC**: 256 kbps (Standard)
- **ALAC (Lossless)**: Up to 24-bit/48kHz
- **ALAC (Hi-Res)**: Up to 24-bit/192kHz
- **Dolby Atmos**: Immersive spatial audio

## Troubleshooting

### SSL Certificate Errors (macOS)
**Error**: `certificate verify failed: unable to get local issuer certificate`
**Solution**: Run the "Install Certificates.command" in your `/Applications/Python X.X/` folder.

### "media-user-token not found in cookies"
- Re-export your cookies after ensuring you are logged in and have an active subscription.

## Credits

- [OrpheusDL](https://github.com/bascurtiz/orpheusdl) - The main framework
- [gamdl](https://github.com/glomatico/gamdl) - Apple Music implementation

All credit for the Apple Music download functionality goes to the gamdl project and its contributors.
 