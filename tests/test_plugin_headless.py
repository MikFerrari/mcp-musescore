"""The real QML plugin, run headless in MuseScore (dev/plugin_harness.py). Skipped without MuseScore."""

import shutil
import zipfile
from pathlib import Path

import pytest

from dev.plugin_harness import find_musescore, run

JAZZ = Path(__file__).resolve().parent.parent / "examples" / "jazz piano" / "Jazz Piano.mscz"

pytestmark = pytest.mark.skipif(find_musescore() is None, reason="MuseScore not installed")


def results_by_action(results):
    return {entry["action"]: entry["result"] for entry in results}


def text_lines(path):
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith(".mscx") and "/" not in n)
        return z.read(name).decode("utf-8").count('<Spanner type="TextLine">')


def test_score_reading_reports_parts_and_ranges():
    results = results_by_action(run([
        {"action": "getScore", "params": {"startMeasure": 2, "endMeasure": 3, "staves": [1]}},
    ], JAZZ))
    analysis = results["getScore"]["analysis"]
    assert [m["measure"] for m in analysis["measures"]] == [2, 3]
    assert analysis["measures"][0]["durationTicks"] == 1440  # 3/4
    assert analysis["staves"] == [
        {**analysis["staves"][0], "index": 1, "partName": "Piano", "isDrums": False}
    ]
    assert analysis["measures"][0]["elements"]["staff1"]


def test_highlights_are_added_to_the_score(tmp_path):
    out = tmp_path / "out.mscz"
    results = results_by_action(run([
        {"action": "highlightNotes", "params": {"startMeasure": 1, "endMeasure": 1, "startStaff": 0, "endStaff": 1}},
        {"action": "highlightSymbols", "params": {"startMeasure": 1, "endMeasure": 1}},
        {"action": "addHighlight", "params": {"startMeasure": 2, "endMeasure": 2, "startStaff": 1, "endStaff": 2}},
    ], JAZZ, out=out))
    assert results["highlightNotes"]["boxes"] >= 1
    assert results["highlightSymbols"]["found"]["time_signatures"] == 2
    assert results["addHighlight"]["placedSegments"] == 1
    boxes = results["highlightNotes"]["boxes"] + results["highlightSymbols"]["boxes"] + 1
    assert text_lines(out) == 2 * boxes  # every line is stored with a start and an end tag


def test_clear_highlights_removes_them(tmp_path):
    out = tmp_path / "out.mscz"
    results = run([
        {"action": "highlightNotes", "params": {"startMeasure": 1, "endMeasure": 2, "startStaff": 0, "endStaff": 1}},
        {"action": "clearHighlights", "params": {"startMeasure": 1, "endMeasure": 2}},
    ], JAZZ, out=out)
    assert results[1]["result"]["removed"] == results[0]["result"]["boxes"]
    assert text_lines(out) == 0


def test_errors_come_back_as_results():
    (result,) = run([{"action": "colorRange", "params": {"startMeasure": 99}}], JAZZ)
    assert "Invalid measure range" in result["result"]["error"]
