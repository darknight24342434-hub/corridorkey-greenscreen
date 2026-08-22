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

`pytest.ini` restricts collection to `tests/`, because `run_corridorkey_test.py` matches pytest's default `*_test.py` pattern despite being a runner script — importing it would pull in torch just to collect.

**Half the suite fails, and always has: 22 pass, 28 fail.** As with the rest of this bench, the tests are written as contracts describing what the code *should* do, ahead of it doing so. The failures cluster around:

- **Frame-directory safety** — `clean_frame_dirs` should refuse an untrusted output directory before deleting anything, and recreate only known subfolders.
- **Encoder lifecycle** — stream encoders should report failures on close, terminate only running processes, and open with the expected dimensions and fps.
- **Alpha-hint correctness** — the green-background mask should come back as a float mask, and low-saturation greenish grey should be read as foreground, not background.
- **Node resolution** — a valid `COMFYUI_ROOT` should win, and a failure should report every path searched.
- **Frame limits** — negative and fractional preview limits should be handled, and the smallest positive limit should win.
- **Zero-frame guards** — the edge-fix pass should raise rather than proceed when the decoders produce no frames.

Two contracts that *did* fail — that the ffmpeg binary must not be hardcoded to a single Windows install — now pass, because the paths in this copy are configurable.

## Limitations

- **This is a bench, not a product.** There is no single entry point that takes a clip and returns a finished key.
- **Nothing runs without ComfyUI plus the CorridorKey node**, which is a separate install this repository does not vendor or version-pin.
- **GPU memory is the ceiling.** The original work resized everything to 720p to fit 8 GB; larger frames will need more.
- **The Resolve half needs Resolve Studio** and drives it through its scripting API, so it cannot run headless or on a machine without a licence.
- **The DCTL shaders are tuned by eye** against the specific test clips. Treat the constants as a starting point.
- **Half the test suite fails**, as described above. Read it as a to-do list.

## License

MIT for the code. See [LICENSE](LICENSE).

Test footage is not distributed here; each clip's own licence is recorded in [`SOURCES_AND_LICENSES.md`](SOURCES_AND_LICENSES.md).
