from __future__ import annotations

import importlib.util
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


@pytest.mark.parametrize(
    ("max_frames", "seconds", "fps", "expected"),
    [
        (-5, -1.0, 24.0, None),
        (0, 0.001, 24.0, 1),
        (2, 10.0, 24.0, 2),
        (999, 0.0417, 23.976, 1),
    ],
)
def test_frame_limit_handles_negative_and_fractional_preview_limits(max_frames, seconds, fps, expected):
    module = load_module(RUN_SCRIPT, "round171_run_frame_limit")

    assert module.frame_limit(max_frames, seconds, fps) == expected


def test_make_alpha_hint_treats_low_saturation_greenish_gray_as_foreground():
    module = load_module(RUN_SCRIPT, "round171_run_alpha_hint_gray")
    rgb = np.full((32, 32, 3), [92, 105, 92], dtype=np.uint8)

    alpha = module.make_alpha_hint(rgb)

    assert alpha.dtype == np.float32
    assert float(alpha.mean()) > 0.95


def test_clean_frame_dirs_recreates_only_known_frame_folders(tmp_path):
    module = load_module(RUN_SCRIPT, "round171_run_clean_frame_dirs")
    out_dir = tmp_path / "corridorkey_out"
    keep = out_dir / "notes.txt"
    stale = out_dir / "matte_frames" / "old.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale")
    keep.write_text("keep", encoding="utf-8")
    # The directory must carry the tool's own marker, or clean_frame_dirs refuses
    # to delete anything in it (see the round 1 / round 175 guard contracts).
    (out_dir / module.OUTPUT_MARKER).touch()

    module.clean_frame_dirs(out_dir)

    assert keep.read_text(encoding="utf-8") == "keep"
    assert not stale.exists()
    assert sorted(path.name for path in out_dir.iterdir() if path.is_dir()) == [
        "hint_frames",
        "matte_frames",
        "qc_frames",
        "rgba_frames",
        "source_frames",
    ]


@pytest.mark.parametrize("script", [EDGEFIX_SCRIPT, CUBE_SCRIPT])
def test_smoothstep_clamps_out_of_range_values(script):
    module = load_module(script, f"round171_smoothstep_{script.stem}")
    values = np.array([-10.0, 0.0, 0.5, 1.0, 10.0], dtype=np.float32)

    stepped = module.smoothstep(values)

    np.testing.assert_allclose(stepped, np.array([0.0, 0.0, 0.5, 1.0, 1.0], dtype=np.float32))


@pytest.mark.parametrize("script", [EDGEFIX_SCRIPT, CUBE_SCRIPT])
def test_edgefix_frame_reduces_green_spill_when_alpha_marks_foreground(script):
    module = load_module(script, f"round171_edgefix_spill_{script.stem}")
    rgb = np.full((12, 12, 3), [80, 220, 85], dtype=np.uint8)
    alpha = np.full((12, 12), 255, dtype=np.uint8)

    fixed = module.edgefix_frame(rgb, alpha)

    assert fixed.shape == (12, 12, 4)
    assert float(fixed[:, :, 1].mean()) < float(rgb[:, :, 1].mean())
    assert int(fixed[:, :, 3].min()) >= 250


def test_cube_holdout_mask_keeps_pale_object_when_it_overlaps_base_alpha():
    module = load_module(CUBE_SCRIPT, "round171_cube_holdout_detect")
    rgb = np.full((120, 160, 3), [20, 210, 20], dtype=np.uint8)
    rgb[45:95, 60:115] = [190, 185, 176]
    alpha = np.zeros((120, 160), dtype=np.uint8)
    alpha[55:75, 70:90] = 255

    mask = module.holdout_mask(rgb, alpha)

    assert mask.shape == alpha.shape
    assert int(np.count_nonzero(mask[50:90, 65:110] > 15)) > 1000
    assert int(np.count_nonzero(mask[:20, :] > 15)) == 0


def test_cube_clean_base_alpha_keeps_large_connected_foreground_component():
    module = load_module(CUBE_SCRIPT, "round171_cube_clean_large_component")
    base_alpha = np.zeros((100, 120), dtype=np.uint8)
    base_alpha[35:85, 30:90] = 220
    holdout = np.zeros_like(base_alpha)

    cleaned = module.clean_base_alpha(base_alpha, holdout)

    assert int(np.count_nonzero(cleaned[35:85, 30:90])) == 50 * 60
    assert int(cleaned[10, 10]) == 0
