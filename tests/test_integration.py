"""Integration tests: real MCP tools and WebSocket client against the in-memory mock plugin."""

import asyncio

from fastmcp import Client

import server
from mock_musescore import MockMuseScore, MockScore, demo_score, running_mock

QUARTER = {"numerator": 1, "denominator": 4}


def patterned_score(*patterns):
    """Mock score with one staff per pattern; each letter is a bar of four quarter notes of one pitch."""
    mock = MockMuseScore(MockScore(staves=len(patterns), measures=len(patterns[0])))
    for staff, pattern in enumerate(patterns):
        mock.score.cursor_staff, mock.score.cursor_tick = staff, 0
        for letter in pattern:
            for _ in range(4):
                mock.process_command({"action": "addNote", "params": {
                    "pitch": 60 + "ABCDEFGH".index(letter), "duration": QUARTER,
                    "advanceCursorAfterAction": True}})
    mock.score.cursor_staff, mock.score.cursor_tick = 0, 0
    return mock.score


def run_with_mock(scenario, score=None):
    """Run `scenario(mcp_client, mock)` with server.client pointed at a mock plugin."""

    async def runner():
        original_uri = server.client.uri
        async with running_mock(score) as mock:
            server.client.uri = mock.uri
            server.client.websocket = None
            try:
                async with Client(server.mcp) as mcp_client:
                    await scenario(mcp_client, mock)
            finally:
                await server.client.close()
                server.client.uri = original_uri

    asyncio.run(runner())


def test_ping_reaches_plugin():
    async def scenario(c, mock):
        await c.call_tool("ping_musescore", {})
        assert mock.commands == [{"action": "ping", "params": {}}]

    run_with_mock(scenario)


def test_melody_is_written_sequentially():
    async def scenario(c, mock):
        for pitch in (60, 62, 64):
            await c.call_tool("add_note", {"pitch": pitch, "duration": QUARTER})
        chords = mock.score.elements[0]
        assert [e["notes"][0]["pitchMidi"] for e in chords] == [60, 62, 64]
        assert [e["startTick"] for e in chords] == [0, 480, 960]

    run_with_mock(scenario)


def test_add_to_chord_stacks_pitches():
    async def scenario(c, mock):
        await c.call_tool(
            "add_note", {"pitch": 60, "duration": QUARTER, "advance_cursor_after_action": False}
        )
        await c.call_tool("add_note", {"pitch": 64, "duration": QUARTER, "add_to_chord": True})
        (chord,) = mock.score.elements[0]
        assert [n["pitchMidi"] for n in chord["notes"]] == [60, 64]

    run_with_mock(scenario)


def test_process_sequence_runs_actions_in_order():
    sequence = [
        {"action": "goToMeasure", "params": {"measure": 2}},
        {"action": "addRest", "params": {"duration": QUARTER, "advanceCursorAfterAction": True}},
        {
            "action": "addNote",
            "params": {"pitch": 67, "duration": QUARTER, "advanceCursorAfterAction": True},
        },
    ]

    async def scenario(c, mock):
        await c.call_tool("processSequence", {"sequence": sequence})
        names = [(e["name"], e["startTick"]) for e in mock.score.elements[0]]
        assert names == [("Rest", 1920), ("Chord", 2400)]

    run_with_mock(scenario)


def test_undo_reverts_last_edit():
    async def scenario(c, mock):
        await c.call_tool("add_note", {"pitch": 60, "duration": QUARTER})
        await c.call_tool("undo", {})
        assert mock.score.elements[0] == []

    run_with_mock(scenario)


def test_append_measure_and_get_score():
    async def scenario(c, mock):
        await c.call_tool("append_measure", {"count": 2})
        assert mock.score.num_measures == 6
        await c.call_tool("get_score", {})
        assert mock.commands[-1]["action"] == "getScore"

    run_with_mock(scenario, MockScore(measures=4))


def test_plugin_errors_surface_in_result():
    async def scenario(c, mock):
        result = await c.call_tool("go_to_measure", {"measure": 99})
        assert "Invalid measure number" in str(result)

    run_with_mock(scenario)


def test_show_score_returns_structured_score_for_the_viewer():
    async def scenario(c, mock):
        result = await c.call_tool("show_score", {"first_measure": 2, "last_measure": 3})
        score = result.structured_content
        assert [m["measure"] for m in score["measures"]] == [2, 3]
        assert score["measureTicks"] == 1920
        assert score["title"] == "Twinkle Twinkle (demo)"
        assert "2 of 4" in result.content[0].text

    run_with_mock(scenario, demo_score())


def test_show_score_declares_its_ui_resource_and_serves_it():
    async def scenario(c, mock):
        tools = {t.name: t for t in await c.list_tools()}
        assert tools["show_score"].meta["ui"]["resourceUri"] == "ui://musescore/score-viewer.html"
        (page,) = await c.read_resource("ui://musescore/score-viewer.html")
        assert page.mime_type == "text/html;profile=mcp-app"
        assert "Vex.Flow" in page.text

    run_with_mock(scenario)


def test_get_score_returns_lilypond_in_windows():
    async def scenario(c, mock):
        first = (await c.call_tool("get_score", {})).content[0].text
        assert "Mesures affichées: 1-16" in first and "Total Mesures: 20" in first
        assert "% m.1" in first and "% m.16" in first and "% m.17" not in first
        assert "get_score(start_measure=17)" in first
        rest = (await c.call_tool("get_score", {"start_measure": 17})).content[0].text
        assert "Mesures affichées: 17-20" in rest and "More measures" not in rest
        assert mock.commands[-1]["params"] == {"startMeasure": 17, "endMeasure": 32}

    run_with_mock(scenario, patterned_score("ABCD" * 5))


def test_get_score_json_for_selected_staves():
    async def scenario(c, mock):
        analysis = (await c.call_tool(
            "get_score", {"start_measure": 2, "end_measure": 3, "staves": [1], "format": "json"}
        )).data
        assert [m["measure"] for m in analysis["measures"]] == [2, 3]
        assert [st["index"] for st in analysis["staves"]] == [1]
        assert set(analysis["measures"][0]["elements"]) == {"staff1"}

    run_with_mock(scenario, demo_score())


def test_navigation_returns_lilypond_for_the_selection():
    async def scenario(c, mock):
        text = (await c.call_tool("go_to_measure", {"measure": 1})).content[0].text
        assert "[Partition]" in text
        assert "c'4" in text and "c2 g2" in text

    run_with_mock(scenario, demo_score())


def test_set_tempo_at_a_measure():
    async def scenario(c, mock):
        await c.call_tool("set_tempo", {"bpm": 90, "measure": 2})
        assert (mock.score.tempo, mock.score.tempo_measure) == (90, 2)

    run_with_mock(scenario)


def test_color_range_and_clear_color():
    async def scenario(c, mock):
        result = (await c.call_tool(
            "color_range", {"start_measure": 1, "first_staff": 1, "last_staff": 1, "color": "#00ff00"}
        )).data
        assert result["colored"] == 2
        assert set(mock.score.colors.values()) == {"#00ff00"}
        assert {staff for staff, _, _ in mock.score.colors} == {1}
        await c.call_tool("clear_color", {"start_measure": 1})
        assert set(mock.score.colors.values()) == {"#000000"}

    run_with_mock(scenario, demo_score())


def test_set_repeat_barlines_and_volta():
    async def scenario(c, mock):
        await c.call_tool("set_repeat_barlines", {"start_measure": 1, "end_measure": 2, "play_count": 3})
        assert mock.score.repeats == {1: {"start": True}, 2: {"end": True, "count": 3}}
        await c.call_tool("add_volta", {"start_measure": 2, "endings": [1]})
        assert mock.score.voltas == [{"startMeasure": 2, "endMeasure": 2, "endings": [1], "text": "1."}]

    run_with_mock(scenario)


def test_set_repeat_barlines_falls_back_to_selection():
    score = MockScore()
    score.repeat_barlines_supported = False

    async def scenario(c, mock):
        result = (await c.call_tool("set_repeat_barlines", {"start_measure": 2, "end_measure": 3})).data
        assert result["fallback"] == "manual"
        assert mock.score.repeats == {}
        assert mock.score.selected["startMeasure"] == 2 and mock.score.selected["endMeasure"] == 3

    run_with_mock(scenario, score)


def test_prepare_measure_repeat_selects_bars():
    async def scenario(c, mock):
        result = (await c.call_tool(
            "prepare_measure_repeat", {"start_measure": 3, "bars": 2, "first_staff": 1, "last_staff": 1}
        )).data
        assert mock.score.selected == {"startMeasure": 3, "endMeasure": 4, "startStaff": 1, "endStaff": 2}
        assert "2-measure repeat" in result["next_step"]

    run_with_mock(scenario, demo_score())


def test_invalid_range_is_a_tool_error():
    async def scenario(c, mock):
        result = await c.call_tool("color_range", {"start_measure": 9}, raise_on_error=False)
        assert result.is_error
        assert "Invalid measure range" in result.content[0].text

    run_with_mock(scenario)


def test_delete_measures_shifts_later_bars():
    async def scenario(c, mock):
        await c.call_tool("delete_measures", {"start_measure": 2, "end_measure": 3})
        assert mock.score.num_measures == 3
        pitches = [e["notes"][0]["pitchMidi"] for e in mock.score.elements[0]]
        assert pitches == [60] * 4 + [64] * 4 + [65] * 4

    run_with_mock(scenario, patterned_score("ABCEF"))


def test_find_repeated_measures_reads_in_chunks_and_highlights():
    # 20 bars, so the analysis needs two getScore chunks; bars 3-10 repeat as 11-18
    pattern = "GH" + "ABCDEFAB" + "ABCDEFAB" + "CD"

    async def scenario(c, mock):
        result = (await c.call_tool("find_repeated_measures", {"highlight": True})).data
        ranges = [cmd["params"] for cmd in mock.commands if cmd["action"] == "getScore"]
        assert [(r["startMeasure"], r["endMeasure"]) for r in ranges] == [(1, 16), (17, 20)]

        (passage,) = result["passages"]
        assert (passage["start"], passage["end"], passage["duplicate_measures"]) == (3, 10, [11, 18])
        assert passage["apply"] == ["delete_measures(11, 18)", "set_repeat_barlines(3, 10, play_count=2)"]

        colored_ticks = {tick for _, tick, _ in mock.score.colors}
        assert min(colored_ticks) == 10 * 1920 and max(colored_ticks) < 18 * 1920
        assert result["highlight_legend"][-1]["meaning"].startswith("duplicate of measures 3-10")

    run_with_mock(scenario, patterned_score(pattern, pattern))


def test_applying_a_passage_shortens_the_score():
    async def scenario(c, mock):
        (passage,) = (await c.call_tool("find_repeated_measures", {})).data["passages"]
        dup_first, dup_last = passage["duplicate_measures"]
        await c.call_tool("delete_measures", {"start_measure": dup_first, "end_measure": dup_last})
        await c.call_tool("set_repeat_barlines", {"start_measure": passage["start"], "end_measure": passage["end"]})
        assert mock.score.num_measures == 5
        assert mock.score.repeats[2]["start"] and mock.score.repeats[4]["end"]
        again = (await c.call_tool("find_repeated_measures", {})).data
        assert again["passages"] == []

    run_with_mock(scenario, patterned_score("EABCABCF"))



# --- score-file edits, groove sections, part scores ----------------------------------

import json
import shutil
from pathlib import Path

from src.mscz import ScoreFile

JAZZ = Path(__file__).resolve().parent.parent / "examples" / "jazz piano" / "Jazz Piano.mscz"


def jazz_copy(tmp_path, monkeypatch):
    """A copy of the example score plus an isolated backup directory."""
    monkeypatch.setenv("MCP_MUSESCORE_BACKUP_DIR", str(tmp_path / "backups"))
    path = tmp_path / "Jazz Piano.mscz"
    shutil.copy(JAZZ, path)
    return path


def bass_staff_repeat(path, measure):
    return ScoreFile(path).staves()[1].findall("Measure")[measure - 1].findtext("voice/MeasureRepeat/subtype")


def test_apply_measure_repeats_edits_file_and_reloads(tmp_path, monkeypatch):
    path = jazz_copy(tmp_path, monkeypatch)
    original = path.read_bytes()

    async def scenario(c, mock):
        result = (await c.call_tool("apply_measure_repeats", {
            "staff": 1, "groups": [{"start": 2, "end": 3}], "score_path": str(path)})).data
        assert [g["start"] for g in result["applied"]] == [2, 3]
        assert result["reopened"] is True
        actions = [cmd["action"] for cmd in mock.commands]
        assert actions.index("saveScore") < actions.index("reopenScore")
        assert mock.opened == [str(path)]
        assert Path(result["backup"]).read_bytes() == original
        assert bass_staff_repeat(path, 2) == "1" and bass_staff_repeat(path, 3) == "1"

        restored = (await c.call_tool("restore_backup", {"score_path": str(path)})).data
        assert path.read_bytes() == original
        assert Path(restored["previous_version_backup"]).exists()
        assert mock.opened == [str(path), str(path)]

    run_with_mock(scenario)


def test_apply_measure_repeats_without_changes_does_not_reload(tmp_path, monkeypatch):
    path = jazz_copy(tmp_path, monkeypatch)

    async def scenario(c, mock):
        result = (await c.call_tool("apply_measure_repeats", {
            "staff": 1, "groups": [{"start": 1}], "score_path": str(path)})).data
        assert result["skipped"] and not result["applied"]
        assert "backup" not in result and mock.opened == []
        assert not (tmp_path / "backups").exists()

    run_with_mock(scenario)


def test_apply_measure_repeats_finds_the_open_file(tmp_path, monkeypatch):
    path = jazz_copy(tmp_path, monkeypatch)
    data_dir = tmp_path / "musescore"
    (data_dir / "session").mkdir(parents=True)
    (data_dir / "session" / "session.json").write_text(json.dumps([str(path)]))
    (data_dir / "recent_files.json").write_text(json.dumps([str(path), "/nowhere/Other.mscz"]))
    monkeypatch.setenv("MUSESCORE_DATA_DIR", str(data_dir))
    score = MockScore()
    score.score_name = "Jazz Piano"

    async def scenario(c, mock):
        result = (await c.call_tool("apply_measure_repeats", {
            "staff": 1, "groups": [{"start": 3, "end": 6, "bars": 2}]})).data
        assert result["file"] == str(path)
        assert [(g["start"], g["bars"]) for g in result["applied"]] == [(3, 2), (5, 2)]
        assert bass_staff_repeat(path, 3) == "2"

    run_with_mock(scenario, score)


def test_apply_measure_repeats_rejects_uneven_runs(tmp_path, monkeypatch):
    path = jazz_copy(tmp_path, monkeypatch)

    async def scenario(c, mock):
        result = await c.call_tool("apply_measure_repeats", {
            "staff": 1, "groups": [{"start": 3, "end": 5, "bars": 2}], "score_path": str(path)},
            raise_on_error=False)
        assert result.is_error and "cannot be split" in result.content[0].text

    run_with_mock(scenario)


def drum_score(pattern, other=None):
    score = patterned_score(pattern, other) if other else patterned_score(pattern)
    score.drum_staves = {0}
    score.part_names[0] = "Drum Kit"
    return score


def test_find_groove_sections_defaults_to_drums():
    async def scenario(c, mock):
        result = (await c.call_tool("find_groove_sections", {})).data
        (staff,) = result["staves"]
        assert staff["name"] == "Drum Kit"
        assert staff["road_map"][0] == "Bars 1-5: groove bar 1, 3 exact copies, fill 5"
        assert staff["apply_exact_copies"] == {"staff": 0, "groups": [{"start": 2, "end": 4, "bars": 1}]}
        assert staff["sections"][0]["collapse"] == {"start": 1, "end": 4, "play_count": 4}

    run_with_mock(scenario, drum_score("AAAAB", "CDEFG"))


def test_find_groove_sections_needs_a_drum_staff():
    async def scenario(c, mock):
        result = await c.call_tool("find_groove_sections", {}, raise_on_error=False)
        assert result.is_error and "No drum staff" in result.content[0].text

    run_with_mock(scenario, patterned_score("AAAA"))


def test_collapse_to_repeat_keeps_one_bar_with_a_count():
    async def scenario(c, mock):
        result = (await c.call_tool("collapse_to_repeat", {"start": 2, "end": 5})).data
        assert result["success"]
        assert mock.score.repeats[2] == {"start": True, "end": True, "count": 4}
        assert mock.score.num_measures == 3
        assert mock.score.style["repeatPlayCountShow"] is True

    run_with_mock(scenario, drum_score("BAAAAC"))


def test_collapse_to_repeat_refuses_when_other_staves_differ():
    async def scenario(c, mock):
        result = await c.call_tool("collapse_to_repeat", {"start": 1, "end": 4}, raise_on_error=False)
        assert result.is_error and "Part 2 (staff 1): bars [2, 3, 4]" in result.content[0].text
        assert mock.score.num_measures == 4
        forced = (await c.call_tool("collapse_to_repeat", {"start": 1, "end": 4, "force": True})).data
        assert forced["differences_dropped"][0]["bars"] == [2, 3, 4]
        assert mock.score.num_measures == 1

    run_with_mock(scenario, drum_score("AAAA", "CDEF"))


def test_make_part_score_opens_a_copy_with_one_part(tmp_path, monkeypatch):
    source = jazz_copy(tmp_path, monkeypatch)
    score = drum_score("AAAA", "CDEF")
    score.part_names[0] = "Paul McCartney | Ludwig | Drums"

    async def scenario(c, mock):
        result = (await c.call_tool("make_part_score", {"staves": [0], "score_path": str(source)})).data
        target = tmp_path / "Jazz Piano - Drums.mscz"
        assert result["file"] == str(target) and target.read_bytes() == source.read_bytes()
        assert result["kept_parts"] == ["Paul McCartney | Ludwig | Drums"]
        assert mock.opened == [str(target)] and mock.score.num_staves == 1
        actions = [cmd["action"] for cmd in mock.commands]
        assert actions.count("saveScore") == 2 and actions.index("removeParts") < len(actions) - 1

        second = (await c.call_tool("make_part_score", {"staves": [0], "score_path": str(source)})).data
        assert second["file"] == str(tmp_path / "Jazz Piano - Drums (2).mscz")

    run_with_mock(scenario, score)


def test_add_rehearsal_mark():
    async def scenario(c, mock):
        await c.call_tool("add_rehearsal_mark", {"measure": 3, "text": "Verse"})
        assert mock.score.rehearsal_marks == [{"measure": 3, "text": "Verse"}]

    run_with_mock(scenario)


def test_highlight_label_and_clear():
    async def scenario(c, mock):
        await c.call_tool("highlight_bars", {"start_measure": 2, "end_measure": 3, "first_staff": 0, "last_staff": 1,
                                             "color": "#ff0000", "opacity": 0.5})
        assert mock.score.highlights == [
            {"staff": s, "startMeasure": 2, "endMeasure": 3, "color": "#80ff0000"} for s in (0, 1)
        ]
        await c.call_tool("add_label", {"measure": 2, "text": "Groove x7", "staff": 1})
        assert mock.score.labels[0]["text"] == "Groove x7" and mock.score.labels[0]["background"] == "#fff2a8"

        removed = (await c.call_tool("clear_highlights", {"first_staff": 1, "last_staff": 1})).data
        assert removed["removed"] == 2
        assert [h["staff"] for h in mock.score.highlights] == [0] and mock.score.labels == []

    run_with_mock(scenario, demo_score())


def test_highlight_rejects_bad_colors():
    async def scenario(c, mock):
        result = await c.call_tool("highlight_bars", {"start_measure": 1, "color": "yellow"}, raise_on_error=False)
        assert result.is_error and "#rrggbb" in result.content[0].text
        result = await c.call_tool("add_label", {"measure": 1, "text": "A", "background": "#12"},
                                   raise_on_error=False)
        assert result.is_error

    run_with_mock(scenario)


def test_color_range_can_color_part_of_a_bar():
    async def scenario(c, mock):
        # staff 0, bar 1 of the demo: four quarter notes at ticks 0, 480, 960, 1440
        result = (await c.call_tool("color_range", {
            "start_measure": 1, "first_staff": 0, "last_staff": 0, "start_beat": 3, "end_beat": 4,
            "color": "#2ca02c"})).data
        assert result["colored"] == 2
        assert sorted(tick for _, tick, _ in mock.score.colors) == [960, 1440]

        mock.score.colors.clear()
        await c.call_tool("color_range", {"start_measure": 1, "end_measure": 2, "first_staff": 0,
                                          "last_staff": 0, "start_beat": 3.5, "end_beat": 1})
        # from the "and" of 3 in bar 1 (no note starts there) to the end of beat 1 in bar 2
        assert sorted(tick for _, tick, _ in mock.score.colors) == [1440, 1920]

    run_with_mock(scenario, demo_score())


def test_beats_outside_the_bar_are_errors():
    async def scenario(c, mock):
        result = await c.call_tool("color_range", {"start_measure": 1, "start_beat": 5}, raise_on_error=False)
        assert result.is_error and "outside measure 1" in result.content[0].text
        result = await c.call_tool("color_range", {"start_measure": 1, "start_beat": 3, "end_beat": 2},
                                   raise_on_error=False)
        assert result.is_error and "empty" in result.content[0].text

    run_with_mock(scenario, demo_score())


def crash_and_kick_score():
    """Drum staff: bar 1 has kick+crash on beat 1, then kick+snare, plus an f and an mf dynamic."""
    mock = MockMuseScore(MockScore(staves=1, measures=2))
    for pitches in ((36, 57), (36, 38), (36,), (38,)):
        mock.process_command({"action": "addNote", "params": {
            "pitch": pitches[0], "duration": QUARTER, "advanceCursorAfterAction": len(pitches) == 1}})
        for extra in pitches[1:]:
            mock.process_command({"action": "addNote", "params": {
                "pitch": extra, "duration": QUARTER, "advanceCursorAfterAction": True, "addToChord": True}})
            mock.score.cursor_tick = mock.score.elements[0][-1]["startTick"] + 480
    score = mock.score
    score.drum_staves = {0}
    score.markings = [{"staff": 0, "startTick": 0, "name": "Dynamic", "text": "f"},
                      {"staff": 0, "startTick": 1920, "name": "Dynamic", "text": "mf"}]
    score.cursor_tick = 0
    return score


def test_highlight_notes_marks_only_the_crash():
    async def scenario(c, mock):
        result = (await c.call_tool("highlight_notes", {"start_measure": 1, "drums": ["crashes"]})).data
        assert result["highlighted"] == 1
        assert list(mock.score.note_highlights) == [(0, 0, 57)]
        assert mock.commands[-1]["params"]["pitches"] == [49, 57]
        assert mock.commands[-1]["params"]["color"] == "#73ffd600"

        bad = await c.call_tool("highlight_notes", {"start_measure": 1, "drums": ["gong"]}, raise_on_error=False)
        assert bad.is_error and "Unknown drum" in bad.content[0].text

    run_with_mock(scenario, crash_and_kick_score())


def test_dynamics_are_listed_highlighted_and_cleared():
    async def scenario(c, mock):
        text = (await c.call_tool("get_score", {})).content[0].text
        assert "[Markings]\nm.1 beat 1, staff 0: Dynamic f\nm.2 beat 1, staff 0: Dynamic mf" in text

        result = (await c.call_tool("highlight_markings", {})).data
        assert result["found"] == 2
        assert all(mk["highlight"] == "#8cffd600" for mk in mock.score.markings)
        assert "Dynamic f (highlighted)" in (await c.call_tool("get_score", {})).content[0].text

        await c.call_tool("highlight_notes", {"start_measure": 1})
        cleared = (await c.call_tool("clear_highlights", {"kinds": ["markings", "notes"]})).data
        assert cleared["removed"] == 2 + 6  # two dynamics, six noteheads in bar 1
        assert not any(mk["highlight"] for mk in mock.score.markings) and not mock.score.note_highlights

    run_with_mock(scenario, crash_and_kick_score())


def test_highlight_symbols_modes():
    async def scenario(c, mock):
        await c.call_tool("highlight_symbols", {"start_measure": 1, "end_measure": 2})
        await c.call_tool("highlight_symbols", {"kinds": ["repeat_barlines"], "mode": "color", "color": "#5dade2"})
        first, second = mock.score.symbol_requests
        assert first["mode"] == "highlight" and first["color"] == "#99a7d8f0"
        assert first["kinds"] == ["time_signatures", "repeat_barlines", "measure_repeats"]
        assert second == {**second, "mode": "color", "color": "#5dade2", "kinds": ["repeat_barlines"], "endMeasure": 4}
        bad = await c.call_tool("highlight_symbols", {"mode": "color", "color": "blue"}, raise_on_error=False)
        assert bad.is_error

    run_with_mock(scenario)
