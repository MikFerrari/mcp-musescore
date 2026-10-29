"""Repeat structure tools for MuseScore MCP: find repeated material and mark it."""

import re
import shutil
from pathlib import Path
from typing import Annotated, Dict, List, Literal, Optional, Tuple

from fastmcp.exceptions import ToolError

from ..client import MuseScoreClient
from ..mscz import ScoreFileError
from ..mscz.locate import find_score_file
from ..utils.repetition import find_repeats, staff_fingerprints
from ..utils.sections import find_sections
from .formatting import range_params

FETCH_CHUNK = 16
PASSAGE_COLORS = ["#1f77b4", "#2ca02c", "#9467bd", "#8c564b", "#e377c2", "#17becf"]
MEASURE_REPEAT_COLOR = "#ff7f0e"


def _contiguous(indices: List[int]) -> List[List[int]]:
    """Split sorted staff indices into [first, last] runs of consecutive staves."""
    runs: List[List[int]] = []
    for idx in sorted(indices):
        if runs and runs[-1][1] + 1 == idx:
            runs[-1][1] = idx
        else:
            runs.append([idx, idx])
    return runs


def _apply_steps(passage: Dict) -> List[str]:
    """Tool calls that compress one passage, ordered so earlier measure numbers stay valid."""
    dup_first, dup_last = passage["duplicate_measures"]
    if passage["type"] == "repeat":
        return [
            f"delete_measures({dup_first}, {dup_last})",
            f"set_repeat_barlines({passage['start']}, {passage['end']}, play_count={passage['times']})",
        ]
    first_ending, second_ending = passage["first_ending"], passage["second_ending"]
    return [
        f"add_volta({second_ending[0]}, {second_ending[1]}, endings=[2])",
        f"delete_measures({dup_first}, {dup_last})",
        f"add_volta({first_ending[0]}, {first_ending[1]}, endings=[1])",
        f"set_repeat_barlines({passage['start']}, {first_ending[1]})",
    ]


async def fetch_measures(client: MuseScoreClient, start_measure: int, end_measure: Optional[int],
                         staves: Optional[List[int]]) -> Tuple[List[Dict], List[Dict]]:
    """Read measures start..end (default: to the end) in chunks; returns (measures, staff info)."""
    params = {"startMeasure": start_measure, "endMeasure": start_measure + FETCH_CHUNK - 1}
    if staves:
        params["staves"] = staves
    analysis = (await client.request("getScore", params))["analysis"]
    total = analysis.get("numMeasures", 0)
    last = min(end_measure, total) if end_measure is not None else total
    measures = [m for m in analysis.get("measures", []) if m["measure"] <= last]

    next_start = params["endMeasure"] + 1
    while next_start <= last:
        chunk = {**params, "startMeasure": next_start, "endMeasure": min(next_start + FETCH_CHUNK - 1, last)}
        measures.extend((await client.request("getScore", chunk))["analysis"].get("measures", []))
        next_start = chunk["endMeasure"] + 1
    return measures, analysis.get("staves", [])


def _road_map_line(section: Dict) -> str:
    """One line of the section map, e.g. "Bars 6-13: groove bar 6, 5 exact copies, near 9, fill 13"."""
    parts = [f"groove bar {section['groove_bar']}"]
    if section["exact"]:
        parts.append(f"{len(section['exact'])} exact cop{'y' if len(section['exact']) == 1 else 'ies'}")
    if section["near"]:
        parts.append("near " + ", ".join(str(n["bar"]) for n in section["near"]))
    if section["fills"]:
        parts.append("fill " + ", ".join(str(b) for b in section["fills"]))
    return f"Bars {section['start']}-{section['end']}: " + ", ".join(parts)


def setup_structure_tools(mcp, client: MuseScoreClient):
    """Setup repeat structure tools."""

    async def staff_header() -> List[Dict]:
        analysis = (await client.request(
            "getScore", {"startMeasure": 1, "endMeasure": 1, "includeElements": False}
        ))["analysis"]
        return analysis.get("staves", [])

    @mcp.tool(
        description=(
            "Find repeated material in the score. Returns (1) per-staff bars that repeat the previous 1, 2 or 4 bars, "
            "candidates for measure-repeat signs (these declutter the page but keep the length), and (2) whole-score "
            "passages played again right away, candidates for repeat barlines and 1st/2nd endings (these shorten the "
            "score once the duplicate measures are deleted). Each passage lists the tool calls that apply it. "
            "Reads the score in small chunks, so it works on large scores. Measure numbers refer to the score as it "
            "is now: apply passages from the last one backwards."
        ),
        tags={"structure"},
    )
    async def find_repeated_measures(
        start_measure: Annotated[int, "First measure to analyse (1-based)"] = 1,
        end_measure: Annotated[Optional[int], "Last measure to analyse (1-based, inclusive). Defaults to the end of the score."] = None,
        staves: Annotated[Optional[List[int]], "Staff indices to analyse (0-based). Defaults to all staves."] = None,
        highlight: Annotated[
            bool,
            "Also color the results in the score: duplicate measures of each passage in a passage color, "
            "per-staff measure repeats in orange. Undo or clear_color removes it.",
        ] = False,
    ):
        measures, staff_info = await fetch_measures(client, start_measure, end_measure, staves)
        staff_names = [st["name"] for st in staff_info]
        found = find_repeats(measures, staff_names)
        for passage in found["passages"]:
            passage["apply"] = _apply_steps(passage)

        result = {
            "measures_analysed": [measures[0]["measure"], measures[-1]["measure"]] if measures else [],
            "staves_analysed": [st.get("index", i) for i, st in enumerate(staff_info)],
            "passages": found["passages"],
            "measure_repeats": found["measure_repeats"],
            "notes": [
                "Apply passages from the last one backwards: delete_measures renumbers later measures.",
                "measure_repeats use the current measure numbers; re-run this tool after deleting measures.",
                "Place measure-repeat signs with apply_measure_repeats (one call per staff, all groups at once).",
            ],
        }

        if highlight:
            analysed = result["staves_analysed"]
            legend = []
            for group in found["measure_repeats"]:
                for first, last_staff in _contiguous(group["staves"]):
                    await client.request("colorRange", {
                        **range_params(group["from"], group["to"], first, last_staff),
                        "color": MEASURE_REPEAT_COLOR,
                    })
            if found["measure_repeats"]:
                legend.append({"color": MEASURE_REPEAT_COLOR, "meaning": "per-staff measure repeat"})
            for i, passage in enumerate(found["passages"]):
                color = PASSAGE_COLORS[i % len(PASSAGE_COLORS)]
                dup_first, dup_last = passage["duplicate_measures"]
                for first, last_staff in _contiguous(analysed):
                    await client.request("colorRange", {
                        **range_params(dup_first, dup_last, first, last_staff),
                        "color": color,
                    })
                legend.append({
                    "color": color,
                    "meaning": f"duplicate of measures {passage['start']}-{passage['end']} (passage {i + 1})",
                })
            result["highlight_legend"] = legend

        return result

    @mcp.tool(
        description=(
            "Put start and end repeat barlines around a range of measures, or remove them. "
            "Needs MuseScore 4.6 or newer; on older versions the measures are selected and the reply "
            "says which palette items to click."
        ),
        tags={"structure"},
    )
    async def set_repeat_barlines(
        start_measure: Annotated[int, "Measure that gets the start repeat (1-based)"],
        end_measure: Annotated[int, "Measure that gets the end repeat (1-based, inclusive)"],
        play_count: Annotated[int, "How many times the passage is played in total"] = 2,
        clear: Annotated[bool, "True to remove the repeat barlines instead of adding them"] = False,
    ):
        return await client.request("setRepeatBarlines", {
            "startMeasure": start_measure,
            "endMeasure": end_measure,
            "playCount": play_count,
            "clear": clear,
        })

    @mcp.tool(
        description=(
            "Add a volta (1st/2nd ending bracket) over a range of measures. Experimental: the plugin API has no "
            "dedicated volta call, so check the result; if it fails, the measures are selected and the reply "
            "says which palette item to click."
        ),
        tags={"structure"},
    )
    async def add_volta(
        start_measure: Annotated[int, "First measure under the volta (1-based)"],
        end_measure: Annotated[Optional[int], "Last measure under the volta (1-based, inclusive). Defaults to start_measure."] = None,
        endings: Annotated[List[int], "Which repeat passes play this ending, e.g. [1] for a first ending, [2] for a second"] = [1],
        text: Annotated[Optional[str], 'Volta label. Defaults to the endings, e.g. "1." or "1, 2."'] = None,
    ):
        params = {
            "startMeasure": start_measure,
            "endMeasure": end_measure if end_measure is not None else start_measure,
            "endings": endings,
        }
        if text:
            params["text"] = text
        return await client.request("addVolta", params)

    @mcp.tool(
        description=(
            "Prepare measure-repeat signs: selects the measures that should become repeat signs, on the given staves, "
            "and returns what to click in MuseScore. The plugin API cannot place measure-repeat signs itself."
        ),
        tags={"structure"},
    )
    async def prepare_measure_repeat(
        start_measure: Annotated[int, "First measure to turn into a repeat sign (1-based); it repeats the bars before it"],
        end_measure: Annotated[Optional[int], "Last measure (1-based, inclusive). Defaults to start_measure + bars - 1."] = None,
        bars: Annotated[Literal[1, 2, 4], "Size of the measure-repeat sign: 1, 2 or 4 bars"] = 1,
        first_staff: Annotated[int, "First staff (0-based)"] = 0,
        last_staff: Annotated[Optional[int], "Last staff (0-based, inclusive). Defaults to the last staff."] = None,
    ):
        if end_measure is None:
            end_measure = start_measure + bars - 1
        result = await client.request(
            "selectMeasures", range_params(start_measure, end_measure, first_staff, last_staff)
        )
        sign = "measure repeat" if bars == 1 else f"{bars}-measure repeat"
        result["next_step"] = (
            f"In MuseScore, with the selection still active, click the {sign} sign in the Repeats & jumps palette. "
            "The selected measures' content is replaced by the sign."
        )
        return result

    @mcp.tool(
        description=(
            "Map the sections of a groove-based part (drums by default): for each section the groove bar, the bars "
            "that copy it exactly, near copies (with the hits that differ), and fills. Proposes measure-repeat signs "
            "for exact copies only, as ready-made apply_measure_repeats groups, and a collapse_to_repeat range for a "
            "'groove bar x N' chart. Show near copies to the user and only replace them once approved: a repeat sign "
            "deletes their differences."
        ),
        tags={"structure"},
    )
    async def find_groove_sections(
        staves: Annotated[Optional[List[int]], "Staff indices to analyse (0-based). Defaults to the drum staves."] = None,
        start_measure: Annotated[int, "First measure to analyse (1-based)"] = 1,
        end_measure: Annotated[Optional[int], "Last measure to analyse (1-based, inclusive). Defaults to the end of the score."] = None,
        tolerance: Annotated[float, "How similar (0-1, share of identical hits) a bar must be to its groove to count as a near copy"] = 0.85,
    ):
        if not staves:
            staves = [st["index"] for st in await staff_header() if st.get("isDrums")]
            if not staves:
                raise ToolError("No drum staff found in the score; pass the staves to analyse")

        measures, staff_info = await fetch_measures(client, start_measure, end_measure, staves)
        result = []
        for st in staff_info:
            sections = find_sections(measures, st["name"], tolerance)
            groups = [g for sec in sections for g in sec["proposed_repeat_signs"]]
            result.append({
                "staff": st.get("index"),
                "name": st.get("partName") or st["name"],
                "road_map": [_road_map_line(sec) for sec in sections],
                "sections": sections,
                "apply_exact_copies": {"staff": st.get("index"), "groups": groups} if groups else None,
            })
        return {
            "staves": result,
            "notes": [
                "apply_exact_copies can be passed to apply_measure_repeats as is.",
                "Near copies are not included: ask the user per section. A bar can only become a 1-bar sign if it "
                "equals the bar before it, so approving a near bar also makes the exact bars right after it eligible.",
                "For a 'groove bar x N' chart, use each section's collapse range with collapse_to_repeat, in a score "
                "that contains only this part (make_part_score), working from the last section backwards.",
            ],
        }

    @mcp.tool(
        description=(
            "Collapse a run of identical bars into one bar played N times: keeps bar start, deletes bars "
            "start+1..end, and puts start/end repeat barlines with a play count on bar start (shown as e.g. '7x'). "
            "This changes every staff, so use it in a score that contains only the part being charted "
            "(make_part_score). It refuses when any staff's bars differ from bar start, unless force=True. "
            "Later measures are renumbered: collapse from the end of the score backwards. Needs MuseScore 4.6+."
        ),
        tags={"structure"},
    )
    async def collapse_to_repeat(
        start: Annotated[int, "The bar to keep (1-based); it is played play_count times"],
        end: Annotated[int, "Last bar of the run (1-based, inclusive)"],
        force: Annotated[bool, "Collapse even if some bars differ from bar start (their differences are lost)"] = False,
    ):
        if end <= start:
            raise ToolError("end must be after start")
        measures, staff_info = await fetch_measures(client, start, end, None)
        differing = []
        for st in staff_info:
            fps, _ = staff_fingerprints(measures, st["name"])
            bars = [measures[i]["measure"] for i in range(1, len(fps)) if fps[i] != fps[0]]
            if bars:
                differing.append({"staff": st.get("index"), "name": st.get("partName") or st["name"], "bars": bars})
        if differing and not force:
            listed = "; ".join(f"{d['name']} (staff {d['staff']}): bars {d['bars']}" for d in differing)
            raise ToolError(
                f"Not every bar in {start + 1}-{end} is a copy of bar {start}: {listed}. Collapsing would delete "
                "those differences. Chart a single part (make_part_score) or pass force=True after the user agrees."
            )

        play_count = end - start + 1
        repeat = await client.request(
            "setRepeatBarlines", {"startMeasure": start, "endMeasure": start, "playCount": play_count}
        )
        if repeat.get("fallback"):
            return repeat
        await client.request("deleteMeasures", {"startMeasure": start + 1, "endMeasure": end})
        try:
            await client.request("setStyle", {"key": "repeatPlayCountShow", "value": True})
        except ToolError:
            pass
        return {
            "success": True,
            "message": (
                f"Bar {start} now plays {play_count} times; bars {start + 1}-{end} were removed, "
                f"so later bars moved up by {play_count - 1}."
            ),
            "differences_dropped": differing,
        }

    @mcp.tool(
        description=(
            "Make a separate score containing only some parts (e.g. the drum kit) to build a chart in: saves the "
            "current score, copies its file, opens the copy in a new tab, removes every other instrument and saves "
            "the copy. The original score keeps its content and stays open in its own tab. Needs MuseScore 4.7+. "
            "Staff indices in the new score start again from 0."
        ),
        tags={"structure"},
    )
    async def make_part_score(
        staves: Annotated[List[int], "Staff indices (0-based) whose parts to keep"],
        output_path: Annotated[Optional[str], "Where to save the new score (.mscz). Defaults to next to the original, named after the part."] = None,
        score_path: Annotated[Optional[str], "Path of the current score's file. Defaults to the file of the score open in MuseScore."] = None,
    ):
        header = await staff_header()
        kept = [st for st in header if st["index"] in staves]
        if not kept:
            raise ToolError(f"None of the staves {staves} exist")
        info = await client.request("scoreFileInfo")

        # The plugin's writeScore() cannot write .mscz in MuseScore 4.7, so copy the saved file instead.
        await client.request("saveScore")
        try:
            source = find_score_file(info.get("scoreName", ""), score_path)
        except ScoreFileError as e:
            raise ToolError(str(e))

        if output_path:
            target = Path(output_path).expanduser()
            if target.suffix.lower() != source.suffix.lower():
                target = target.with_suffix(source.suffix)
        else:
            # Part names can be long ("Player | Instrument | Drums"): use the short name or the last segment
            label = kept[0].get("shortName") or (kept[0].get("partName") or "part").split("|")[-1]
            label = re.sub(r'[\\/:*?"<>|]+', "-", label).strip(" -") or "part"
            target = source.with_name(f"{source.stem} - {label}{source.suffix}")
            n = 2
            while target.exists():
                target = source.with_name(f"{source.stem} - {label} ({n}){source.suffix}")
                n += 1
        if target.exists():
            raise ToolError(f"{target} already exists; choose another output_path")

        shutil.copy2(source, target)
        await client.request("openScore", {"path": str(target)})
        removed = await client.request("removeParts", {"keepStaves": staves})
        await client.request("saveScore")
        return {
            "file": str(target),
            "kept_parts": removed.get("kept"),
            "staves_now": removed.get("numStaves"),
            "message": f"Opened {target.name} with only {', '.join(removed.get('kept') or [])}. "
                       "The original score is unchanged and still open in its own tab.",
        }

    @mcp.tool(
        description="Add a rehearsal mark (section label such as 'A' or 'Verse') at the start of a measure. Experimental: check the score afterwards.",
        tags={"structure"},
    )
    async def add_rehearsal_mark(
        measure: Annotated[int, "Measure number (1-based)"],
        text: Annotated[str, 'Label, e.g. "A" or "Verse 1"'],
    ):
        return await client.request("addRehearsalMark", {"measure": measure, "text": text})

