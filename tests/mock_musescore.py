"""In-memory stand-in for the MuseScore QML plugin's WebSocket API.

Speaks the same envelope as musescore-mcp-websocket.qml
({"status": "success", "result": ...} / {"status": "error", "message": ...})
over a simple 4/4-style score, so the MCP server can be run and tested without
MuseScore. Only a subset of actions is implemented; others return "Unknown command".

Run standalone on the plugin's port:  python tests/mock_musescore.py [--demo]
"""

import asyncio
import copy
import json
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from websockets.asyncio.server import serve

WHOLE_NOTE_TICKS = 1920

SEQUENCE_ACTIONS = {
    "getScore", "addNote", "addRest", "addTuplet", "appendMeasure", "deleteSelection",
    "getCursorInfo", "goToMeasure", "nextElement", "prevElement", "nextStaff", "prevStaff",
    "selectCurrentMeasure", "processSequence", "insertMeasure", "goToFinalMeasure",
    "goToBeginningOfScore", "setTimeSignature", "addLyrics", "addInstrument",
    "setStaffMute", "setInstrumentSound", "setTempo",
    "selectMeasures", "setRepeatBarlines", "addVolta", "colorRange", "deleteMeasures",
    "setStyle", "addRehearsalMark", "removeParts", "addHighlight", "addLabel", "clearHighlights",
    "highlightNotes", "highlightMarkings", "highlightSymbols",
}

TPC_BY_PITCH_CLASS = [14, 21, 16, 23, 18, 13, 20, 15, 22, 17, 24, 19]
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _ticks(duration: Dict[str, int]) -> int:
    return WHOLE_NOTE_TICKS * duration["numerator"] // duration["denominator"]


class MockScore:
    def __init__(self, staves: int = 1, measures: int = 4, title: str = "Mock Score"):
        self.title = title
        self.num_staves = staves
        self.num_measures = measures
        self.time_signature = (4, 4)
        self.tempo = 120
        self.tempo_measure: Optional[int] = None
        self.elements: Dict[int, List[Dict[str, Any]]] = {i: [] for i in range(staves)}
        self.cursor_tick = 0
        self.cursor_staff = 0
        # Repeat structure and formatting, as the plugin would leave them in the score
        self.repeat_barlines_supported = True  # False mimics MuseScore < 4.6
        self.repeats: Dict[int, Dict[str, Any]] = {}  # measure -> {"start", "end", "count"}
        self.voltas: List[Dict[str, Any]] = []
        self.colors: Dict[tuple, str] = {}  # (staff, startTick, voice) -> color
        self.selected: Optional[Dict[str, int]] = None
        self.part_names = [f"Part {i + 1}" for i in range(staves)]
        self.drum_staves: set = set()
        self.style: Dict[str, Any] = {}
        self.rehearsal_marks: List[Dict[str, Any]] = []
        self.highlights: List[Dict[str, Any]] = []  # {"staff", "startMeasure", "endMeasure", "color"}
        self.labels: List[Dict[str, Any]] = []  # {"staff", "measure", "text", "background"}
        self.note_highlights: Dict[tuple, str] = {}  # (staff, startTick, pitch) -> color
        self.markings: List[Dict[str, Any]] = []  # {"staff", "startTick", "name", "text", "highlight"}
        self.symbol_requests: List[Dict[str, Any]] = []  # highlightSymbols calls (the mock has no symbols to find)
        self.score_name = title

    @property
    def measure_ticks(self) -> int:
        num, den = self.time_signature
        return WHOLE_NOTE_TICKS * num // den

    def summary(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """getScore analysis; params mirror the plugin (startMeasure, endMeasure, staves, includeElements)."""
        params = params or {}
        first = max(1, params.get("startMeasure") or 1)
        last = min(self.num_measures, params.get("endMeasure") or self.num_measures)
        staves = [s for s in (params.get("staves") or range(self.num_staves)) if 0 <= s < self.num_staves]
        measures = []
        for number in range(first, last + 1):
            start = (number - 1) * self.measure_ticks
            end = start + self.measure_ticks
            entry = {"measure": number, "startTick": start, "durationTicks": self.measure_ticks}
            if params.get("includeElements") is not False:
                per_staff = {
                    f"staff{s}": [e for e in self.elements[s] if start <= e["startTick"] < end]
                    for s in staves
                }
                entry["numElements"] = sum(len(v) for v in per_staff.values())
                entry["elements"] = per_staff
                entry["markings"] = [
                    {"name": mk["name"], "text": mk["text"], "staff": mk["staff"], "startTick": mk["startTick"],
                     "highlighted": mk.get("highlight") is not None}
                    for mk in self.markings if mk["staff"] in staves and start <= mk["startTick"] < end
                ]
            measures.append(entry)
        return {
            "title": self.title,
            "numMeasures": self.num_measures,
            "startMeasure": first,
            "endMeasure": last,
            "measures": measures,
            "staves": [
                {"name": f"staff{i}", "index": i, "partName": self.part_names[i], "shortName": "",
                 "instrumentId": "drum.group" if i in self.drum_staves else "keyboard.piano",
                 "isDrums": i in self.drum_staves, "visible": True}
                for i in staves
            ],
        }

    def selection(self, start_tick: int, end_tick: int, start_staff: int, end_staff: int) -> Dict[str, Any]:
        elements = {
            f"staff{s}": [
                e for e in self.elements.get(s, []) if start_tick <= e["startTick"] < end_tick
            ]
            for s in range(start_staff, end_staff)
        }
        return {
            "startStaff": start_staff,
            "endStaff": end_staff,
            "startTick": start_tick,
            "elements": elements,
            "totalDuration": end_tick - start_tick,
        }

    def cursor_selection(self) -> Dict[str, Any]:
        return self.selection(self.cursor_tick, self.cursor_tick + 1, self.cursor_staff, self.cursor_staff + 1)


class MockMuseScore:
    """Command dispatcher plus a WebSocket handler."""

    def __init__(self, score: Optional[MockScore] = None):
        self.score = score or MockScore()
        self.history: List[MockScore] = []
        self.port: Optional[int] = None
        self.commands: List[Dict[str, Any]] = []
        self.opened: List[str] = []  # paths opened through openScore / reopenScore

    @property
    def uri(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def handler(self, websocket) -> None:
        async for message in websocket:
            await websocket.send(json.dumps(self.handle_message(message)))

    def handle_message(self, message: str) -> Dict[str, Any]:
        try:
            command = json.loads(message)
            self.commands.append(command)
            return {"status": "success", "result": self.process_command(command)}
        except Exception as e:  # mirrors the plugin's catch-all
            return {"status": "error", "message": str(e)}

    def process_command(self, command: Dict[str, Any]) -> Any:
        action = command.get("action")
        params = command.get("params") or {}
        method = getattr(self, f"_do_{action}", None) if isinstance(action, str) else None
        if method is None:
            raise ValueError(f"Unknown command: {action}")
        return method(params)

    def _mutating(self) -> None:
        self.history.append(copy.deepcopy(self.score))

    def _missing(self, params: Dict[str, Any], required: List[str]) -> Optional[Dict[str, Any]]:
        missing = [k for k in required if params.get(k) is None]
        return {"error": "Missing required parameters: " + ", ".join(missing)} if missing else None

    def _ensure_room(self, end_tick: int) -> None:
        while end_tick > self.score.num_measures * self.score.measure_ticks:
            self.score.num_measures += 1

    def _place(self, element: Dict[str, Any], advance: bool) -> None:
        score = self.score
        element["startTick"] = score.cursor_tick
        element["voice"] = 0
        self._ensure_room(score.cursor_tick + element["durationTicks"])
        staff_elements = score.elements[score.cursor_staff]
        staff_elements[:] = [e for e in staff_elements if e["startTick"] != score.cursor_tick]
        staff_elements.append(element)
        staff_elements.sort(key=lambda e: e["startTick"])
        if advance:
            score.cursor_tick += element["durationTicks"]

    def _placed_selection(self, element: Dict[str, Any]) -> Dict[str, Any]:
        start = element["startTick"]
        return self.score.selection(
            start, start + element["durationTicks"], self.score.cursor_staff, self.score.cursor_staff + 1
        )

    def _range(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Mirror of the plugin's measureRange(): validated measure and staff range, or an error."""
        if (err := self._missing(params, ["startMeasure"])):
            return err
        first = params["startMeasure"]
        last = params.get("endMeasure") or first
        if first < 1 or last > self.score.num_measures or last < first:
            return {"error": f"Invalid measure range {first}-{last} (score has {self.score.num_measures} measures)"}
        start_staff = params.get("startStaff") or 0
        end_staff = params.get("endStaff")
        if end_staff is None:
            end_staff = self.score.num_staves
        if start_staff < 0 or end_staff > self.score.num_staves or start_staff >= end_staff:
            return {"error": f"Invalid staff range {start_staff}-{end_staff}"}
        ticks = self.score.measure_ticks
        return {
            "valid": True, "startMeasure": first, "endMeasure": last,
            "startTick": (first - 1) * ticks, "endTick": last * ticks,
            "startStaff": start_staff, "endStaff": end_staff,
        }

    def _apply_beats(self, rng: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
        """Mirror of the plugin's applyBeats(): narrow the range to startBeat / endBeat (inclusive)."""
        beat = WHOLE_NOTE_TICKS // self.score.time_signature[1]
        measure = self.score.measure_ticks
        if params.get("startBeat") is not None:
            offset = round((params["startBeat"] - 1) * beat)
            if params["startBeat"] < 1 or offset >= measure:
                return {"error": f"Beat {params['startBeat']} is outside measure {rng['startMeasure']}"}
            rng["startTick"] += offset
        if params.get("endBeat") is not None:
            end = (rng["endMeasure"] - 1) * measure + round(params["endBeat"] * beat)
            if params["endBeat"] < 1 or end > rng["endTick"]:
                return {"error": f"Beat {params['endBeat']} is outside measure {rng['endMeasure']}"}
            rng["endTick"] = end
        if rng["startTick"] >= rng["endTick"]:
            return {"error": "The beat range is empty"}
        return rng

    # --- actions -------------------------------------------------------

    def _do_ping(self, params):
        return "pong"

    def _do_getScore(self, params):
        return {"success": True, "analysis": self.score.summary(params)}

    def _do_getCursorInfo(self, params):
        return {
            "success": True,
            "currentSelection": self.score.cursor_selection(),
            "currentScore": self.score.summary({"includeElements": False}),
        }

    def _do_goToBeginningOfScore(self, params):
        self.score.cursor_tick = 0
        self.score.cursor_staff = 0
        return {
            "success": True,
            "message": "Initialized at 0",
            "currentSelection": self.score.cursor_selection(),
            "currentScore": self.score.summary({"includeElements": False}),
        }

    def _do_goToMeasure(self, params):
        if (err := self._missing(params, ["measure"])):
            return err
        measure = params["measure"]
        if measure < 1 or measure > self.score.num_measures:
            return {"error": "Invalid measure number"}
        start = (measure - 1) * self.score.measure_ticks
        self.score.cursor_tick = start
        return {
            "success": True,
            "currentSelection": self.score.selection(
                start, start + self.score.measure_ticks, 0, self.score.num_staves
            ),
        }

    def _do_goToFinalMeasure(self, params):
        return self._do_goToMeasure({"measure": self.score.num_measures})

    def _do_addNote(self, params):
        if (err := self._missing(params, ["pitch", "duration", "advanceCursorAfterAction"])):
            return err
        duration = params["duration"]
        if not duration.get("numerator") or not duration.get("denominator"):
            return {"error": "Duration must be specified as { numerator: int, denominator: int }"}
        self._mutating()
        pitch = params["pitch"]
        note = {
            "pitchMidi": pitch,
            "tpc": TPC_BY_PITCH_CLASS[pitch % 12],
            "pitchName": NOTE_NAMES[pitch % 12],
        }
        staff_elements = self.score.elements[self.score.cursor_staff]
        if params.get("addToChord") is True and staff_elements and staff_elements[-1]["name"] == "Chord":
            chord = staff_elements[-1]
            chord["notes"].append(note)
            return {
                "success": True,
                "message": f"Note added with pitch {pitch}",
                "currentSelection": self._placed_selection(chord),
            }
        chord = {
            "name": "Chord",
            "durationTicks": _ticks(duration),
            "isTie": False,
            "isTuplet": False,
            "notes": [note],
        }
        self._place(chord, params["advanceCursorAfterAction"])
        return {
            "success": True,
            "message": f"Note added with pitch {pitch}",
            "currentSelection": self._placed_selection(chord),
        }

    def _do_addRest(self, params):
        if (err := self._missing(params, ["duration", "advanceCursorAfterAction"])):
            return err
        duration = params["duration"]
        if not duration.get("numerator") or not duration.get("denominator"):
            return {"error": "Duration must be specified as { numerator: int, denominator: int }"}
        self._mutating()
        rest = {"name": "Rest", "durationTicks": _ticks(duration), "isTie": False, "isTuplet": False}
        self._place(rest, params["advanceCursorAfterAction"])
        return {
            "success": True,
            "message": "Rest added",
            "currentSelection": self._placed_selection(rest),
        }

    def _do_addLyrics(self, params):
        lyrics = params.get("lyrics")
        if not isinstance(lyrics, list) or not lyrics:
            return {"error": "Lyrics must be specified as an array of strings"}
        self._mutating()
        remaining = list(lyrics)
        verse = params.get("verse") or 0
        added = skipped = 0
        for element in self.score.elements[self.score.cursor_staff]:
            if element["startTick"] < self.score.cursor_tick:
                continue
            if not remaining:
                break
            if element["name"] == "Chord":
                element["lyrics"] = [{"text": remaining.pop(0), "no": verse, "syllabic": 0}]
                added += 1
            else:
                skipped += 1
        message = f"Added {added} lyrics"
        if skipped:
            message += f", skipped {skipped} rests"
        if remaining:
            message += f", {len(remaining)} lyrics remaining"
        return {
            "success": True,
            "message": message,
            "addedCount": added,
            "skippedCount": skipped,
            "remainingLyrics": remaining,
            "currentSelection": self.score.cursor_selection(),
        }

    def _do_appendMeasure(self, params):
        self._mutating()
        count = params.get("count") or 1
        self.score.num_measures += count
        return {
            "success": True,
            "message": f"{count} measure(s) appended",
            "currentSelection": self.score.cursor_selection(),
        }

    def _do_addInstrument(self, params):
        if (err := self._missing(params, ["instrumentId"])):
            return err
        self._mutating()
        self.score.elements[self.score.num_staves] = []
        self.score.num_staves += 1
        return {"success": True, "message": f"Instrument {params['instrumentId']} added"}

    def _do_setTimeSignature(self, params):
        if (err := self._missing(params, ["numerator", "denominator"])):
            return err
        self._mutating()
        self.score.time_signature = (params["numerator"], params["denominator"])
        return {
            "success": True,
            "message": f"Time signature set to {params['numerator']}/{params['denominator']}",
        }

    def _do_setTempo(self, params):
        if (err := self._missing(params, ["bpm"])):
            return err
        if params.get("measure") is not None:
            rng = self._range({"startMeasure": params["measure"]})
            if "error" in rng:
                return rng
        self._mutating()
        self.score.tempo = params["bpm"]
        self.score.tempo_measure = params.get("measure")
        return {"success": True, "message": f"Tempo set to {params['bpm']} BPM"}

    def _do_selectMeasures(self, params):
        rng = self._range(params)
        if "error" in rng:
            return rng
        self.score.selected = {k: rng[k] for k in ("startMeasure", "endMeasure", "startStaff", "endStaff")}
        return {"success": True, "message": f"Selected measures {rng['startMeasure']}-{rng['endMeasure']}",
                **{k: rng[k] for k in ("startTick", "endTick", "startStaff", "endStaff")}}

    def _do_setRepeatBarlines(self, params):
        rng = self._range({"startMeasure": params.get("startMeasure"), "endMeasure": params.get("endMeasure")})
        if "error" in rng:
            return rng
        if not self.score.repeat_barlines_supported:
            self._do_selectMeasures(rng)
            return {"success": False, "fallback": "manual",
                    "message": "This MuseScore version does not let plugins set repeat barlines (needs MuseScore 4.6+)."}
        self._mutating()
        first, last = rng["startMeasure"], rng["endMeasure"]
        clear = params.get("clear") is True
        self.score.repeats.setdefault(first, {})["start"] = not clear
        self.score.repeats.setdefault(last, {})["end"] = not clear
        self.score.repeats[last]["count"] = 2 if clear else params.get("playCount") or 2
        return {"success": True, "message": f"Repeat barlines set around measures {first}-{last}"}

    def _do_addVolta(self, params):
        rng = self._range({"startMeasure": params.get("startMeasure"), "endMeasure": params.get("endMeasure")})
        if "error" in rng:
            return rng
        self._mutating()
        endings = params.get("endings") or [1]
        self.score.voltas.append({
            "startMeasure": rng["startMeasure"], "endMeasure": rng["endMeasure"], "endings": endings,
            "text": params.get("text") or ", ".join(str(e) for e in endings) + ".",
        })
        return {"success": True, "experimental": True, "message": "Volta requested"}

    def _do_colorRange(self, params):
        rng = self._range(params)
        if "error" in rng:
            return rng
        rng = self._apply_beats(rng, params)
        if "error" in rng:
            return rng
        self._mutating()
        color = params.get("color") or "#000000"
        voices = params.get("voices") or [0, 1, 2, 3]
        count = 0
        for staff in range(rng["startStaff"], rng["endStaff"]):
            for e in self.score.elements[staff]:
                if rng["startTick"] <= e["startTick"] < rng["endTick"] and e.get("voice", 0) in voices:
                    self.score.colors[(staff, e["startTick"], e.get("voice", 0))] = color
                    count += len(e.get("notes", [])) if e["name"] == "Chord" else 1
        return {"success": True, "colored": count, "message": f"Colored {count} note(s)/rest(s) {color}"}

    def _do_deleteMeasures(self, params):
        rng = self._range({"startMeasure": params.get("startMeasure"), "endMeasure": params.get("endMeasure")})
        if "error" in rng:
            return rng
        self._mutating()
        score = self.score
        start, end = rng["startTick"], rng["endTick"]
        removed_ticks = end - start
        removed_bars = rng["endMeasure"] - rng["startMeasure"] + 1

        def shift(tick):
            return tick - removed_ticks if tick >= end else tick

        for staff, elements in score.elements.items():
            kept = [e for e in elements if not start <= e["startTick"] < end]
            for e in kept:
                e["startTick"] = shift(e["startTick"])
            score.elements[staff] = kept
        score.colors = {
            (st, shift(t), v): c for (st, t, v), c in score.colors.items() if not start <= t < end
        }

        def renumber(m):
            return m - removed_bars if m > rng["endMeasure"] else m

        score.repeats = {
            renumber(m): r for m, r in score.repeats.items()
            if not rng["startMeasure"] <= m <= rng["endMeasure"]
        }
        score.voltas = [
            {**v, "startMeasure": renumber(v["startMeasure"]), "endMeasure": renumber(v["endMeasure"])}
            for v in score.voltas
            if v["endMeasure"] < rng["startMeasure"] or v["startMeasure"] > rng["endMeasure"]
        ]
        score.num_measures -= removed_bars
        score.cursor_tick, score.cursor_staff = 0, 0
        return {"success": True, "message": f"Deleted measures {rng['startMeasure']}-{rng['endMeasure']}",
                "numMeasures": score.num_measures}

    def _do_setStyle(self, params):
        if (err := self._missing(params, ["key", "value"])):
            return err
        self._mutating()
        self.score.style[params["key"]] = params["value"]
        return {"success": True}

    def _do_addRehearsalMark(self, params):
        if (err := self._missing(params, ["measure", "text"])):
            return err
        rng = self._range({"startMeasure": params["measure"]})
        if "error" in rng:
            return rng
        self._mutating()
        self.score.rehearsal_marks.append({"measure": params["measure"], "text": params["text"]})
        return {"success": True, "experimental": True}

    def _do_addHighlight(self, params):
        rng = self._range(params)
        if "error" in rng:
            return rng
        rng = self._apply_beats(rng, params)
        if "error" in rng:
            return rng
        self._mutating()
        for staff in range(rng["startStaff"], rng["endStaff"]):
            self.score.highlights.append({"staff": staff, "startMeasure": rng["startMeasure"],
                                          "endMeasure": rng["endMeasure"], "color": params.get("color")})
        return {"success": True, "experimental": True, "added": rng["endStaff"] - rng["startStaff"]}

    def _do_addLabel(self, params):
        if (err := self._missing(params, ["measure", "text"])):
            return err
        staff = params.get("staff") or 0
        rng = self._range({"startMeasure": params["measure"], "startStaff": staff, "endStaff": staff + 1})
        if "error" in rng:
            return rng
        self._mutating()
        self.score.labels.append({"staff": staff, "measure": params["measure"], "text": params["text"],
                                  "background": params.get("background")})
        return {"success": True}

    def _do_highlightNotes(self, params):
        rng = self._range(params)
        if "error" in rng:
            return rng
        rng = self._apply_beats(rng, params)
        if "error" in rng:
            return rng
        self._mutating()
        pitches = params.get("pitches")
        count = 0
        for staff in range(rng["startStaff"], rng["endStaff"]):
            for e in self.score.elements[staff]:
                if e["name"] != "Chord" or not rng["startTick"] <= e["startTick"] < rng["endTick"]:
                    continue
                for note in e["notes"]:
                    if pitches and note["pitchMidi"] not in pitches:
                        continue
                    self.score.note_highlights[(staff, e["startTick"], note["pitchMidi"])] = params.get("color")
                    count += 1
        return {"success": True, "highlighted": count}

    def _do_highlightSymbols(self, params):
        rng = self._range(params)
        if "error" in rng:
            return rng
        self._mutating()
        self.score.symbol_requests.append(params)
        return {"success": True, "found": {"time_signatures": 1, "repeat_barlines": 0, "measure_repeats": 0},
                "message": "Highlighted 1 symbol(s)"}

    def _markings_in(self, rng, types):
        return [mk for mk in self.score.markings
                if mk["name"] in types and rng["startStaff"] <= mk["staff"] < rng["endStaff"]
                and rng["startTick"] <= mk["startTick"] < rng["endTick"]]

    def _do_highlightMarkings(self, params):
        rng = self._range(params)
        if "error" in rng:
            return rng
        self._mutating()
        found = self._markings_in(rng, params.get("types") or ["Dynamic"])
        for mk in found:
            mk["highlight"] = None if params.get("clear") else params.get("color")
        return {"success": True, "found": len(found), "changed": len(found)}

    def _do_clearHighlights(self, params):
        rng = self._range(params)
        if "error" in rng:
            return rng
        kinds = params.get("kinds") or ["highlights", "notes", "markings", "labels"]

        def inside(staff, measure):
            return (rng["startStaff"] <= staff < rng["endStaff"]
                    and rng["startMeasure"] <= measure <= rng["endMeasure"])

        score = self.score
        before = len(score.highlights) + len(score.labels) + len(score.note_highlights)
        before += sum(1 for mk in score.markings if mk.get("highlight"))
        self._mutating()
        if "highlights" in kinds:
            score.highlights = [h for h in score.highlights if not inside(h["staff"], h["startMeasure"])]
        if "labels" in kinds:
            score.labels = [l for l in score.labels if not inside(l["staff"], l["measure"])]
        if "notes" in kinds:
            score.note_highlights = {
                k: c for k, c in score.note_highlights.items()
                if not (rng["startStaff"] <= k[0] < rng["endStaff"] and rng["startTick"] <= k[1] < rng["endTick"])
            }
        if "markings" in kinds:
            for mk in self._markings_in(rng, ["Dynamic", "StaffText", "SystemText", "Tempo", "Expression"]):
                mk["highlight"] = None
        after = len(score.highlights) + len(score.labels) + len(score.note_highlights)
        after += sum(1 for mk in score.markings if mk.get("highlight"))
        return {"success": True, "removed": before - after}

    def _do_scoreFileInfo(self, params):
        return {"success": True, "scoreName": self.score.score_name, "title": self.score.title,
                "numMeasures": self.score.num_measures, "numStaves": self.score.num_staves}

    def _do_saveScore(self, params):
        return {"success": True, "message": "Score saved"}

    def _do_openScore(self, params):
        if (err := self._missing(params, ["path"])):
            return err
        self.opened.append(params["path"])
        return {"success": True, "path": params["path"], "scoreName": self.score.score_name}

    def _do_reopenScore(self, params):
        return self._do_openScore(params)

    def _do_removeParts(self, params):
        if (err := self._missing(params, ["keepStaves"])):
            return err
        keep = [s for s in range(self.score.num_staves) if s in params["keepStaves"]]
        if not keep:
            return {"error": "None of the staves exist"}
        self._mutating()
        score = self.score
        removed = score.num_staves - len(keep)
        score.elements = {new: score.elements[old] for new, old in enumerate(keep)}
        score.part_names = [score.part_names[s] for s in keep]
        score.drum_staves = {new for new, old in enumerate(keep) if old in score.drum_staves}
        score.num_staves = len(keep)
        return {"success": True, "removed": removed, "kept": score.part_names,
                "numStaves": score.num_staves}

    def _do_undo(self, params):
        if self.history:
            self.score = self.history.pop()
        return {"success": True, "message": "Undo successful"}

    def _do_processSequence(self, params):
        sequence = params.get("sequence")
        if not sequence:
            return {"error": "No sequence specified"}
        try:
            for command in sequence:
                if command.get("action") not in SEQUENCE_ACTIONS:
                    raise ValueError(f"Invalid command: {command.get('action')}")
                self.process_command(command)
        except Exception as e:
            return {"error": str(e)}
        return {
            "success": True,
            "message": "Sequence processed",
            "currentSelection": self.score.cursor_selection(),
        }


@asynccontextmanager
async def running_mock(score: Optional[MockScore] = None, host: str = "127.0.0.1", port: int = 0):
    """Start the mock on a free port (or the given one) and yield the MockMuseScore."""
    mock = MockMuseScore(score)
    async with serve(mock.handler, host, port) as ws_server:
        mock.port = next(iter(ws_server.sockets)).getsockname()[1]
        yield mock


def demo_score() -> MockScore:
    """Twinkle Twinkle on two staves, for previewing the viewer."""
    mock = MockMuseScore(MockScore(staves=2, measures=4, title="Twinkle Twinkle (demo)"))
    quarter = {"numerator": 1, "denominator": 4}
    half = {"numerator": 1, "denominator": 2}

    def note(pitch, duration=quarter, **extra):
        mock.process_command({"action": "addNote", "params": {
            "pitch": pitch, "duration": duration, "advanceCursorAfterAction": True, **extra}})

    for pitch, duration in [
        (60, quarter), (60, quarter), (67, quarter), (67, quarter),
        (69, quarter), (69, quarter), (67, half),
        (65, quarter), (65, quarter), (64, quarter), (64, quarter),
        (62, quarter), (62, quarter),
    ]:
        note(pitch, duration)
    note(60, half, advanceCursorAfterAction=False)
    note(64, half, addToChord=True)
    note(67, half, addToChord=True)

    mock.process_command({"action": "goToBeginningOfScore"})
    mock.process_command({"action": "addLyrics", "params": {
        "lyrics": ["Twin", "kle", "twin", "kle", "lit", "tle", "star"]}})

    mock.score.cursor_staff, mock.score.cursor_tick = 1, 0
    for bar in range(4):
        for pitch in ((48, 55) if bar % 2 == 0 else (53, 55)):
            note(pitch, half)
    mock.score.cursor_staff, mock.score.cursor_tick = 0, 0
    return mock.score


async def _main(demo: bool) -> None:
    async with running_mock(demo_score() if demo else None, port=8765) as mock:
        print(f"Mock MuseScore listening on {mock.uri}")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(_main(demo="--demo" in sys.argv))
    except KeyboardInterrupt:
        pass
