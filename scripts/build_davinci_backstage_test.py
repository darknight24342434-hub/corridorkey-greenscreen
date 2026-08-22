from __future__ import annotations

import csv
import json
import math
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
import os
from pathlib import Path


# Blackmagic's standard scripting location; RESOLVE_SCRIPT_API overrides it.
RESOLVE_MODULES = Path(
    os.environ.get(
        "RESOLVE_SCRIPT_API",
        r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting",
    )
) / "Modules"
# ffmpeg. Set FFMPEG to a full path, or leave it and rely on PATH.
FFMPEG = Path(os.environ.get("FFMPEG", "ffmpeg"))
# Scratch directory for staged media and Resolve imports.
BACKSTAGE = Path(os.environ.get("BACKSTAGE_WORK_DIR", Path.cwd() / "_backstage"))
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DCTL_SOURCE = PROJECT_ROOT / "resolve_tools" / "corridorkey_despill_sidelight.dctl"
# Where the generated .cube lands so Resolve can see it. RESOLVE_LUT_DIR overrides.
LUT_DIR = Path(
    os.environ.get(
        "RESOLVE_LUT_DIR",
        r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT",
    )
) / "CorridorKey"
OUTPUT_ROOT = BACKSTAGE / "corridorkey" / "davinci_composite_test"
IMPORT_STAGE = BACKSTAGE / "resolve_import_staging"
TIMELINE_FPS = 59.94
TIMELINE_W = 3840
TIMELINE_H = 2160


JOBS = [
    {
        "clip_id": "S320260610_9938",
        "background": IMPORT_STAGE / "S320260610_9938_background_1.mp4",
        "foreground": IMPORT_STAGE / "S320260610_9938_corridorkey_rgba.mov",
    },
    {
        "clip_id": "S320260610_9930",
        "background": IMPORT_STAGE / "S320260610_9930_background_2.mp4",
        "foreground": IMPORT_STAGE / "S320260610_9930_corridorkey_rgba.mov",
    },
]


def fail(message: str) -> None:
    raise RuntimeError(message)


def media_info(path: Path) -> dict:
    if not path.exists():
        fail(f"Missing media: {path}")
    proc = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    text = proc.stdout
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not dur_match:
        fail(f"Unable to read duration: {path}\n{text[:1000]}")
    hours, minutes, seconds = dur_match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    video_match = re.search(r"Video:.*?,\s*(\d+)x(\d+).*?,\s*([0-9.]+)\s*fps", text)
    if not video_match:
        fail(f"Unable to read video stream: {path}\n{text[:1000]}")
    width, height, fps = video_match.groups()
    return {
        "path": str(path),
        "duration": duration,
        "width": int(width),
        "height": int(height),
        "fps": float(fps),
        "raw": text,
    }


def install_dctl() -> tuple[Path, str]:
    if not DCTL_SOURCE.exists():
        fail(f"Missing DCTL source: {DCTL_SOURCE}")
    LUT_DIR.mkdir(parents=True, exist_ok=True)
    target = LUT_DIR / DCTL_SOURCE.name
    shutil.copy2(DCTL_SOURCE, target)
    return target, f"CorridorKey/{target.name}"


def import_resolve():
    sys.path.insert(0, str(RESOLVE_MODULES))
    import DaVinciResolveScript as dvr  # type: ignore

    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        fail("Resolve API not connected")
    return resolve


def normalize_path(path: Path) -> str:
    # Keep the ASCII junction path. Resolving this path expands it back to the
    # original S: path with Chinese characters, which Resolve's media importer
    # has failed to ingest reliably from external scripts on this machine.
    return str(path.absolute())


def import_media(resolve, project, paths: list[Path]) -> dict[str, object]:
    media_storage = resolve.GetMediaStorage()
    media_pool = project.GetMediaPool()
    root_folder = media_pool.GetRootFolder()
    media_pool.SetCurrentFolder(root_folder)
    import_paths = [normalize_path(p) for p in paths]
    imported = media_pool.ImportMedia(import_paths) or []
    if not imported:
        imported = media_storage.AddItemListToMediaPool(import_paths) or []
    by_name = {}
    by_path = {}
    for item in imported:
        by_name[item.GetClipProperty("File Name")] = item
        file_path = item.GetClipProperty("File Path")
        if file_path:
            by_path[str(Path(file_path).absolute()).casefold()] = item
    missing = [p for p in paths if normalize_path(p).casefold() not in by_path]
    if missing:
        fail("Resolve did not import: " + ", ".join(str(p) for p in missing))
    return by_path


def ensure_video_tracks(timeline, count: int) -> None:
    while int(timeline.GetTrackCount("video") or 0) < count:
        if not timeline.AddTrack("video"):
            fail("Unable to add video track")


def set_clip_grade(item, dctl_relative: str, dctl_absolute: Path) -> dict:
    result = {
        "cdl": False,
        "lut_relative": False,
        "lut_absolute": False,
        "node_count": None,
        "tools": None,
    }
    result["cdl"] = bool(
        item.SetCDL(
            {
                "NodeIndex": "1",
                "Slope": "1.018 0.968 1.030",
                "Offset": "0.006 0.002 0.010",
                "Power": "1.000 1.000 1.000",
                "Saturation": "0.985",
            }
        )
    )
    graph = item.GetNodeGraph()
    if graph:
        result["node_count"] = int(graph.GetNumNodes() or 0)
        if result["node_count"]:
            result["lut_relative"] = bool(graph.SetLUT(1, dctl_relative))
            if not result["lut_relative"]:
                result["lut_absolute"] = bool(graph.SetLUT(1, str(dctl_absolute)))
            try:
                result["tools"] = graph.GetToolsInNode(1)
            except Exception:
                result["tools"] = None
    return result


def set_background_grade(item) -> bool:
    return bool(
        item.SetCDL(
            {
                "NodeIndex": "1",
                "Slope": "0.985 1.000 1.025",
                "Offset": "0.000 0.000 0.006",
                "Power": "1.000 1.000 1.000",
                "Saturation": "0.940",
            }
        )
    )


def append_job_timeline(project, media_pool, job: dict, imported: dict, dctl_relative: str, dctl_absolute: Path) -> dict:
    clip_id = job["clip_id"]
    bg_info = media_info(job["background"])
    fg_info = media_info(job["foreground"])
    fg_timeline_frames = int(math.ceil(fg_info["duration"] * TIMELINE_FPS))
    bg_source_frames = max(1, int(round(bg_info["duration"] * bg_info["fps"])))
    bg_timeline_frames = max(1, int(round(bg_info["duration"] * TIMELINE_FPS)))
    fg_source_frames = max(1, int(round(fg_info["duration"] * fg_info["fps"])))

    timeline_name = f"{clip_id}_bg_composite"
    timeline = media_pool.CreateEmptyTimeline(timeline_name)
    if not timeline:
        fail(f"Unable to create timeline: {timeline_name}")
    if not project.SetCurrentTimeline(timeline):
        fail(f"Unable to set current timeline: {timeline_name}")
    time.sleep(0.5)
    current_timeline = project.GetCurrentTimeline()
    if not current_timeline or current_timeline.GetName() != timeline_name:
        for idx in range(1, int(project.GetTimelineCount()) + 1):
            candidate = project.GetTimelineByIndex(idx)
            if candidate and candidate.GetName() == timeline_name:
                project.SetCurrentTimeline(candidate)
                current_timeline = project.GetCurrentTimeline()
                break
    if not current_timeline or current_timeline.GetName() != timeline_name:
        fail(f"Unable to activate timeline: {timeline_name}")
    timeline = current_timeline
    timeline.SetSetting("timelineFrameRate", str(TIMELINE_FPS))
    timeline.SetSetting("timelineResolutionWidth", str(TIMELINE_W))
    timeline.SetSetting("timelineResolutionHeight", str(TIMELINE_H))
    ensure_video_tracks(timeline, 2)
    timeline_start_frame = int(timeline.GetStartFrame() or 0)

    bg_item = imported[normalize_path(job["background"]).casefold()]
    fg_item = imported[normalize_path(job["foreground"]).casefold()]

    bg_clip_infos = []
    record = timeline_start_frame
    relative_record = 0
    while relative_record < fg_timeline_frames:
        remaining_timeline_frames = fg_timeline_frames - relative_record
        source_frames_for_this_repeat = min(
            bg_source_frames,
            max(1, int(math.ceil((remaining_timeline_frames / TIMELINE_FPS) * bg_info["fps"]))),
        )
        bg_clip_infos.append(
            {
                "mediaPoolItem": bg_item,
                "startFrame": 0,
                "endFrame": source_frames_for_this_repeat - 1,
                "mediaType": 1,
                "trackIndex": 1,
                "recordFrame": record,
            }
        )
        advance = min(bg_timeline_frames, remaining_timeline_frames)
        record += advance
        relative_record += advance
    bg_timeline_items = media_pool.AppendToTimeline(bg_clip_infos) or []
    if not bg_timeline_items:
        fail(f"Unable to append background: {clip_id}")

    fg_clip_info = {
        "mediaPoolItem": fg_item,
        "startFrame": 0,
        "endFrame": fg_source_frames - 1,
        "mediaType": 1,
        "trackIndex": 2,
        "recordFrame": timeline_start_frame,
    }
    fg_timeline_items = media_pool.AppendToTimeline([fg_clip_info]) or []
    if not fg_timeline_items:
        fg_clip_info.pop("startFrame", None)
        fg_clip_info.pop("endFrame", None)
        fg_timeline_items = media_pool.AppendToTimeline([fg_clip_info]) or []
    if not fg_timeline_items:
        fail(f"Unable to append foreground: {clip_id}")

    grade = set_clip_grade(fg_timeline_items[0], dctl_relative, dctl_absolute)
    bg_grade = [set_background_grade(item) for item in bg_timeline_items]
    fg_timeline_items[0].SetProperty("CompositeMode", 0)
    fg_timeline_items[0].SetProperty("Opacity", 100.0)
    timeline.AddMarker(
        0,
        "Blue",
        "CorridorKey composite",
        "V1 repeated background, V2 transparent CorridorKey MOV. Foreground has DCTL despill, cool grade, and right-side rim light.",
        fg_timeline_frames,
    )

    video_counts = [
        len(timeline.GetItemListInTrack("video", i) or [])
        for i in range(1, int(timeline.GetTrackCount("video") or 0) + 1)
    ]
    return {
        "clip_id": clip_id,
        "timeline_name": timeline_name,
        "foreground": fg_info,
        "background": bg_info,
        "fg_timeline_frames": fg_timeline_frames,
        "bg_repeats": len(bg_timeline_items),
        "video_counts": video_counts,
        "start_frame": int(timeline.GetStartFrame()),
        "end_frame": int(timeline.GetEndFrame()),
        "foreground_grade": grade,
        "background_grade_applied": bg_grade,
        "timeline_start_frame": timeline_start_frame,
    }


def render_timeline(project, timeline, output_dir: Path, custom_name: str) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    project.SetCurrentTimeline(timeline)
    project.DeleteAllRenderJobs()

    codecs = project.GetRenderCodecs("mp4") or {}
    codec = "H264_NVIDIA" if "H.264 NVIDIA" in codecs else "H264"
    if not project.SetCurrentRenderFormatAndCodec("mp4", codec):
        codec = "H264"
        if not project.SetCurrentRenderFormatAndCodec("mp4", codec):
            fail("Unable to set MP4/H.264 render format")

    settings = {
        "SelectAllFrames": False,
        "MarkIn": int(timeline.GetStartFrame() or 0),
        "MarkOut": max(
            [
                int(item.GetEnd())
                for track_index in range(1, int(timeline.GetTrackCount("video") or 0) + 1)
                for item in (timeline.GetItemListInTrack("video", track_index) or [])
            ]
            or [int(timeline.GetStartFrame() or 0)]
        ),
        "TargetDir": str(output_dir),
        "CustomName": custom_name,
        "ExportVideo": True,
        "ExportAudio": False,
        "FormatWidth": TIMELINE_W,
        "FormatHeight": TIMELINE_H,
        "FrameRate": TIMELINE_FPS,
    }
    if not project.SetRenderSettings(settings):
        fail(f"Unable to set render settings for {custom_name}")
    job_id = project.AddRenderJob()
    if not job_id:
        fail(f"Unable to add render job for {custom_name}")
    if not project.StartRendering([job_id], False):
        fail(f"Unable to start render job for {custom_name}")

    last_status = None
    while project.IsRenderingInProgress():
        last_status = project.GetRenderJobStatus(job_id)
        print(f"render {custom_name}: {last_status}")
        time.sleep(10)
    last_status = project.GetRenderJobStatus(job_id)
    out_path = output_dir / f"{custom_name}.mp4"
    return {
        "job_id": job_id,
        "codec": codec,
        "status": last_status,
        "output": str(out_path),
        "exists": out_path.exists(),
        "bytes": out_path.stat().st_size if out_path.exists() else 0,
    }


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    dctl_absolute, dctl_relative = install_dctl()
    resolve = import_resolve()
    project_manager = resolve.GetProjectManager()
    project_name = "Corridorkey_Backstage_DaVinci_Test_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    project = project_manager.CreateProject(project_name)
    if not project:
        fail(f"Unable to create project: {project_name}")
    project.SetSetting("timelineFrameRate", str(TIMELINE_FPS))
    project.SetSetting("timelinePlaybackFrameRate", str(TIMELINE_FPS))
    project.SetSetting("timelineResolutionWidth", str(TIMELINE_W))
    project.SetSetting("timelineResolutionHeight", str(TIMELINE_H))
    project.RefreshLUTList()

    media_paths = []
    for job in JOBS:
        media_paths.extend([job["background"], job["foreground"]])
    imported = import_media(resolve, project, media_paths)
    media_pool = project.GetMediaPool()

    verify = {
        "project_name": project_name,
        "resolve": f"{resolve.GetProductName()} {resolve.GetVersionString()}",
        "dctl_absolute": str(dctl_absolute),
        "dctl_relative": dctl_relative,
        "output_root": str(OUTPUT_ROOT),
        "timelines": [],
        "renders": [],
    }

    for job in JOBS:
        info = append_job_timeline(project, media_pool, job, imported, dctl_relative, dctl_absolute)
        verify["timelines"].append(info)

    project_manager.SaveProject()

    for index in range(1, int(project.GetTimelineCount()) + 1):
        timeline = project.GetTimelineByIndex(index)
        name = timeline.GetName()
        if name.endswith("_bg_composite"):
            render = render_timeline(project, timeline, OUTPUT_ROOT / "renders", name + "_v1")
            verify["renders"].append(render)

    project_manager.SaveProject()
    drp_path = OUTPUT_ROOT / f"{project_name}_final.drp"
    exported = bool(project_manager.ExportProject(project_name, str(drp_path), True))
    verify["drp_path"] = str(drp_path)
    verify["drp_exported"] = exported
    verify["drp_exists"] = drp_path.exists()
    verify["drp_bytes"] = drp_path.stat().st_size if drp_path.exists() else 0

    verify_path = OUTPUT_ROOT / "resolve_build_verify.json"
    verify_path.write_text(json.dumps(verify, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = OUTPUT_ROOT / "resolve_build_verify.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "clip_id",
                "timeline_name",
                "bg_repeats",
                "video_counts",
                "foreground_duration",
                "background_duration",
                "dctl_relative",
                "dctl_absolute",
            ],
        )
        writer.writeheader()
        for row in verify["timelines"]:
            writer.writerow(
                {
                    "clip_id": row["clip_id"],
                    "timeline_name": row["timeline_name"],
                    "bg_repeats": row["bg_repeats"],
                    "video_counts": json.dumps(row["video_counts"]),
                    "foreground_duration": row["foreground"]["duration"],
                    "background_duration": row["background"]["duration"],
                    "dctl_relative": row["foreground_grade"]["lut_relative"],
                    "dctl_absolute": row["foreground_grade"]["lut_absolute"],
                }
            )

    report_path = OUTPUT_ROOT / "resolve_build_report.txt"
    report_path.write_text(
        "\n".join(
            [
                f"Project: {project_name}",
                f"Resolve: {verify['resolve']}",
                f"DCTL: {dctl_absolute}",
                f"Output root: {OUTPUT_ROOT}",
                f"DRP: {drp_path} exported={exported}",
                "",
                "Timelines:",
                *[
                    f"- {row['timeline_name']}: tracks={row['video_counts']} bg_repeats={row['bg_repeats']} dctl={row['foreground_grade']}"
                    for row in verify["timelines"]
                ],
                "",
                "Renders:",
                *[
                    f"- {row['output']} exists={row['exists']} bytes={row['bytes']} status={row['status']}"
                    for row in verify["renders"]
                ],
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(verify, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
