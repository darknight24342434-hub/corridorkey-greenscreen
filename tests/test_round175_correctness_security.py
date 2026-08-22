from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = ROOT / "skills" / "corridorkey-video-matting" / "scripts" / "run_corridorkey_video_matte.py"
EDGEFIX_SCRIPT = ROOT / "scripts" / "build_edgefix_alpha.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clean_frame_dirs_rejects_untrusted_output_dir_before_deleting(tmp_path):
    module = load_module(RUN_SCRIPT, "round175_run_clean_frame_dirs_guard")
    untrusted = tmp_path / "not_a_corridorkey_output"
    stale = untrusted / "rgba_frames" / "important.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"keep")

    with pytest.raises(ValueError, match="output"):
        module.clean_frame_dirs(untrusted)

    assert stale.exists()


class EmptyPipe:
    def __init__(self):
        self.closed = False

    def read(self, size):
        return b""

    def close(self):
        self.closed = True


class FakeDecoder:
    def __init__(self):
        self.stdout = EmptyPipe()
        self.waited = False

    def wait(self):
        self.waited = True
        return 1


class FakeStdin:
    def __init__(self):
        self.closed = False

    def write(self, data):
        raise AssertionError("no frames should be written")

    def close(self):
        self.closed = True


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


def test_edgefix_process_raises_when_decoders_produce_zero_frames(monkeypatch, tmp_path):
    module = load_module(EDGEFIX_SCRIPT, "round175_edgefix_zero_frames")
    original_decoder = FakeDecoder()
    alpha_decoder = FakeDecoder()
    encoder = FakeEncoder()

    monkeypatch.setattr(module, "media_info", lambda path: (2, 2, 24.0))
    monkeypatch.setattr(
        module,
        "start_decoder",
        lambda path, pix_fmt: original_decoder if pix_fmt == "rgb24" else alpha_decoder,
    )
    monkeypatch.setattr(module, "start_encoder", lambda *args, **kwargs: encoder)

    with pytest.raises(RuntimeError, match="No frames"):
        module.process(tmp_path / "original.mov", tmp_path / "alpha.mov", tmp_path / "out.mov")

    assert original_decoder.stdout.closed is True
    assert alpha_decoder.stdout.closed is True
    assert original_decoder.waited is True
    assert alpha_decoder.waited is True
    assert encoder.stdin.closed is True
    assert encoder.waited is True
