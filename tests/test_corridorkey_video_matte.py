from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "corridorkey-video-matting"
    / "scripts"
    / "run_corridorkey_video_matte.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("run_corridorkey_video_matte", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frame_limit_uses_smallest_positive_limit():
    module = load_module()

    assert module.frame_limit(0, 0.0, 24.0) is None
    assert module.frame_limit(12, 0.0, 24.0) == 12
    assert module.frame_limit(0, 0.25, 24.0) == 6
    assert module.frame_limit(10, 1.0, 24.0) == 10
    assert module.frame_limit(100, 0.25, 24.0) == 6


def test_resolve_device_cpu_and_cuda_guard(monkeypatch):
    module = load_module()

    assert module.resolve_device("cpu") == "cpu"

    monkeypatch.setattr(module.torch.cuda, "is_available", lambda: False)
    assert module.resolve_device("auto") == "cpu"
    with pytest.raises(RuntimeError, match="CUDA was requested"):
        module.resolve_device("cuda")

    monkeypatch.setattr(module.torch.cuda, "is_available", lambda: True)
    assert module.resolve_device("auto") == "cuda"
    assert module.resolve_device("cuda") == "cuda"
