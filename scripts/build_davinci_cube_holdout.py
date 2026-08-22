from __future__ import annotations

import csv
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path


BASE_SCRIPT = Path(__file__).with_name("build_davinci_backstage_test.py")
spec = importlib.util.spec_from_file_location("backstage_resolve_base", BASE_SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to import {BASE_SCRIPT}")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


OUTPUT_ROOT = base.OUTPUT_ROOT
IMPORT_STAGE = base.IMPORT_STAGE
TIMELINE_FPS = base.TIMELINE_FPS
TIMELINE_W = base.TIMELINE_W
TIMELINE_H = base.TIMELINE_H

base.DCTL_SOURCE = base.PROJECT_ROOT / "resolve_tools" / "corridorkey_despill_sidelight_v3_edge.dctl"

JOBS = [
    {
        "clip_id": "S320260610_9938",
        "background": IMPORT_STAGE / "S320260610_9938_background_1.mp4",
        "foreground": IMPORT_STAGE / "S320260610_9938_cubeholdout_alpha_prores4444.mov",
    },
    {
        "clip_id": "S320260610_9930",
        "background": IMPORT_STAGE / "S320260610_9930_background_2.mp4",
        "foreground": IMPORT_STAGE / "S320260610_9930_cubeholdout_alpha_prores4444.mov",
    },
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    dctl_absolute, dctl_relative = base.install_dctl()
    resolve = base.import_resolve()
    project_manager = resolve.GetProjectManager()
    project_name = "Corridorkey_Backstage_DaVinci_Test_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_cubeholdout"
    project = project_manager.CreateProject(project_name)
    if not project:
        base.fail(f"Unable to create project: {project_name}")

    project.SetSetting("timelineFrameRate", str(TIMELINE_FPS))
    project.SetSetting("timelinePlaybackFrameRate", str(TIMELINE_FPS))
    project.SetSetting("timelineResolutionWidth", str(TIMELINE_W))
    project.SetSetting("timelineResolutionHeight", str(TIMELINE_H))
    project.RefreshLUTList()

    media_paths = []
    for job in JOBS:
        media_paths.extend([job["background"], job["foreground"]])
    imported = base.import_media(resolve, project, media_paths)
    media_pool = project.GetMediaPool()

    verify = {
        "project_name": project_name,
        "resolve": f"{resolve.GetProductName()} {resolve.GetVersionString()}",
        "dctl_absolute": str(dctl_absolute),
        "dctl_relative": dctl_relative,
        "output_root": str(OUTPUT_ROOT),
        "timelines": [],
        "renders": [],
        "method": "cube_holdout_source_rgb_alpha",
    }

    for job in JOBS:
        info = base.append_job_timeline(project, media_pool, job, imported, dctl_relative, dctl_absolute)
        timeline = project.GetCurrentTimeline()
        timeline_name = f"{job['clip_id']}_bg_composite_v6_cubeholdout"
        if timeline and timeline.SetName(timeline_name):
            info["timeline_name"] = timeline_name
        info["foreground_holdout"] = str(job["foreground"])
        verify["timelines"].append(info)
        time.sleep(0.3)

    project_manager.SaveProject()

    for index in range(1, int(project.GetTimelineCount()) + 1):
        timeline = project.GetTimelineByIndex(index)
        if not timeline:
            continue
        name = timeline.GetName()
        if name.endswith("_v6_cubeholdout"):
            render = base.render_timeline(project, timeline, OUTPUT_ROOT / "renders", name)
            verify["renders"].append(render)

    project_manager.SaveProject()
    drp_path = OUTPUT_ROOT / f"{project_name}_v6_cubeholdout.drp"
    exported = bool(project_manager.ExportProject(project_name, str(drp_path), True))
    verify["drp_path"] = str(drp_path)
    verify["drp_exported"] = exported
    verify["drp_exists"] = drp_path.exists()
    verify["drp_bytes"] = drp_path.stat().st_size if drp_path.exists() else 0

    verify_path = OUTPUT_ROOT / "resolve_v6_cubeholdout_verify.json"
    verify_path.write_text(json.dumps(verify, ensure_ascii=True, indent=2), encoding="utf-8")

    csv_path = OUTPUT_ROOT / "resolve_v6_cubeholdout_verify.csv"
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
                "foreground_holdout",
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
                    "foreground_holdout": row["foreground_holdout"],
                    "dctl_relative": row["foreground_grade"]["lut_relative"],
                    "dctl_absolute": row["foreground_grade"]["lut_absolute"],
                }
            )

    report_path = OUTPUT_ROOT / "resolve_v6_cubeholdout_report.txt"
    report_path.write_text(
        "\n".join(
            [
                f"Project: {project_name}",
                f"Resolve: {verify['resolve']}",
                f"DCTL: {dctl_absolute}",
                f"Method: cube holdout source RGB + alpha",
                f"Output root: {OUTPUT_ROOT}",
                f"DRP: {drp_path} exported={exported}",
                "",
                "Timelines:",
                *[
                    f"- {row['timeline_name']}: tracks={row['video_counts']} bg_repeats={row['bg_repeats']} foreground={row['foreground_holdout']}"
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
    print(json.dumps(verify, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
