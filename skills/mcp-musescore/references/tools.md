# Public MCP tool reference

This reference describes the tools currently registered by `server.py` and the JSON actions sent to the MuseScore QML plugin. Use the Python function names when calling MCP. Use the camelCase action names only inside `processSequence`.

The server currently registers 47 public tools.

## Connection and score inspection

| MCP tool | Parameters | What it does |
|---|---|---|
| `connect_to_musescore` | none | Opens the WebSocket client connection to `ws://localhost:8765`. Returns `{success: bool}`. |
| `ping_musescore` | none | Sends the `ping` action. A healthy plugin returns `pong`. |
| `get_score` | `start_measure: int = 1`, `end_measure: int \| None = None`, `staves: list[int] \| None = None`, `format: "lilypond" \| "json" = "lilypond"` | Reads a window of the score (16 measures by default) as LilyPond with a `% m.N` comment at every barline, or as the raw JSON analysis. The header gives the total number of measures; page through long scores with `start_measure`, and read only some staves of large scores with `staves`. |
| `show_score` | `first_measure: int = 1`, `last_measure: int \| None = None` | Read-only. Renders the score as sheet music in MCP Apps hosts (VexFlow viewer). Other hosts get only a text summary; use `get_score` for data. |

Call `ping_musescore` before a mutation. Call `get_score` before and after a complex edit.

## Navigation and selection

| MCP tool | Parameters | What it does |
|---|---|---|
| `get_cursor_info` | none | Reads the current cursor/selection state and the related score context. |
| `go_to_measure` | `measure: int` | Moves the cursor to a zero-based measure index in the plugin's cursor implementation. Verify the result because the UI displays human-facing measure numbers. |
| `go_to_final_measure` | none | Moves to the final measure. |
| `go_to_beginning_of_score` | none | Resets the plugin cursor/selection to the beginning of the score. |
| `next_element` | none | Moves the cursor to the next score element. |
| `prev_element` | none | Moves the cursor to the previous score element. |
| `next_staff` | none | Moves the cursor to the next staff. |
| `prev_staff` | none | Moves the cursor to the previous staff. |
| `select_current_measure` | none | Selects the current measure. |
| `select_custom_range` | `start_tick: int`, `end_tick: int`, `start_staff: int`, `end_staff: int` | Selects a precise tick range across one or more zero-based staves. MuseScore uses 480 ticks per quarter note in this project. |

Navigation tools return the selection as LilyPond with measure/beat/staff metadata. On a plugin error they return the raw response envelope instead; read it rather than assuming a move succeeded.

## Notes, rests, lyrics, and measures

Duration values are JSON objects such as `{"numerator": 1, "denominator": 4}` for a quarter note. MIDI pitch 60 is middle C (C4); valid MIDI pitch values are 0–127.

| MCP tool | Parameters | What it does |
|---|---|---|
| `add_note` | `pitch: int = 64`, `duration: dict = {numerator: 1, denominator: 4}`, `advance_cursor_after_action: bool = true` | Adds a note at the current cursor position. |
| `add_rest` | `duration: dict = {numerator: 1, denominator: 4}`, `advance_cursor_after_action: bool = true` | Adds a rest at the current cursor position. |
| `add_tuplet` | `duration: dict = {numerator: 1, denominator: 4}`, `ratio: dict = {numerator: 3, denominator: 2}`, `advance_cursor_after_action: bool = true` | Adds a tuplet; the default ratio represents a triplet. |
| `add_lyrics` | `lyrics: list[str]`, `verse: int = 0` | Adds syllables to consecutive notes from the current cursor. Verse is zero-based. |
| `insert_measure` | none | Inserts a measure at the current position. |
| `append_measure` | `count: int = 1` | Appends one or more measures to the end of the score. |
| `delete_selection` | `measure: int | null = null` | Deletes the current selection, or a specified measure when provided. Treat as destructive and verify immediately. |
| `delete_measures` | `start_measure: int`, `end_measure: int \| None = None` | Removes whole measures on every staff (MuseScore's Remove selected range). Later measures are renumbered, so remove ranges from the last one backwards. |
| `undo` | none | Undoes the last MuseScore operation. |

## Staff, instruments, and time

| MCP tool | Parameters | What it does |
|---|---|---|
| `add_instrument` | `instrument_id: str` | Adds a staff/instrument using the MuseScore instrument identifier. |
| `set_staff_mute` | `staff: int`, `mute: bool` | Mutes or unmutes a zero-based staff. |
| `set_instrument_sound` | `staff: int`, `instrument_id: str` | Changes the sound/instrument identifier for a zero-based staff. |
| `set_time_signature` | `numerator: int = 4`, `denominator: int = 4` | Sets the time signature at the current score context. |
| `set_tempo` | `bpm: float`, `measure: int \| None = None` | Adds a tempo marking (quarter-note BPM) at the cursor, or at the start of `measure`. |

## Repeat structure and coloring

Measure ranges are 1-based and inclusive. Staff ranges in these tools are 0-based and inclusive (`first_staff`, `last_staff`).

| MCP tool | Parameters | What it does |
|---|---|---|
| `find_repeated_measures` | `start_measure: int = 1`, `end_measure: int \| None = None`, `staves: list[int] \| None = None`, `highlight: bool = false` | Reads the score in 16-measure chunks and returns `passages` (whole-score repeats and repeats with 1st/2nd endings, each with `duplicate_measures`, `saves_measures` and the `apply` tool calls) and `measure_repeats` (per-staff 1/2/4-bar repeats, grouped by staves). `highlight` colors duplicates in the score and returns a legend. |
| `set_repeat_barlines` | `start_measure: int`, `end_measure: int`, `play_count: int = 2`, `clear: bool = false` | Sets start/end repeat barlines. Needs MuseScore 4.6+; on older versions it selects the bars and returns `fallback: "manual"` with palette instructions. |
| `add_volta` | `start_measure: int`, `end_measure: int \| None = None`, `endings: list[int] = [1]`, `text: str \| None = None` | Experimental volta (ending bracket). Check the score afterwards; if it fails or nothing appears, the bars are selected for the palette. |
| `prepare_measure_repeat` | `start_measure: int`, `end_measure: int \| None = None`, `bars: 1 \| 2 \| 4 = 1`, `first_staff: int = 0`, `last_staff: int \| None = None` | Selects the bars to replace with a measure-repeat sign and returns the palette step. The plugin API cannot place these signs itself. |
| `color_range` | `start_measure: int`, `end_measure: int \| None = None`, `first_staff: int = 0`, `last_staff: int \| None = None`, `color: str = "#d62728"`, `voices: list[int] \| None = None`, `start_beat: float \| None = None`, `end_beat: float \| None = None` | Colors notes and rests (stems, beams, accidentals, dots, ties) in a range. `start_beat` (in `start_measure`, fractions allowed, 3.5 = "and" of 3) and `end_beat` (in `end_measure`, inclusive) color only part of a bar; a beat is the time signature's denominator. Only notes/rests that start inside the span are colored. |
| `clear_color` | `start_measure: int`, `end_measure: int \| None = None`, `first_staff: int = 0`, `last_staff: int \| None = None`, `start_beat`, `end_beat` | Resets a range (optionally part of a bar) to black. |
| `highlight_bars` | `start_measure: int`, `end_measure: int \| None = None`, `first_staff: int = 0`, `last_staff: int \| None = None` (defaults to `first_staff`), `color: str = "#ffe600"`, `opacity: float = 0.35`, `start_beat`, `end_beat` | Highlighter band behind the bars of each staff: a thick, semi-transparent text line drawn behind the notes. Experimental; check the score. |
| `highlight_notes` | `start_measure: int`, `end_measure: int \| None = None`, `first_staff: int = 0`, `last_staff: int \| None = None` (defaults to `first_staff`), `drums: list[str] \| None` (e.g. `["crash"]`, `["kick", "snare"]`, `["hi-hat"]`, `["ride"]`, `["tom"]`), `pitches: list[int] \| None`, `start_beat`, `end_beat`, `color: str = "#ffd600"`, `opacity: float = 0.45` | Full-staff-height translucent box around each matching hit (as wide as the note, taller if the note is above/below the staff); hits whose boxes would touch share one box. Use `drums`/`pitches` to pick one note of a chord, e.g. the crash. Boxes are sized to the current layout; after big layout changes, clear and re-run. |
| `highlight_symbols` | `start_measure: int = 1`, `end_measure: int \| None = None` (end of score), `first_staff: int = 0`, `last_staff: int \| None = None`, `kinds: ["time_signatures", "repeat_barlines", "measure_repeats"]`, `mode: "highlight" \| "color" = "highlight"`, `color: str = "#a7d8f0"` (pastel light blue), `opacity: float = 0.6`, `clear: bool = false` | Highlight mode: full-staff-height box around each symbol (repeat barlines include their dots). Color mode: recolors the symbol; `clear=True` resets it to black. |
| `highlight_markings` | `start_measure: int = 1`, `end_measure: int \| None = None` (end of score), `first_staff: int = 0`, `last_staff: int \| None = None`, `types: ["Dynamic"]` (also `StaffText`, `SystemText`, `Tempo`, `Expression`), `color`, `opacity: float = 0.55` | Translucent box behind each marking, set on the marking itself (not a style change). `get_score` lists markings under `[Markings]` (JSON: `measures[].markings`). |
| `add_label` | `measure: int`, `text: str`, `staff: int = 0`, `background: str = "#fff2a8"`, `border: str = "#000000"` | Boxed staff text with a colored background at the start of a measure. |
| `clear_highlights` | `start_measure: int = 1`, `end_measure: int \| None = None` (end of score), `first_staff: int = 0`, `last_staff: int \| None = None`, `kinds: ["highlights", "notes", "markings", "labels"]` | `highlights` removes boxes from `highlight_bars`/`highlight_notes`/`highlight_symbols`; `markings` the boxes behind dynamics; `labels` boxed labels; `notes` small notehead boxes from older versions. |

Which highlight to use: one note or one drum (crash hits) → `highlight_notes`; time signatures, repeat signs, measure repeats → `highlight_symbols`; dynamics or other markings → `highlight_markings`; a whole passage on a staff → `highlight_bars`; recolor the notation itself → `color_range` / `highlight_symbols(mode="color")`. All highlight boxes span the full staff height. Never change score style to highlight individual markings.

Coloring notes (`color_range`) changes the notation itself; highlights and labels are separate score elements, so `clear_highlights` removes them without touching note colors.

To compress a score: call `find_repeated_measures`, then run each passage's `apply` steps, starting from the last passage. Re-run the analysis after deleting measures, because measure numbers change.

## Drum charts and measure-repeat signs

| MCP tool | Parameters | What it does |
|---|---|---|
| `find_groove_sections` | `staves: list[int] \| None = None`, `start_measure: int = 1`, `end_measure: int \| None = None`, `tolerance: float = 0.85` | Per staff (drum staves by default): a `road_map`, `sections` (groove bar, `exact` copies, `near` copies with `extra`/`missing` hits, `fills`, `proposed_repeat_signs`, `collapse`), and `apply_exact_copies` ready for `apply_measure_repeats`. Bars that already hold repeat signs count as copies. |
| `apply_measure_repeats` | `staff: int`, `groups: list[{start, end?, bars: 1\|2\|4}]`, `score_path: str \| None = None` | Writes measure-repeat signs into the score file and reloads the score. Returns `applied`, `unchanged`, `skipped` (with reasons, e.g. a tie crossing the barline), `dropped` content, `backup`, `reopened`. |
| `restore_backup` | `backup: str \| None = None`, `score_path: str \| None = None` | Restores the newest (or given) backup of the open score and reloads it; the current version is backed up first. |
| `collapse_to_repeat` | `start: int`, `end: int`, `force: bool = false` | Keeps bar `start` with repeat barlines and play count `end - start + 1`, deletes the rest. Affects every staff; refuses when any staff differs unless `force`. Use in a part-only score. |
| `make_part_score` | `staves: list[int]`, `output_path: str \| None = None` | Saves a copy, opens it, and removes all other parts (MuseScore 4.7+). Staff indices restart from 0 in the new score. |
| `add_rehearsal_mark` | `measure: int`, `text: str` | Rehearsal mark at a measure start (experimental). |

Workflow for a chart: `find_groove_sections` → show the road map and near copies to the user → `apply_measure_repeats` with the approved groups (one call per staff). For "groove bar x N": `make_part_score` → `collapse_to_repeat` per section, from the last section backwards.

`apply_measure_repeats` reloads the score, which resets MuseScore's undo history; undo with `restore_backup`, not Ctrl+Z.

## Batch execution

| MCP tool | Parameters | What it does |
|---|---|---|
| `processSequence` | `sequence: list[object]` | Sends a list of camelCase actions to the plugin in order. Use only when the complete sequence is known and safe. |

The currently accepted inner actions are:

```text
getScore, addNote, addRest, addTuplet, appendMeasure, deleteSelection,
getCursorInfo, goToMeasure, nextElement, prevElement, nextStaff, prevStaff,
selectCurrentMeasure, processSequence, insertMeasure, goToFinalMeasure,
goToBeginningOfScore, setTimeSignature, addLyrics, addInstrument,
setStaffMute, setInstrumentSound, setTempo, selectMeasures, deleteMeasures,
setRepeatBarlines, addVolta, colorRange, setStyle, addRehearsalMark, removeParts,
addHighlight, addLabel, clearHighlights
```

The range actions take `startMeasure`/`endMeasure` (1-based, inclusive) and `startStaff`/`endStaff` (0-based, `endStaff` exclusive).

Each action uses a `params` object. Examples:

```json
{
  "sequence": [
    {"action": "goToBeginningOfScore", "params": {}},
    {"action": "addNote", "params": {"pitch": 60, "duration": {"numerator": 1, "denominator": 4}, "advanceCursorAfterAction": true}},
    {"action": "addRest", "params": {"duration": {"numerator": 1, "denominator": 4}, "advanceCursorAfterAction": true}}
  ]
}
```

`syncStateToSelection` is an internal QML action, not a public MCP tool.

## Names not currently exposed

The upstream README mentions `add_lyrics_to_current_note` and `set_title`, but the current `src/tools/` registration does not expose those tools. Do not call them as if they existed. They require a future repository change before they can be used through MCP.
