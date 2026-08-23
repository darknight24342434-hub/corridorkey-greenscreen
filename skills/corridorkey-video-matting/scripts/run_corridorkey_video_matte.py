from __future__ import annotations

import argparse
import math
import os
import shutil
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


FRAME_DIRS = ["rgba_frames", "qc_frames", "matte_frames", "hint_frames", "source_frames"]
OUTPUT_MARKER = ".corridorkey_output"


class UnsafeOutputDirectoryError(ValueError, RuntimeError):
    """clean_frame_dirs was pointed at a directory this tool did not create.

    It is both a bad argument (ValueError) and a refusal to act (RuntimeError), so
    either style of handler catches it.
    """


def mark_output_dir(out_dir: Path) -> None:
    (out_dir / OUTPUT_MARKER).write_text(
        "Created by run_corridorkey_video_matte.py. The frame folders beside this file are\n"
        "deleted and recreated on every run; do not keep anything else in them.\n",
        encoding="utf-8",
    )


def clean_frame_dirs(out_dir: Path) -> None:
    """Empty and recreate the five frame folders under out_dir.

    This deletes directories, so it only proceeds when there is nothing to delete or
    when the directory carries the marker this tool writes to every output folder it
    owns. An unrelated directory that merely happens to contain an rgba_frames folder
    is refused before anything is removed.
    """
    existing = [child for child in FRAME_DIRS if (out_dir / child).exists()]
    if existing and not (out_dir / OUTPUT_MARKER).exists():
        raise UnsafeOutputDirectoryError(
            f"Refusing to delete {', '.join(existing)} under {out_dir}: it is not marked as a "
            f"corridorkey output directory (no {OUTPUT_MARKER} file), so it may be outside this "
            "tool's control. Point --output-dir at a fresh folder or at one this tool generated."
        )
    for child in FRAME_DIRS:
        path = out_dir / child
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    mark_output_dir(out_dir)


def run_ffmpeg(command: list[str], cwd: Path) -> None:
    subprocess.run(command, check=True, cwd=str(cwd))


def open_stream_encoders(out_dir: Path, fps: float, width: int, height: int) -> dict[str, subprocess.Popen]:
    size = f"{width}x{height}"
    specs = {
        "qc": [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s:v",
            size,
            "-framerate",
            f"{fps:g}",
            "-i",
            "pipe:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "corridorkey_qc_checkerboard.mp4",
        ],
        "matte": [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-s:v",
            size,
            "-framerate",
            f"{fps:g}",
            "-i",
            "pipe:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "corridorkey_matte.mp4",
        ],
        "rgba": [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-s:v",
            size,
            "-framerate",
            f"{fps:g}",
            "-i",
            "pipe:0",
            "-c:v",
            "qtrle",
            "-pix_fmt",
            "argb",
            "corridorkey_transparent_rgba.mov",
        ],
    }
    processes: dict[str, subprocess.Popen] = {}
    for name, command in specs.items():
        processes[name] = subprocess.Popen(command, cwd=str(out_dir), stdin=subprocess.PIPE)
    return processes


def close_stream_encoders(processes: dict[str, subprocess.Popen] | None) -> None:
    if not processes:
        return
    errors: list[str] = []
    for name, process in processes.items():
        if process.stdin:
            process.stdin.close()
        return_code = process.wait()
        if return_code != 0:
            errors.append(f"{name} ffmpeg exited with code {return_code}")
    if errors:
        raise RuntimeError("; ".join(errors))


def terminate_stream_encoders(processes: dict[str, subprocess.Popen] | None) -> None:
    if not processes:
        return
    for process in processes.values():
        if process.poll() is None:
            process.terminate()


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
        run_ffmpeg(command, out_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run CorridorKey green-screen video matting and export alpha MOV plus QC files."
    )
    parser.add_argument("--input", required=True, help="Input green-screen video.")
    parser.add_argument("--output-dir", required=True, help="Output folder for frames and encoded files.")
    parser.add_argument("--img-size", type=int, default=1024, help="CorridorKey inference image size.")
    parser.add_argument("--max-frames", type=int, default=0, help="Maximum processed output frames. 0 means no limit.")
    parser.add_argument("--seconds", type=float, default=0.0, help="Preview/export duration in output seconds. 0 means no limit.")
    parser.add_argument("--start-frame", type=int, default=0, help="Source frame to start from.")
    parser.add_argument("--start-time", type=float, default=0.0, help="Source time in seconds to start from.")
    parser.add_argument("--sample-step", type=int, default=1, help="Process every Nth source frame.")
    parser.add_argument("--fps", type=float, default=0.0, help="Output FPS. 0 means source FPS divided by sample-step.")
    parser.add_argument("--despill", type=float, default=1.0, help="CorridorKey despill strength.")
    parser.add_argument("--despeckle-size", type=int, default=100, help="Auto despeckle size. Use 0 to preserve fine details.")
    parser.add_argument("--refiner-scale", type=float, default=1.0, help="CorridorKey refiner scale.")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto", help="Inference device.")
    parser.add_argument("--keep-existing-frames", action="store_true", help="Do not clear existing frame folders before running.")
    parser.add_argument(
        "--stream-encode",
        action="store_true",
        help="Pipe processed frames directly to ffmpeg instead of writing PNG frame folders.",
    )
    return parser.parse_args()


def resolve_device(value: str) -> str:
    if value == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false.")
        return "cuda"
    if value == "cpu":
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def frame_limit(max_frames: int, seconds: float, fps: float) -> int | None:
    limits: list[int] = []
    if max_frames > 0:
        limits.append(max_frames)
    if seconds > 0:
        limits.append(max(1, math.ceil(seconds * fps)))
    if not limits:
        return None
    return min(limits)


def main() -> int:
    args = parse_args()

    if args.sample_step < 1:
        raise ValueError("--sample-step must be >= 1")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg was not found on PATH.")

    corridorkey_node = add_corridorkey_to_path()
    from corridor_key.engine import CorridorKeyEngine, resolve_checkpoint_path

    src = Path(args.input)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not args.keep_existing_frames:
        clean_frame_dirs(out_dir)
    else:
        # Frame folders that already exist are kept as they are. We only claim the
        # directory as ours (so a later run may clean it) when we created them.
        pre_existing = [child for child in FRAME_DIRS if (out_dir / child).exists()]
        for child in FRAME_DIRS:
            (out_dir / child).mkdir(parents=True, exist_ok=True)
        if not pre_existing:
            mark_output_dir(out_dir)

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open input video: {src}")

    source_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if source_fps <= 0:
        source_fps = 30.0
    output_fps = args.fps if args.fps > 0 else source_fps / args.sample_step
    output_fps = max(output_fps, 1.0)
    max_written = frame_limit(args.max_frames, args.seconds, output_fps)

    start_frame = args.start_frame
    if args.start_time > 0:
        start_frame = max(start_frame, int(round(args.start_time * source_fps)))
    if start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    device = resolve_device(args.device)
    checkpoint_path = resolve_checkpoint_path()
    print(f"Using CorridorKey node: {corridorkey_node}")
    print(f"Loading CorridorKey checkpoint: {checkpoint_path}")
    print(f"Using device: {device} img_size={args.img_size}")
    print(f"Source FPS: {source_fps:g} output FPS: {output_fps:g} sample-step={args.sample_step}")

    engine = CorridorKeyEngine(
        checkpoint_path=checkpoint_path,
        device=device,
        img_size=args.img_size,
        use_refiner=True,
    )

    written = 0
    read_index = start_frame
    start = time.time()
    stream_encoders: dict[str, subprocess.Popen] | None = None
    try:
        while max_written is None or written < max_written:
            ok, bgr = cap.read()
            if not ok:
                break
            if (read_index - start_frame) % args.sample_step != 0:
                read_index += 1
                continue
            read_index += 1

            rgb_u8 = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            alpha_hint = make_alpha_hint(rgb_u8)
            rgb_float = rgb_u8.astype(np.float32) / 255.0

            result = engine.process_frame(
                image=rgb_float,
                mask_linear=alpha_hint,
                refiner_scale=args.refiner_scale,
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

            if args.stream_encode:
                if stream_encoders is None:
                    height, width = matte_u8.shape[:2]
                    stream_encoders = open_stream_encoders(out_dir, output_fps, width, height)
                assert stream_encoders["rgba"].stdin is not None
                assert stream_encoders["qc"].stdin is not None
                assert stream_encoders["matte"].stdin is not None
                stream_encoders["rgba"].stdin.write(np.ascontiguousarray(rgba_u8).tobytes())
                stream_encoders["qc"].stdin.write(np.ascontiguousarray(qc_u8).tobytes())
                stream_encoders["matte"].stdin.write(np.ascontiguousarray(matte_u8).tobytes())
            else:
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
    except Exception:
        terminate_stream_encoders(stream_encoders)
        raise
    finally:
        cap.release()
    if written == 0:
        terminate_stream_encoders(stream_encoders)
        raise RuntimeError("No frames were processed.")

    if args.stream_encode:
        close_stream_encoders(stream_encoders)
    else:
        encode_outputs(out_dir, output_fps)
    print(f"Done. Frames: {written}. Output: {out_dir}")
    print(f"Alpha MOV: {out_dir / 'corridorkey_transparent_rgba.mov'}")
    print(f"QC MP4: {out_dir / 'corridorkey_qc_checkerboard.mp4'}")
    print(f"Matte MP4: {out_dir / 'corridorkey_matte.mp4'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
