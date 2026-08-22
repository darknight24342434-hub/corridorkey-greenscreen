from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = ROOT / "skills" / "corridorkey-video-matting" / "scripts" / "run_corridorkey_video_matte.py"
CUBE_SCRIPT = ROOT / "scripts" / "build_cube_holdout_alpha.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_png_reports_encoder_failure(monkeypatch, tmp_path):
    module = load_module(RUN_SCRIPT, "round9_run_write_png")

    monkeypatch.setattr(module.cv2, "imencode", lambda *args, **kwargs: (False, None))

    target = tmp_path / "nested" / "frame.png"
    with pytest.raises(RuntimeError, match="Could not encode PNG"):
        module.write_png(target, np.zeros((2, 2), dtype=np.uint8))

    assert target.parent.is_dir()
    assert not target.exists()


class FakeStreamProcess:
    def __init__(self, poll_value):
        self._poll_value = poll_value
        self.terminated = False

    def poll(self):
        return self._poll_value

    def terminate(self):
        self.terminated = True


def test_terminate_stream_encoders_only_terminates_running_processes():
    module = load_module(RUN_SCRIPT, "round9_run_terminate_streams")
    running = FakeStreamProcess(None)
    exited = FakeStreamProcess(0)

    module.terminate_stream_encoders({"running": running, "exited": exited})

    assert running.terminated is True
    assert exited.terminated is False


def test_encode_outputs_invokes_three_expected_ffmpeg_jobs(monkeypatch, tmp_path):
    module = load_module(RUN_SCRIPT, "round9_run_encode_outputs")
    calls = []

    def fake_run_ffmpeg(command, cwd):
        calls.append((command, cwd))

    monkeypatch.setattr(module, "run_ffmpeg", fake_run_ffmpeg)

    module.encode_outputs(tmp_path, 23.976)

    assert len(calls) == 3
    assert all(cwd == tmp_path for _, cwd in calls)
    assert calls[0][0][-1] == "corridorkey_qc_checkerboard.mp4"
    assert calls[1][0][-1] == "corridorkey_matte.mp4"
    assert calls[2][0][-1] == "corridorkey_transparent_rgba.mov"
    assert all("-framerate" in command for command, _ in calls)


def test_clean_base_alpha_drops_small_top_noise_but_keeps_holdout_overlap():
    module = load_module(CUBE_SCRIPT, "round9_cube_clean_base_alpha")
    base_alpha = np.zeros((100, 100), dtype=np.uint8)
    base_alpha[0:5, 10:20] = 220
    base_alpha[40:45, 40:45] = 180
    holdout = np.zeros_like(base_alpha)
    holdout[42:44, 42:44] = 255

    cleaned = module.clean_base_alpha(base_alpha, holdout)

    assert np.count_nonzero(cleaned[0:5, 10:20]) == 0
    assert np.count_nonzero(cleaned[40:45, 40:45]) > 0


class FakeReadPipe:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def read(self, size):
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def close(self):
        self.closed = True


class FakeStdin:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(data)

    def close(self):
        self.closed = True


class FakeDecoder:
    def __init__(self, chunks):
        self.stdout = FakeReadPipe(chunks)
        self.waited = False

    def wait(self):
        self.waited = True
        return 0


class FakeStderr:
    def read(self):
        return b""


class FakeEncoder:
    def __init__(self):
        self.stdin = FakeStdin()
        self.stderr = FakeStderr()
        self.waited = False

    def wait(self):
        self.waited = True
        return 0


def test_cube_process_holdout_until_zero_copies_alpha_rgba_without_reprocessing(monkeypatch, tmp_path):
    module = load_module(CUBE_SCRIPT, "round9_cube_process_holdout_bypass")
    width, height, fps = 2, 2, 24.0
    rgb_frame = bytes(range(width * height * 3))
    rgba_frame = bytes(range(100, 100 + width * height * 4))
    original_decoder = FakeDecoder([rgb_frame])
    alpha_decoder = FakeDecoder([rgba_frame])
    encoder = FakeEncoder()

    monkeypatch.setattr(module, "media_info", lambda path: (width, height, fps))
    monkeypatch.setattr(
        module,
        "start_decoder",
        lambda path, pix_fmt: original_decoder if pix_fmt == "rgb24" else alpha_decoder,
    )
    monkeypatch.setattr(module, "start_encoder", lambda *args, **kwargs: encoder)
    monkeypatch.setattr(module, "holdout_mask", lambda *args, **kwargs: pytest.fail("holdout should be bypassed"))
    monkeypatch.setattr(module, "edgefix_frame", lambda *args, **kwargs: pytest.fail("edgefix should be bypassed"))

    frames = module.process(tmp_path / "original.mov", tmp_path / "alpha.mov", tmp_path / "out.mov", holdout_until=0)

    assert frames == 1
    assert encoder.stdin.writes == [rgba_frame]
    assert encoder.stdin.closed is True
    assert original_decoder.stdout.closed is True
    assert alpha_decoder.stdout.closed is True
    assert original_decoder.waited is True
    assert alpha_decoder.waited is True
    assert encoder.waited is True
