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


def test_clean_frame_dirs_refuses_untrusted_output_directory(tmp_path):
    module = load_module(RUN_SCRIPT, "round1_run_clean_frame_dirs")
    unrelated = tmp_path / "unrelated_project"
    stale = unrelated / "rgba_frames" / "keep.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"do not delete")

    with pytest.raises(RuntimeError, match="outside|unsafe|refusing",):
        module.clean_frame_dirs(unrelated)

    assert stale.exists()


class FakePipe:
    def __init__(self, chunks: list[bytes] | None = None):
        self._chunks = list(chunks or [])
        self.closed = False

    def read(self, size: int | None = None) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def close(self) -> None:
        self.closed = True


class FakeDecoder:
    def __init__(self):
        self.stdout = FakePipe()

    def wait(self) -> int:
        return 0


class FakeEncoder:
    def __init__(self):
        self.stdin = FakePipe()
        self.stderr = FakePipe()

    def wait(self) -> int:
        return 0


def test_edgefix_process_rejects_zero_decoded_frames(monkeypatch, tmp_path):
    module = load_module(EDGEFIX_SCRIPT, "round1_edgefix_zero_frames")

    monkeypatch.setattr(module, "media_info", lambda path: (2, 2, 24.0))
    monkeypatch.setattr(module, "start_decoder", lambda path, pix_fmt: FakeDecoder())
    monkeypatch.setattr(module, "start_encoder", lambda *args, **kwargs: FakeEncoder())

    with pytest.raises(RuntimeError, match="No frames|zero frames"):
        module.process(tmp_path / "original.mov", tmp_path / "alpha.mov", tmp_path / "out.mov")
