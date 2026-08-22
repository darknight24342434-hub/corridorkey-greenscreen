from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = ROOT / "skills" / "corridorkey-video-matting" / "scripts" / "run_corridorkey_video_matte.py"
EDGEFIX_SCRIPT = ROOT / "scripts" / "build_edgefix_alpha.py"
CUBE_SCRIPT = ROOT / "scripts" / "build_cube_holdout_alpha.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_add_corridorkey_to_path_prepends_resolved_node(monkeypatch, tmp_path):
    module = load_module(RUN_SCRIPT, "round3_run_add_path")
    node = tmp_path / "node"

    monkeypatch.setattr(module, "resolve_corridorkey_node", lambda: node)
    monkeypatch.setattr(module.sys, "path", ["existing"])

    assert module.add_corridorkey_to_path() == node
    assert module.sys.path[:2] == [str(node), "existing"]


def test_run_ffmpeg_propagates_subprocess_failure(monkeypatch, tmp_path):
    module = load_module(RUN_SCRIPT, "round3_run_ffmpeg")
    calls = []

    def fake_run(command, check, cwd):
        calls.append((command, check, cwd))
        raise subprocess.CalledProcessError(9, command)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError) as exc:
        module.run_ffmpeg(["ffmpeg", "-version"], tmp_path)

    assert exc.value.returncode == 9
    assert calls == [(["ffmpeg", "-version"], True, str(tmp_path))]


@pytest.mark.parametrize(
    ("writer", "input_array", "expected_shape"),
    [
        ("write_rgb", np.zeros((2, 3, 3), dtype=np.uint8), (2, 3, 3)),
        ("write_gray", np.zeros((2, 3), dtype=np.uint8), (2, 3)),
        ("write_rgba", np.zeros((2, 3, 4), dtype=np.uint8), (2, 3, 4)),
    ],
)
def test_image_writer_wrappers_delegate_to_png_encoder(monkeypatch, tmp_path, writer, input_array, expected_shape):
    module = load_module(RUN_SCRIPT, f"round3_run_{writer}")
    captured = []

    def fake_write_png(path, array):
        captured.append((path, array.copy()))

    monkeypatch.setattr(module, "write_png", fake_write_png)

    target = tmp_path / f"{writer}.png"
    getattr(module, writer)(target, input_array)

    assert captured[0][0] == target
    assert captured[0][1].shape == expected_shape


def test_open_stream_encoders_uses_expected_dimensions_fps_and_outputs(monkeypatch, tmp_path):
    module = load_module(RUN_SCRIPT, "round3_run_open_streams")
    calls = []

    class FakePopen:
        stdin = object()

        def __init__(self, command, cwd, stdin):
            calls.append((command, cwd, stdin))

    monkeypatch.setattr(module.subprocess, "Popen", FakePopen)

    processes = module.open_stream_encoders(tmp_path, 29.97, 1920, 1080)

    assert set(processes) == {"qc", "matte", "rgba"}
    assert len(calls) == 3
    assert all(cwd == str(tmp_path) for _, cwd, _ in calls)
    assert all("1920x1080" in command for command, _, _ in calls)
    assert all("29.97" in command for command, _, _ in calls)
    assert {command[-1] for command, _, _ in calls} == {
        "corridorkey_qc_checkerboard.mp4",
        "corridorkey_matte.mp4",
        "corridorkey_transparent_rgba.mov",
    }


@pytest.mark.parametrize("script", [EDGEFIX_SCRIPT, CUBE_SCRIPT])
def test_alpha_rebuild_start_encoder_creates_parent_and_uses_alpha_codec(monkeypatch, tmp_path, script):
    module = load_module(script, f"round3_encoder_{script.stem}")
    calls = []

    class FakePopen:
        def __init__(self, command, stdin, stderr):
            calls.append((command, stdin, stderr))

    monkeypatch.setattr(module.subprocess, "Popen", FakePopen)

    output = tmp_path / "nested" / "out.mov"
    module.start_encoder(output, 640, 360, 23.976)

    command, stdin, stderr = calls[0]
    assert output.parent.is_dir()
    assert stdin == subprocess.PIPE
    assert stderr == subprocess.PIPE
    assert "-c:v" in command and "prores_ks" in command
    assert "-pix_fmt" in command and "yuva444p10le" in command
    assert str(output) == command[-1]


def test_edgefix_read_exact_returns_empty_bytes_at_eof():
    module = load_module(EDGEFIX_SCRIPT, "round3_edgefix_read_exact")

    class EmptyPipe:
        def read(self, size):
            return None

    assert module.read_exact(EmptyPipe(), 128) == b""


@pytest.mark.parametrize("script", [EDGEFIX_SCRIPT, CUBE_SCRIPT])
def test_alpha_rebuild_process_reports_encoder_failure_after_closing_pipes(monkeypatch, tmp_path, script):
    module = load_module(script, f"round3_process_encoder_failure_{script.stem}")
    width, height, fps = 2, 2, 24.0
    rgb_frame = bytes([10] * (width * height * 3))
    rgba_frame = bytes([255] * (width * height * 4))

    class FakeReadPipe:
        def __init__(self, chunk):
            self._chunks = [chunk, b""]
            self.closed = False

        def read(self, size):
            return self._chunks.pop(0)

        def close(self):
            self.closed = True

    class FakeDecoder:
        def __init__(self, chunk):
            self.stdout = FakeReadPipe(chunk)
            self.waited = False

        def wait(self):
            self.waited = True
            return 0

    class FakeStdin:
        def __init__(self):
            self.closed = False

        def write(self, data):
            assert data

        def close(self):
            self.closed = True

    class FakeStderr:
        def read(self):
            return b"encoder exploded"

    class FakeEncoder:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stderr = FakeStderr()

        def wait(self):
            return 23

    original_decoder = FakeDecoder(rgb_frame)
    alpha_decoder = FakeDecoder(rgba_frame)
    encoder = FakeEncoder()

    monkeypatch.setattr(module, "media_info", lambda path: (width, height, fps))
    monkeypatch.setattr(
        module,
        "start_decoder",
        lambda path, pix_fmt: original_decoder if pix_fmt == "rgb24" else alpha_decoder,
    )
    monkeypatch.setattr(module, "start_encoder", lambda *args, **kwargs: encoder)
    if script == CUBE_SCRIPT:
        monkeypatch.setattr(module, "holdout_mask", lambda rgb, alpha: np.zeros((height, width), dtype=np.uint8))
        monkeypatch.setattr(module, "clean_base_alpha", lambda alpha, holdout: alpha)
        process_args = (tmp_path / "original.mov", tmp_path / "alpha.mov", tmp_path / "out.mov", 99.0)
    else:
        process_args = (tmp_path / "original.mov", tmp_path / "alpha.mov", tmp_path / "out.mov")

    with pytest.raises(RuntimeError, match="encoder failed with code 23"):
        module.process(*process_args)

    assert encoder.stdin.closed is True
    assert original_decoder.stdout.closed is True
    assert alpha_decoder.stdout.closed is True
    assert original_decoder.waited is True
    assert alpha_decoder.waited is True


def test_cube_holdout_mask_ignores_pure_green_screen_background():
    module = load_module(CUBE_SCRIPT, "round3_cube_holdout_green")
    rgb = np.zeros((80, 120, 3), dtype=np.uint8)
    rgb[:, :] = [20, 210, 20]
    alpha = np.zeros((80, 120), dtype=np.uint8)

    mask = module.holdout_mask(rgb, alpha)

    assert mask.shape == alpha.shape
    assert int(np.count_nonzero(mask > 15)) == 0


def test_cube_clean_base_alpha_drops_all_small_disconnected_noise():
    module = load_module(CUBE_SCRIPT, "round3_cube_clean_noise")
    base_alpha = np.zeros((100, 100), dtype=np.uint8)
    base_alpha[50:52, 50:52] = 255
    holdout = np.zeros_like(base_alpha)

    cleaned = module.clean_base_alpha(base_alpha, holdout)

    assert np.count_nonzero(cleaned) == 0
