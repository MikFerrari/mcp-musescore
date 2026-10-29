"""Split a staff (typically drums) into groove sections for a chart-style layout.

A section starts with a groove bar. The bars after it are exact copies of the groove,
near copies (similar enough, e.g. one extra kick), or fills (bars that break the pattern).
A section ends where a new groove starts or after a fill that is not followed by the groove.

Near copies are never proposed for replacement on their own: replacing them with a repeat
sign would delete their differences, so they are reported for the user to approve.
"""

from typing import Any, Dict, List, Sequence, Set, Tuple

from .repetition import Fingerprint, staff_fingerprints

TICKS_PER_BEAT = 480

# General MIDI percussion names for the common kit pieces
DRUM_NAMES = {
    35: "kick", 36: "kick", 37: "side stick", 38: "snare", 39: "clap", 40: "snare",
    41: "low tom", 42: "closed hi-hat", 43: "low tom", 44: "pedal hi-hat", 45: "mid tom",
    46: "open hi-hat", 47: "mid tom", 48: "high tom", 49: "crash", 50: "high tom",
    51: "ride", 52: "china", 53: "ride bell", 54: "tambourine", 55: "splash", 56: "cowbell",
    57: "crash 2", 59: "ride 2",
}

Event = Tuple[int, int]


def bar_events(fingerprint: Fingerprint) -> Set[Event]:
    """(tick within the bar, pitch) for every note in a bar."""
    return {(item[0], pitch) for item in fingerprint if item[2] == "Chord" for pitch in item[4]}


def similarity(a: Set[Event], b: Set[Event]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def describe(events: Set[Event]) -> List[str]:
    """Readable hits, e.g. "kick on beat 2.5"."""
    out = []
    for tick, pitch in sorted(events):
        beat = tick / TICKS_PER_BEAT + 1
        beat_text = f"{beat:g}"
        out.append(f"{DRUM_NAMES.get(pitch, f'pitch {pitch}')} on beat {beat_text}")
    return out


def _runs(numbers: List[int]) -> List[Tuple[int, int]]:
    runs: List[List[int]] = []
    for n in numbers:
        if runs and runs[-1][1] + 1 == n:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return [tuple(r) for r in runs]


def find_sections(measures: Sequence[Dict[str, Any]], staff: str, tolerance: float = 0.85) -> List[Dict[str, Any]]:
    """Groove sections of one staff, with 1-based measure numbers."""
    fps, existing = staff_fingerprints(measures, staff)
    numbers = [m.get("measure", i + 1) for i, m in enumerate(measures)]
    events = [bar_events(fp) for fp in fps]
    n = len(fps)

    def kind(j: int, groove: int) -> str:
        if fps[j] == fps[groove]:
            return "exact"
        if events[j] and similarity(events[j], events[groove]) >= tolerance:
            return "near"
        return "other"

    sections = []
    i = 0
    while i < n:
        groove = i
        exact, near, fills = [], [], []
        j = i + 1
        next_start = n
        while j < n:
            k = kind(j, groove)
            if k == "exact":
                exact.append(j)
            elif k == "near":
                near.append(j)
            elif j + 1 < n and kind(j + 1, groove) != "other":
                fills.append(j)  # fill inside the section, the groove comes back
            elif j + 1 < n and (fps[j + 1] == fps[j] or similarity(events[j + 1], events[j]) >= tolerance):
                next_start = j  # bar j starts a new groove
                break
            else:
                fills.append(j)  # fill that ends the section
                next_start = j + 1
                break
            j += 1
        end = min(next_start, n) - 1

        # 1-bar repeat signs repeat the previous bar, so a bar qualifies only if it equals the bar before it
        signs = [b for b in exact if fps[b] == fps[b - 1] and b not in existing]
        contiguous = groove
        while contiguous + 1 <= end and contiguous + 1 in exact:
            contiguous += 1

        section = {
            "start": numbers[groove],
            "end": numbers[end],
            "groove_bar": numbers[groove],
            "exact": [numbers[b] for b in exact],
            "near": [
                {
                    "bar": numbers[b],
                    "similarity": round(similarity(events[b], events[groove]), 2),
                    "extra": describe(events[b] - events[groove]),
                    "missing": describe(events[groove] - events[b]),
                }
                for b in near
            ],
            "fills": [numbers[b] for b in fills],
            "already_repeat_signs": [numbers[b] for b in sorted(existing) if groove < b <= end],
            "proposed_repeat_signs": [
                {"start": numbers[a], "end": numbers[b], "bars": 1} for a, b in _runs(signs)
            ],
        }
        if contiguous > groove:
            section["collapse"] = {"start": numbers[groove], "end": numbers[contiguous],
                                   "play_count": contiguous - groove + 1}
        sections.append(section)
        i = next_start
    return sections
