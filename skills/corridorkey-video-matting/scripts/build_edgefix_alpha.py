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


def edgefix_frame(rgb_u8: np.ndarray, alpha_u8: np.ndarray) -> np.ndarray:
    kernel = np.ones((3, 3), np.uint8)
    alpha = cv2.erode(alpha_u8, kernel, iterations=3)
    alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=1.05, sigmaY=1.05)
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
            "-map",
            "0:v:0",
            "-pix_fmt",
            pix_fmt,
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def start_encoder(output: Path, width: int, height: int, fps: float) -> subprocess.Popen:
    output.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            str(FFMPEG),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-s:v",
            f"{width}x{height}",
            "-framerate",
            f"{fps:g}",
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


def read_exact(pipe, size: int) -> bytes:
    data = pipe.read(size)
    return data or b""


def process(original: Path, alpha_mov: Path, output: Path) -> int:
    width, height, fps = media_info(original)
    aw, ah, afps = media_info(alpha_mov)
    if (aw, ah) != (width, height):
        raise RuntimeError(f"Size mismatch: original {width}x{height}, alpha {aw}x{ah}")

    rgb_size = width * height * 3
    rgba_size = width * height * 4
    original_proc = start_decoder(original, "rgb24")
    alpha_proc = start_decoder(alpha_mov, "rgba")
    encoder = start_encoder(output, width, height, fps)
    if not original_proc.stdout or not alpha_proc.stdout or not encoder.stdin:
        raise RuntimeError("Failed to open ffmpeg pipes")

    frames = 0
    start = time.time()
    try:
        while True:
            rgb_bytes = read_exact(original_proc.stdout, rgb_size)
            rgba_bytes = read_exact(alpha_proc.stdout, rgba_size)
            if len(rgb_bytes) != rgb_size or len(rgba_bytes) != rgba_size:
                break

            rgb = np.frombuffer(rgb_bytes, dtype=np.uint8).reshape((height, width, 3))
            rgba = np.frombuffer(rgba_bytes, dtype=np.uint8).reshape((height, width, 4))
            fixed = edgefix_frame(rgb, rgba[:, :, 3])
            encoder.stdin.write(np.ascontiguousarray(fixed).tobytes())
            frames += 1
            if frames == 1 or frames % 30 == 0:
                elapsed = time.time() - start
                print(f"processed {frames} frames in {elapsed:.1f}s", flush=True)
    finally:
        if encoder.stdin:
            encoder.stdin.close()
        original_proc.stdout.close()
        alpha_proc.stdout.close()
        original_proc.wait()
        alpha_proc.wait()

    err = encoder.stderr.read().decode("utf-8", errors="replace") if encoder.stderr else ""
    code = encoder.wait()
    if code != 0:
        raise RuntimeError(f"encoder failed with code {code}\n{err[-2000:]}")
    return frames


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build edge-fixed original-RGB alpha ProRes 4444 MOV.")
    parser.add_argument("--original", required=True)
    parser.add_argument("--alpha", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frames = process(Path(args.original), Path(args.alpha), Path(args.output))
    print(f"done frames={frames} output={args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
