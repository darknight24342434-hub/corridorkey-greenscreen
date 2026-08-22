from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch


# ComfyUI portable install root. Set COMFYUI_ROOT to wherever yours lives;
# the CorridorKey custom node is expected under ComfyUI/custom_nodes.
COMFYUI_ROOT = os.environ.get("COMFYUI_ROOT", "")

CORRIDORKEY_NODE_CANDIDATES = [
    Path(COMFYUI_ROOT) / "ComfyUI" / "custom_nodes" / "ComfyUI-CorridorKey",
]


def resolve_corridorkey_node() -> Path:
    env_path = os.environ.get("CORRIDORKEY_NODE")
    candidates = [Path(env_path)] if env_path else []
    candidates.extend(CORRIDORKEY_NODE_CANDIDATES)

    for candidate in candidates:
        if (candidate / "corridor_key").is_dir() and (candidate / "models" / "CorridorKey.pth").is_file():
            return candidate

    searched = "\n".join(f"  - {candidate}" for candidate in candidates)
    raise FileNotFoundError(
        "Could not find ComfyUI-CorridorKey with models/CorridorKey.pth. "
        "Set CORRIDORKEY_NODE to the installed ComfyUI-CorridorKey path.\n"
        f"Searched:\n{searched}"
    )


def add_corridorkey_to_path() -> Path:
    node_path = resolve_corridorkey_node()
    sys.path.insert(0, str(node_path))
    return node_path


def make_alpha_hint(rgb_u8: np.ndarray) -> np.ndarray:
    """Build a rough foreground alpha hint by keying green pixels as background."""
    bgr = cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    r = rgb_u8[:, :, 0].astype(np.float32)
    g = rgb_u8[:, :, 1].astype(np.float32)
    b = rgb_u8[:, :, 2].astype(np.float32)

    hsv_green = (h >= 35) & (h <= 90) & (s >= 45) & (v >= 35)
    channel_green = (g > r * 1.12) & (g > b * 1.05) & (g > 65)
    background = hsv_green & channel_green

    alpha = (~background).astype(np.uint8) * 255
    kernel = np.ones((3, 3), np.uint8)
    alpha = cv2.erode(alpha, kernel, iterations=1)
    alpha = cv2.GaussianBlur(alpha, (7, 7), 0)
    return alpha.astype(np.float32) / 255.0


def write_png(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", array)
    if not ok:
        raise RuntimeError(f"Could not encode PNG: {path}")
    encoded.tofile(str(path))


def write_rgb(path: Path, rgb: np.ndarray) -> None:
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    write_png(path, bgr)


def write_gray(path: Path, gray: np.ndarray) -> None:
    write_png(path, gray)


def write_rgba(path: Path, rgba: np.ndarray) -> None:
    bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
    write_png(path, bgra)


def checkerboard(width: int, height: int, tile: int = 48) -> np.ndarray:
    yy, xx = np.indices((height, width))
    checks = ((xx // tile) + (yy // tile)) % 2
    dark = np.array([42, 42, 42], dtype=np.uint8)
    light = np.array([150, 150, 150], dtype=np.uint8)
    return np.where(checks[:, :, None] == 0, dark, light)


def encode_outputs(out_dir: Path, fps: float) -> None:
    frame_pattern = str(Path("rgba_frames") / "rgba_%05d.png")
    qc_pattern = str(Path("qc_frames") / "qc_%05d.png")
    matte_pattern = str(Path("matte_frames") / "matte_%05d.png")

    commands = [
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-framerate",
            f"{fps:g}",
            "-i",
            qc_pattern,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "corridorkey_qc_checkerboard.mp4",
        ],
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-framerate",
            f"{fps:g}",
            "-i",
            matte_pattern,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "corridorkey_matte.mp4",
        ],
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-framerate",
            f"{fps:g}",
            "-i",
            frame_pattern,
            "-c:v",
            "qtrle",
            "-pix_fmt",
            "argb",
            "corridorkey_transparent_rgba.mov",
        ],
    ]
    for command in commands:
        subprocess.run(command, check=True, cwd=str(out_dir))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--img-size", type=int, default=1024)
    parser.add_argument("--max-frames", type=int, default=30)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--sample-step", type=int, default=1)
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument("--despill", type=float, default=1.0)
    parser.add_argument("--despeckle-size", type=int, default=400)
    args = parser.parse_args()

    corridorkey_node = add_corridorkey_to_path()
    from corridor_key.engine import CorridorKeyEngine, resolve_checkpoint_path

    src = Path(args.input)
    out_dir = Path(args.output_dir)
    for child in ["rgba_frames", "qc_frames", "matte_frames", "hint_frames", "source_frames"]:
        (out_dir / child).mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open input video: {src}")

    print(f"Using CorridorKey node: {corridorkey_node}")
    print(f"Loading CorridorKey checkpoint: {resolve_checkpoint_path()}")
    print(f"Using CUDA: {torch.cuda.is_available()} img_size={args.img_size}")
    engine = CorridorKeyEngine(
        checkpoint_path=resolve_checkpoint_path(),
        device="cuda" if torch.cuda.is_available() else "cpu",
        img_size=args.img_size,
        use_refiner=True,
    )

    written = 0
    read_index = 0
    if args.start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, args.start_frame)
        read_index = args.start_frame
    start = time.time()
    while written < args.max_frames:
        ok, bgr = cap.read()
        if not ok:
            break
        if read_index % max(args.sample_step, 1) != 0:
            read_index += 1
            continue
        read_index += 1

        rgb_u8 = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        alpha_hint = make_alpha_hint(rgb_u8)
        rgb_float = rgb_u8.astype(np.float32) / 255.0

        result = engine.process_frame(
            image=rgb_float,
            mask_linear=alpha_hint,
            refiner_scale=1.0,
            input_is_linear=False,
            despill_strength=args.despill,
            auto_despeckle=True,
            despeckle_size=args.despeckle_size,
        )

        matte = np.clip(result["matte"][:, :, 0], 0.0, 1.0)
        fg = np.clip(result["fg"], 0.0, 1.0)
        rgba = np.dstack([fg, matte])
        rgba_u8 = (rgba * 255.0 + 0.5).astype(np.uint8)
        qc_u8 = (np.clip(result["comp"], 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
        matte_u8 = (matte * 255.0 + 0.5).astype(np.uint8)
        hint_u8 = (alpha_hint * 255.0 + 0.5).astype(np.uint8)

        index = written + 1
        write_rgba(out_dir / "rgba_frames" / f"rgba_{index:05d}.png", rgba_u8)
        write_rgb(out_dir / "qc_frames" / f"qc_{index:05d}.png", qc_u8)
        write_gray(out_dir / "matte_frames" / f"matte_{index:05d}.png", matte_u8)
        write_gray(out_dir / "hint_frames" / f"hint_{index:05d}.png", hint_u8)
        write_rgb(out_dir / "source_frames" / f"source_{index:05d}.png", rgb_u8)

        written += 1
        if written == 1 or written % 10 == 0:
            elapsed = time.time() - start
            print(f"Processed {written} frame(s) in {elapsed:.1f}s")

    cap.release()
    if written == 0:
        raise RuntimeError("No frames were processed.")

    encode_outputs(out_dir, args.fps)
    print(f"Done. Frames: {written}. Output: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
