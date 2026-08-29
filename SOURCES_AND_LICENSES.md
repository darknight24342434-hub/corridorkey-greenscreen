# CorridorKey Green-Screen Test Footage Sources

The clips themselves are **not distributed with this repository** — the originals run to
hundreds of megabytes. Each entry below records where the footage came from and under what
licence, so it can be fetched again from the source page; the local filenames are the ones
the scripts expect once you have downloaded it.

## 1. Filming with the green screen

- Local original: `filming_with_the_green_screen.original.webm`
- Local test MP4: `filming_with_the_green_screen.test_720p.mp4`
- Preview: `preview_filming_with_the_green_screen.png`
- Source page: https://commons.wikimedia.org/wiki/File:Filming_with_the_green_screen.webm
- Source file: Wikimedia Commons original file redirect
- License/status: Public domain in the United States per Wikimedia Commons page, because the media is listed as Voice of America material.
- Test use: Best current CorridorKey smoke-test candidate because it shows real people in front of a green screen.

## 2. Court Scale Green Screen

- Local original: `court_scale_green_screen.original.webm`
- Local test MP4: `court_scale_green_screen.test_1280.mp4`
- Preview: `preview_court_scale_green_screen.png`
- Source page: https://commons.wikimedia.org/wiki/File:Court_Scale_Green_Screen.webm
- Source file: Wikimedia Commons original file redirect
- License/status: Creative Commons Attribution 3.0 Unported, attribution to HD Green Screen.
- Test use: Small, simple chroma-key sample for fast keying checks.

## 3. Glitch Green Screen Split Overlay

- Local MP4: `glitch_green_screen_split_overlay.ccby4.mp4`
- Preview: `preview_glitch_green_screen_split_overlay.png`
- Source page: https://freestockfootagearchive.com/glitch-green-screen-split-up-down-overlay-effect/
- Source file: https://freestockfootagearchive.com/wp-content/uploads/2021/07/Glitch-Green-Screen-Split-Up-Down-Overlay-Effect.mp4
- License/status: Free Stock Footage Archive page lists private/commercial use under Creative Commons Attribution 4.0 conditions; attribution required.
- Test use: Overlay/effect-style green-screen footage. Useful for chroma-key tests, less useful than real-person footage for CorridorKey edge matting.

## Recommended First Test

Use `filming_with_the_green_screen.test_720p.mp4` first. It is already resized to 720x1280 H.264 so it should be easier on the RTX 2070 8GB VRAM than the original 1080x1920 WebM.
