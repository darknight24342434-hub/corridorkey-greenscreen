---
name: corridorkey-video-matting
description: Run CorridorKey green-screen video matting for videos with green backgrounds, including 人物綠幕去背, 影片人物去背, alpha/transparent MOV export, matte preview, checkerboard QC, and short preview runs before full export. Use when Codex needs to remove a green screen from video footage with the local ComfyUI-CorridorKey model.
---

# CorridorKey Video Matting

## Overview

Use this skill for green-screen video matting with the local ComfyUI-CorridorKey installation. It is for footage that already has a green background; do not use it as the default path for arbitrary non-green-screen person segmentation.

## Core Workflow

1. Confirm the input is green-screen footage. If it is not, explain that CorridorKey expects a green-screen alpha hint and choose a separate person-segmentation workflow instead.
2. Find the input video path. If the user has not provided one, search the current project with `rg --files -g '*.mp4' -g '*.mov' -g '*.webm' -g '*.mkv'`.
3. Run a short preview first unless the user explicitly requests full export. Use 5 seconds or roughly 60-150 frames depending on source FPS.
4. Inspect the checkerboard QC output before running the full clip. Look for green spill, missing hair/edge detail, holes, and unwanted foreground speckles.
5. Run the full export after the preview is acceptable.
6. Report the output folder and the three main files:
   - `corridorkey_transparent_rgba.mov`
   - `corridorkey_qc_checkerboard.mp4`
   - `corridorkey_matte.mp4`

## Script

Use the bundled script:

```powershell
python "$env:CORRIDORKEY_SCRIPTS\run_corridorkey_video_matte.py" `
  --input "D:\path\to\green_screen.mp4" `
  --output-dir "D:\path\to\outputs\preview_name" `
  --seconds 5 `
  --start-time 0 `
  --img-size 1024 `
  --despeckle-size 100
```

For full export, omit `--seconds` and `--max-frames`:

```powershell
python "$env:CORRIDORKEY_SCRIPTS\run_corridorkey_video_matte.py" `
  --input "D:\path\to\green_screen.mp4" `
  --output-dir "D:\path\to\outputs\final_name" `
  --img-size 1024 `
  --despeckle-size 100
```

## 4K Source-RGB Edgefix Workflow

Use this path when CorridorKey creates an acceptable alpha/matte but the transparent MOV looks softer than the original source, especially on 4K face detail, or when the keyed edge still has green spill.

1. Run CorridorKey first and keep the normal output files:
   - `corridorkey_transparent_rgba.mov`
   - `corridorkey_qc_checkerboard.mp4`
   - `corridorkey_matte.mp4`
2. Compare the original source frame against `corridorkey_transparent_rgba.mov`. If the face or fine fabric detail is softer, do not use CorridorKey's RGB as the final foreground.
3. Rebuild the alpha MOV from original source RGB plus CorridorKey alpha:

```powershell
python "$env:CORRIDORKEY_SCRIPTS\build_edgefix_alpha.py" `
  --original "D:\path\to\source_green_screen.mp4" `
  --alpha "D:\path\to\corridorkey_transparent_rgba.mov" `
  --output "D:\path\to\edgefixed_alpha\clip_id\corridorkey_originalrgb_edgefixed_prores4444.mov"
```

The edgefix script preserves the original RGB image detail, uses CorridorKey only for alpha, chokes/softens the alpha edge, applies edge-only green despill, and writes a DaVinci/AE-friendly ProRes 4444 MOV with `yuva444p10le` alpha.

4. Validate the rebuilt MOV:

```powershell
ffmpeg -hide_banner -i "D:\path\to\corridorkey_originalrgb_edgefixed_prores4444.mov"
```

Expected video codec and pixel format: `prores`, `yuva444p10le`.

5. Check at least one mid-frame over the real background, not only over checkerboard. Green spill can look acceptable on checkerboard but obvious over blue or dark space backgrounds.
6. If green edge remains after the source-RGB edgefix pass, use Resolve Fusion Delta Keyer or AE Keylight for a manual matte refinement instead of stacking more global color correction.

## DaVinci Composite Handoff

For final review renders in DaVinci Resolve:

1. Create a separate Resolve project or timeline for the composite version under test.
2. Import the selected background clip and the source-RGB edgefixed ProRes 4444 foreground.
3. Put the background on V1 and foreground on V2. Loop or extend the background to cover the full foreground duration.
4. Keep the subject scale and position based on the keyed source; adjust the background crop/position to match the camera angle and apparent subject size.
5. Apply only shot-level balancing in Resolve after the matte is already clean: exposure, contrast, temperature/tint, and optional side-light simulation with a soft Power Window.
6. Render the accepted version to MP4 and export a `.drp` project backup.
7. Cleanup rule after approval: keep the accepted render(s), final `.drp`, and only the media paths required to relink the `.drp`. Remove rejected v1/v2/v3/v4 renders, preview PNGs, temporary reports, and transient alpha experiment folders.

## Local Paths

The script auto-detects the local CorridorKey node in this order:

1. `CORRIDORKEY_NODE` environment variable
2. `$env:COMFYUI_ROOT\ComfyUI\custom_nodes\ComfyUI-CorridorKey`
3. `$env:COMFYUI_ROOT\ComfyUI\custom_nodes\ComfyUI-CorridorKey`

The node must contain `corridor_key/` and `models/CorridorKey.pth`.

## Settings

- Use `--start-time` for previewing after a fully green leader or slate.
- Use `--sample-step 2` to process every other source frame and set output FPS automatically to `source_fps / 2`.
- Use `--fps` only when the output frame rate must be forced.
- Use `--despeckle-size 0` to preserve fine wires/strings or delicate details.
- Use `--despeckle-size 100` as a default for person footage.
- Increase `--despill` only if green contamination remains on edges. Start at `1.0`.
- Use `--device cpu` if CUDA import or VRAM behavior is unstable. Auto mode uses CUDA only when `torch.cuda.is_available()`.

## Validation

After each run, verify the transparent MOV includes an alpha-capable pixel format:

```powershell
ffmpeg -hide_banner -i "D:\path\to\corridorkey_transparent_rgba.mov"
```

Expected video codec and pixel format: `qtrle`, `argb`.

Check `corridorkey_qc_checkerboard.mp4` visually. If the matte is bad, do not run a full export until adjusting start time, crop, despeckle, or confirming that the source is really green-screen footage.

## Known Environment Notes

- The historical project is this repository.
- An older reusable script lives at `run_corridorkey_test.py` in this repository.
- The ComfyUI embedded Python may be incomplete if `python313.zip` or `Lib\encodings` is missing. Prefer the working system Python unless the local environment has been repaired.
- The system Python was verified with `timm`, `torchvision`, `torch`, `opencv-python`, `numpy`, and `Pillow`.
