from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import os
from pathlib import Path

import cv2
import numpy as np


# ffmpeg. Set FFMPEG to a full path, or leave it and rely on PATH.
FFMPEG = Path(os.environ.get("FFMPEG", "ffmpeg"))


def media_info(path: Path) -> tuple[int, int, float]:
    proc = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    text = proc.stdout
    match = re.search(r"Video:.*?,\s*(\d+)x(\d+).*?,\s*([0-9.]+)\s*fps", text)
    if not match:
        raise RuntimeError(f"Could not parse video info for {path}\n{text[:1200]}")
    width, height, fps = match.groups()
    return int(width), int(height), float(fps)


def smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def fill_holes(mask: np.ndarray) -> np.ndarray:
    mask = (mask > 0).astype(np.uint8) * 255
    h, w = mask.shape
    flood = mask.copy()
    flood_pad = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(flood, flood_pad, (0, 0), 255)
    holes = cv2.bitwise_not(flood)
    return cv2.bitwise_or(mask, holes)


def holdout_mask(rgb_u8: np.ndarray, base_alpha_u8: np.ndarray) -> np.ndarray:
    h, w = base_alpha_u8.shape
    mw, mh = max(2, w // 2), max(2, h // 2)
    rgb = cv2.resize(rgb_u8, (mw, mh), interpolation=cv2.INTER_AREA)
    alpha = cv2.resize(base_alpha_u8, (mw, mh), interpolation=cv2.INTER_AREA)

    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    max_rgb = np.maximum.reduce([r, g, b])
    max_rb = np.maximum(r, b)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat = hsv[:, :, 1].astype(np.int16)
    val = hsv[:, :, 2].astype(np.int16)

    green_screen = (
        (g > 72)
        & (g > r + 22)
        & (g > b + 22)
        & (g > (max_rb * 118) // 100)
    )
    pale_cube_face = (sat < 78) & (val > 78)
    candidate = ((~green_screen) | pale_cube_face) & (max_rgb > 18)

    candidate[: max(2, int(mh * 0.03)), :] = False
    candidate[:, : max(2, int(mw * 0.005))] = False
    candidate[:, -max(2, int(mw * 0.005)) :] = False

    candidate_u8 = candidate.astype(np.uint8) * 255
    candidate_u8 = cv2.morphologyEx(
        candidate_u8, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1
    )
    candidate_u8 = cv2.morphologyEx(
        candidate_u8, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2
    )
    candidate_u8 = fill_holes(candidate_u8)

    num, labels, stats, _ = cv2.connectedComponentsWithStats(candidate_u8, 8)
    out = np.zeros(candidate_u8.shape, np.uint8)
    alpha_bin = alpha > 15
    min_area = max(350, int(mw * mh * 0.0027))
    cube_area = max(1100, int(mw * mh * 0.0085))
    min_w = max(16, int(mw * 0.033))
    min_h = max(16, int(mh * 0.059))

    for i in range(1, num):
        x, y, ww, hh, area = stats[i]
        if area < min_area:
            continue
        comp = labels == i
        overlap = int(np.count_nonzero(alpha_bin & comp))
        cube_like = area > cube_area and ww > min_w and hh > min_h and y > int(mh * 0.05)
        near_alpha = overlap > 40 or overlap / max(area, 1) > 0.015
        if cube_like or near_alpha:
            out[comp] = 255

    out = cv2.GaussianBlur(out, (0, 0), 0.8)
    return cv2.resize(out, (w, h), interpolation=cv2.INTER_LINEAR)


def clean_base_alpha(base_alpha_u8: np.ndarray, holdout_u8: np.ndarray) -> np.ndarray:
    h, w = base_alpha_u8.shape
    alpha_bin = (base_alpha_u8 > 12).astype(np.uint8) * 255
    num, labels, stats, _ = cv2.connectedComponentsWithStats(alpha_bin, 8)
    out = np.zeros(alpha_bin.shape, np.uint8)
    holdout_bin = holdout_u8 > 15
    min_keep_area = int(w * h * 0.001)

    for i in range(1, num):
        x, y, ww, hh, area = stats[i]
        comp = labels == i
        touches_top = y <= int(h * 0.03)
        overlaps_holdout = np.count_nonzero(holdout_bin & comp) > 0
        if touches_top and area < int(w * h * 0.008) and not overlaps_holdout:
            continue
        if area >= min_keep_area or overlaps_holdout:
            out[comp] = 255

    return np.where(out > 0, base_alpha_u8, 0).astype(np.uint8)


def edgefix_frame(rgb_u8: np.ndarray, alpha_u8: np.ndarray) -> np.ndarray:
    kernel = np.ones((3, 3), np.uint8)
    alpha = cv2.erode(alpha_u8, kernel, iterations=2)
    alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=1.0, sigmaY=1.0)
    alpha = np.where(alpha_u8 > 252, np.maximum(alpha, 250), alpha).astype(np.uint8)

    af = alpha.astype(np.float32) / 255.0
    rgb = rgb_u8.astype(np.float32) / 255.0
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]

    max_rb = np.maximum(r, b)
    min_rb = np.minimum(r, b)
    green_excess = g - max_rb

    spill = smoothstep((green_excess + 0.015) / 0.145)
    spill = np.maximum(spill, smoothstep((g - min_rb + 0.020) / 0.210) * 0.62)

    edge = smoothstep((1.0 - af - 0.03) / 0.85)
    foreground = smoothstep((af - 0.015) / 0.08)
    spill = np.clip(spill * (0.38 + edge * 1.08) * foreground * 1.08, 0.0, 1.0)

    neutral_g = max_rb * 0.68 + b * 0.32
    g2 = g * (1.0 - spill) + neutral_g * spill
    g2 = np.minimum(g2, max_rb * (1.0 - 0.22 * spill) + 0.012)
    r2 = np.clip(r + spill * 0.055, 0.0, 1.0)
    b2 = np.clip(b + spill * 0.080, 0.0, 1.0)

    out = np.empty((rgb_u8.shape[0], rgb_u8.shape[1], 4), dtype=np.uint8)
    out[:, :, 0] = np.clip(r2 * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[:, :, 1] = np.clip(g2 * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[:, :, 2] = np.clip(b2 * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[:, :, 3] = alpha
    return out


def start_decoder(path: Path, pix_fmt: str) -> subprocess.Popen:
    return subprocess.Popen(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            pix_fmt,
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def start_encoder(output: Path, width: int, height: int, fps: float) -> subprocess.Popen:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    return subprocess.Popen(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-s:v",
            f"{width}x{height}",
            "-r",
            f"{fps:.6f}",
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4",
            "-pix_fmt",
            "yuva444p10le",
            "-vendor",
            "apl0",
            str(output),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def process(original: Path, base_alpha_mov: Path, output: Path, holdout_until: float | None = None) -> int:
    width, height, fps = media_info(original)
    aw, ah, _ = media_info(base_alpha_mov)
    if (aw, ah) != (width, height):
        raise RuntimeError(f"Size mismatch: original {width}x{height}, alpha {aw}x{ah}")

    rgb_size = width * height * 3
    rgba_size = width * height * 4
    original_proc = start_decoder(original, "rgb24")
    alpha_proc = start_decoder(base_alpha_mov, "rgba")
    encoder = start_encoder(output, width, height, fps)
    if not original_proc.stdout or not alpha_proc.stdout or not encoder.stdin:
        raise RuntimeError("Failed to open ffmpeg pipes")

    frames = 0
    start = time.time()
    try:
        while True:
            rgb_bytes = original_proc.stdout.read(rgb_size)
            rgba_bytes = alpha_proc.stdout.read(rgba_size)
            if len(rgb_bytes) != rgb_size or len(rgba_bytes) != rgba_size:
                break

            rgb = np.frombuffer(rgb_bytes, dtype=np.uint8).reshape((height, width, 3))
            rgba = np.frombuffer(rgba_bytes, dtype=np.uint8).reshape((height, width, 4))
            time_sec = frames / fps
            if holdout_until is not None and time_sec >= holdout_until:
                fixed = rgba
            else:
                holdout = holdout_mask(rgb, rgba[:, :, 3])
                base_alpha = clean_base_alpha(rgba[:, :, 3], holdout)
                merged_alpha = np.maximum(base_alpha, holdout).astype(np.uint8)
                fixed = edgefix_frame(rgb, merged_alpha)
            encoder.stdin.write(np.ascontiguousarray(fixed).tobytes())
            frames += 1
            if frames == 1 or frames % 30 == 0:
                elapsed = time.time() - start
                print(f"processed {frames} frames in {elapsed:.1f}s", flush=True)
    finally:
        if encoder.stdin:
            encoder.stdin.close()
        if original_proc.stdout:
            original_proc.stdout.close()
        if alpha_proc.stdout:
            alpha_proc.stdout.close()
        original_proc.wait()
        alpha_proc.wait()

    err = encoder.stderr.read().decode("utf-8", errors="replace") if encoder.stderr else ""
    code = encoder.wait()
    if code != 0:
        raise RuntimeError(f"encoder failed with code {code}\n{err[-2000:]}")
    return frames


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build cube-holdout original-RGB ProRes 4444 alpha MOV.")
    parser.add_argument("--original", required=True)
    parser.add_argument("--base-alpha", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--holdout-until",
        type=float,
        default=None,
        help="Apply cube holdout only before this source time in seconds; later frames copy base alpha/RGBA.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frames = process(Path(args.original), Path(args.base_alpha), Path(args.output), args.holdout_until)
    print(f"done frames={frames} output={args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
