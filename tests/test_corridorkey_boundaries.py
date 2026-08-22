from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = ROOT / "skills" / "corridorkey-video-matting" / "scripts" / "run_corridorkey_video_matte.py"
CUBE_SCRIPT = ROOT / "scripts" / "build_cube_holdout_alpha.py"
EDGEFIX_SCRIPT = ROOT / "scripts" / "build_edgefix_alpha.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_corridorkey_node_prefers_valid_env_path(tmp_path, monkeypatch):
    module = load_module(RUN_SCRIPT, "run_corridorkey_video_matte_env")
    node = tmp_path / "node"
    (node / "corridor_key").mkdir(parents=True)
    (node / "models").mkdir()
    (node / "models" / "CorridorKey.pth").write_bytes(b"checkpoint")

    monkeypatch.setenv("CORRIDORKEY_NODE", str(node))
    monkeypatch.setattr(module, "CORRIDORKEY_NODE_CANDIDATES", [tmp_path / "missing"])

    assert module.resolve_corridorkey_node() == node


def test_resolve_corridorkey_node_reports_all_searched_paths(tmp_path, monkeypatch):
    module = load_module(RUN_SCRIPT, "run_corridorkey_video_matte_missing")
    env_node = tmp_path / "env-node"
    fallback = tmp_path / "fallback-node"

    monkeypatch.setenv("CORRIDORKEY_NODE", str(env_node))
    monkeypatch.setattr(module, "CORRIDORKEY_NODE_CANDIDATES", [fallback])

    with pytest.raises(FileNotFoundError) as exc:
        module.resolve_corridorkey_node()

    message = str(exc.value)
    assert str(env_node) in message
    assert str(fallback) in message


def test_make_alpha_hint_returns_float_mask_with_green_background_transparent():
    module = load_module(RUN_SCRIPT, "run_corridorkey_video_matte_alpha")
    rgb = np.zeros((40, 80, 3), dtype=np.uint8)
    rgb[:, :40] = [20, 220, 20]
    rgb[:, 40:] = [220, 30, 30]

    alpha = module.make_alpha_hint(rgb)

    assert alpha.shape == rgb.shape[:2]
    assert alpha.dtype == np.float32
    assert 0.0 <= float(alpha.min()) <= float(alpha.max()) <= 1.0
    assert float(alpha[:, :30].mean()) < 0.20
    assert float(alpha[:, 50:].mean()) > 0.80


def test_clean_frame_dirs_recreates_known_output_subdirs(tmp_path):
    module = load_module(RUN_SCRIPT, "run_corridorkey_video_matte_clean")
    out_dir = tmp_path / "out"
    stale = out_dir / "rgba_frames" / "old.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale")

    module.clean_frame_dirs(out_dir)

    for child in ["rgba_frames", "qc_frames", "matte_frames", "hint_frames", "source_frames"]:
        assert (out_dir / child).is_dir()
    assert not stale.exists()


class FakeProcess:
    def __init__(self, code: int):
        self.stdin = FakeStdin()
        self._code = code
        self.wait_called = False

    def wait(self) -> int:
        self.wait_called = True
        return self._code


class FakeStdin:
    def __init__(self):
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_close_stream_encoders_closes_all_pipes_and_reports_failures():
    module = load_module(RUN_SCRIPT, "run_corridorkey_video_matte_encoders")
    ok = FakeProcess(0)
    bad = FakeProcess(7)

    with pytest.raises(RuntimeError, match="matte ffmpeg exited with code 7"):
        module.close_stream_encoders({"qc": ok, "matte": bad})

    assert ok.stdin.closed
    assert bad.stdin.closed
    assert ok.wait_called
    assert bad.wait_called


def test_media_info_parses_ffmpeg_video_line(monkeypatch):
    module = load_module(EDGEFIX_SCRIPT, "build_edgefix_alpha_media")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="Video: h264, yuv420p, 1920x1080, 59.94 fps")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.media_info(Path("clip.mp4")) == (1920, 1080, 59.94)


def test_media_info_raises_when_ffmpeg_output_has_no_video_line(monkeypatch):
    module = load_module(EDGEFIX_SCRIPT, "build_edgefix_alpha_media_fail")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout="not a media file")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Could not parse video info"):
        module.media_info(Path("broken.mp4"))


def test_cube_process_rejects_mismatched_media_sizes(monkeypatch, tmp_path):
    module = load_module(CUBE_SCRIPT, "build_cube_holdout_alpha_process")

    def fake_media_info(path: Path):
        if path.name == "original.mov":
            return (640, 480, 24.0)
        return (320, 480, 24.0)

    monkeypatch.setattr(module, "media_info", fake_media_info)

    with pytest.raises(RuntimeError, match="Size mismatch"):
        module.process(tmp_path / "original.mov", tmp_path / "alpha.mov", tmp_path / "out.mov")


def test_fill_holes_fills_enclosed_background_pixel():
    module = load_module(CUBE_SCRIPT, "build_cube_holdout_alpha_fill")
    mask = np.zeros((7, 7), dtype=np.uint8)
    mask[1:6, 1:6] = 255
    mask[3, 3] = 0

    filled = module.fill_holes(mask)

    assert filled[3, 3] == 255
    assert filled[0, 0] == 0


def test_edgefix_frame_preserves_shape_dtype_and_alpha_channel():
    module = load_module(CUBE_SCRIPT, "build_cube_holdout_alpha_edgefix")
    rgb = np.zeros((8, 8, 3), dtype=np.uint8)
    rgb[:, :] = [80, 210, 90]
    alpha = np.full((8, 8), 255, dtype=np.uint8)

    fixed = module.edgefix_frame(rgb, alpha)

    assert fixed.shape == (8, 8, 4)
    assert fixed.dtype == np.uint8
    assert fixed[:, :, 3].min() >= 250
