# corridorkey-greenscreen

A research bench for green-screen keying: run the CorridorKey matting model over test footage, compare the alpha it produces against conventional chroma keys, and rebuild a clean composite through DaVinci Resolve with purpose-written despill shaders.

## What it does / why

Chroma keying fails in specific, repeatable places — a green rim on backlit hair, spill on a shoulder facing the screen, an alpha edge that crawls between frames. This repository is the harness that was used to measure those failures and try fixes, rather than a finished keyer.

Three strands:

- **`run_corridorkey_test.py` / `skills/.../run_corridorkey_video_matte.py`** — drive the CorridorKey ComfyUI custom node over a clip, frame by frame, and write the resulting alpha and composite streams.
- **`resolve_tools/*.dctl`** — three generations of a DaVinci Resolve DCTL despill shader. `v3_edge` is the current one: aggressive edge despill for source-RGB-plus-alpha composites.
- **`scripts/build_*.py`** — build the Resolve side of the comparison: a cube-holdout alpha, an edge-fix pass, and a hybrid composite, plus the `.cube` LUT and the project staging that Resolve imports.

`run_corridorkey_batch.ps1` runs the matting stage over a directory of clips with a completion marker per clip, so an interrupted batch resumes rather than restarting.

## Requirements

- **A ComfyUI install with the CorridorKey custom node.** The matting stage shells out to ComfyUI's embedded Python.
- **PyTorch with CUDA** for anything at usable speed. The original work was done on an RTX 2070 with 8 GB, which is why the test footage is pre-resized to 720p.
- `numpy`, `opencv-python`, `Pillow` — see `requirements.txt`.
- **ffmpeg** on `PATH`, or `FFMPEG` pointing at the binary.
- **DaVinci Resolve Studio**, only for the `scripts/build_davinci_*.py` half.

### Configuration

Nothing is hardcoded to a particular machine. Set what you have:

| Variable | Used by | Meaning |
| --- | --- | --- |
| `COMFYUI_ROOT` | matting | ComfyUI portable install root. The CorridorKey node is expected at `ComfyUI/custom_nodes/ComfyUI-CorridorKey`. |
| `FFMPEG` | every encode | Full path to ffmpeg. Defaults to `ffmpeg` on `PATH`. |
| `FFMPEG_DIR` | `run_corridorkey_batch.ps1` | A directory to prepend to `PATH` for the batch. |
| `CORRIDORKEY_SCRIPTS` | batch runner | Where the matting scripts live. Defaults to the copy in this repository. |
| `CORRIDORKEY_BATCH_ROOT` | batch runner | The directory of clips to process. Or pass `-Root`. |
| `BACKSTAGE_WORK_DIR` | Resolve builds | Scratch space for staged media. Defaults to `./_backstage`. |
| `RESOLVE_SCRIPT_API` | Resolve builds | Blackmagic scripting root, if not in the standard location. |
| `RESOLVE_LUT_DIR` | Resolve builds | Where the generated `.cube` is written so Resolve can see it. |

## Install

```
git clone <repo-url> corridorkey-greenscreen
cd corridorkey-greenscreen
pip install -r requirements.txt
```

Then fetch test footage — see below — and point `COMFYUI_ROOT` at your ComfyUI install.

## Test footage

**No video is committed here.** [`SOURCES_AND_LICENSES.md`](SOURCES_AND_LICENSES.md) names every clip that was used, its source page, its licence, and why it was chosen. Each is downloadable from the URL given there.

That file is worth reading before you add footage of your own. Every clip in the original bench was traced to a specific licence — public domain via Voice of America, CC-BY 3.0 with named attribution, CC-BY 4.0 — because a keying comparison you cannot publish is a keying comparison you cannot discuss.

## Usage

Matte a single clip:

```powershell
$env:COMFYUI_ROOT = "D:\ComfyUI_windows_portable"
python .\skills\corridorkey-video-matting\scripts\run_corridorkey_video_matte.py --help
```

Batch a directory, resumable:

```powershell
$env:COMFYUI_ROOT = "D:\ComfyUI_windows_portable"
powershell -ExecutionPolicy Bypass -File .\run_corridorkey_batch.ps1 -Root "D:\clips"
```

It writes `batch_corridorkey.log` and `batch_status.csv` under `<Root>\corridorkey`, and drops a `_corridorkey_complete.txt` marker per finished clip so a re-run skips what is done.

Rebuild the alpha edge:

```powershell
python .\scripts\build_edgefix_alpha.py --help
```

Install a despill shader by copying the `.dctl` into Resolve's LUT directory, then selecting it as a DCTL node in the colour page.

## Tests

```
python -m pytest
```

50 tests, all passing. `pytest.ini` restricts collection to `tests/`, because `run_corridorkey_test.py` matches pytest's default `*_test.py` pattern despite being a runner script.

**The tests need `torch` importable**, because the matting module imports it at the top and several tests monkeypatch `torch.cuda.is_available`. The CPU wheel is enough:

```
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Without it, 24 of the 50 fail at import time with `No module named 'torch'`, which looks like a broken suite and is only a missing dependency.

The suite was written as contracts ahead of the implementation and half of it failed for a long time. The gaps it named are closed:

- **Output-directory guard.** `clean_frame_dirs` deletes and recreates the five frame folders, so it now refuses to touch a directory that this tool did not create. A run writes a `.corridorkey_output` marker into its output folder; a directory that has frame folders but no marker is refused before anything is removed. A fresh, empty directory is always accepted.
- **Frame-rate check.** Both alpha-rebuild scripts reject an alpha clip whose frame rate differs from the original, before any decoder starts. Same size but different rate would otherwise pair frames that silently drift apart.
- **Zero-frame check.** A run where both decoders opened but neither produced a full frame now raises instead of returning `0` and leaving an empty output file that looks like success.

These were checked against real ffmpeg output, not only against the mocks in the tests.

## Limitations

- **This is a bench, not a product.** There is no single entry point that takes a clip and returns a finished key.
- **Nothing runs without ComfyUI plus the CorridorKey node**, which is a separate install this repository does not vendor or version-pin.
- **GPU memory is the ceiling.** The original work resized everything to 720p to fit 8 GB; larger frames will need more.
- **The Resolve half needs Resolve Studio** and drives it through its scripting API, so it cannot run headless or on a machine without a licence.
- **The DCTL shaders are tuned by eye** against the specific test clips. Treat the constants as a starting point.
- **The output-directory marker is a file, not a lock.** `.corridorkey_output` stops the tool from emptying a folder it did not create; it does not stop a person from deleting the marker or placing one by hand.

## License

MIT for the code. See [LICENSE](LICENSE).

Test footage is not distributed here; each clip's own licence is recorded in [`SOURCES_AND_LICENSES.md`](SOURCES_AND_LICENSES.md).
