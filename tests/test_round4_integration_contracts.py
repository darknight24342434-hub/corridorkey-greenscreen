from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EDGEFIX_SCRIPT = ROOT / "scripts" / "build_edgefix_alpha.py"
CUBE_SCRIPT = ROOT / "scripts" / "build_cube_holdout_alpha.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("script", "module_name", "alpha_arg_name"),
    [
        (EDGEFIX_SCRIPT, "round4_edgefix_contract", "alpha.mov"),
        (CUBE_SCRIPT, "round4_cube_contract", "base_alpha.mov"),
    ],
)
def test_alpha_rebuild_rejects_same_size_but_different_fps(monkeypatch, tmp_path, script, module_name, alpha_arg_name):
    module = load_module(script, module_name)

    def fake_media_info(path: Path):
        if path.name == "original.mov":
            return (1920, 1080, 59.94)
        return (1920, 1080, 29.97)

    def fail_if_started(*args, **kwargs):
        raise AssertionError("decoding should not start when source and alpha FPS differ")

    monkeypatch.setattr(module, "media_info", fake_media_info)
    monkeypatch.setattr(module, "start_decoder", fail_if_started)
    monkeypatch.setattr(module, "start_encoder", fail_if_started)

    with pytest.raises(RuntimeError, match="FPS mismatch"):
        module.process(tmp_path / "original.mov", tmp_path / alpha_arg_name, tmp_path / "out.mov")


@pytest.mark.parametrize("script", [EDGEFIX_SCRIPT, CUBE_SCRIPT])
def test_alpha_rebuild_ffmpeg_binary_is_not_hardcoded_to_single_windows_install(script):
    module = load_module(script, f"round4_ffmpeg_contract_{script.stem}")

    assert str(module.FFMPEG).lower() in {"ffmpeg", "ffmpeg.exe"}
