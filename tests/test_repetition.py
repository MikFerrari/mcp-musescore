"""Unit tests for the repeated-material analysis (no MuseScore or mock needed)."""

from src.utils.repetition import find_repeats, measure_fingerprint, staff_fingerprints
from src.utils.sections import find_sections

BAR = 1920
PITCHES = {letter: 60 + i for i, letter in enumerate("ABCDEFGH")}


def bar_elements(number, letter):
    """Four quarter notes of one pitch per letter; "-" is a whole-bar rest."""
    start = (number - 1) * BAR
    if letter == "-":
        return [{"name": "Rest", "startTick": start, "durationTicks": BAR, "voice": 0}]
    return [
        {"name": "Chord", "startTick": start + i * 480, "durationTicks": 480, "voice": 0,
         "notes": [{"pitchMidi": PITCHES[letter]}]}
        for i in range(4)
    ]


def measures(*staff_patterns):
    """getScore-style measures; each pattern string gives one staff, one letter per bar."""
    return [
        {
            "measure": i + 1,
            "startTick": i * BAR,
            "durationTicks": BAR,
            "elements": {f"staff{s}": bar_elements(i + 1, p[i]) for s, p in enumerate(staff_patterns)},
        }
        for i in range(len(staff_patterns[0]))
    ]


def test_fingerprint_ignores_position_in_score_and_lyrics():
    first = bar_elements(1, "A")
    later = bar_elements(5, "A")
    later[0]["lyrics"] = [{"text": "la"}]
    assert measure_fingerprint(first, 0) == measure_fingerprint(later, 4 * BAR)
    assert measure_fingerprint(first, 0) != measure_fingerprint(bar_elements(1, "B"), 0)


def test_one_bar_repeat_on_one_staff_only():
    result = find_repeats(measures("ABBC", "EFGH"))
    assert result["measure_repeats"] == [{"bars": 1, "from": 3, "to": 3, "staves": [0]}]
    assert result["passages"] == []


def test_two_and_four_bar_measure_repeats():
    two = find_repeats(measures("ABAB"))["measure_repeats"]
    four = find_repeats(measures("ABCDABCD"))["measure_repeats"]
    assert two == [{"bars": 2, "from": 3, "to": 4, "staves": [0]}]
    assert four == [{"bars": 4, "from": 5, "to": 8, "staves": [0]}]


def test_rest_bars_are_not_measure_repeats():
    assert find_repeats(measures("A--B"))["measure_repeats"] == []


def test_staves_with_the_same_repeats_are_grouped():
    result = find_repeats(measures("AAB", "CCD", "EFG"))
    assert result["measure_repeats"] == [{"bars": 1, "from": 2, "to": 2, "staves": [0, 1]}]


def test_passage_repeated_on_all_staves():
    (passage,) = find_repeats(measures("EABCABC", "EDEFDEF"))["passages"]
    assert passage["type"] == "repeat"
    assert (passage["start"], passage["end"], passage["times"]) == (2, 4, 2)
    assert passage["duplicate_measures"] == [5, 7]
    assert passage["saves_measures"] == 3


def test_repeat_count_prefers_the_biggest_saving():
    (passage,) = find_repeats(measures("AAAA"))["passages"]
    assert (passage["start"], passage["end"], passage["times"]) == (1, 1, 4)
    assert passage["saves_measures"] == 3


def test_passage_with_first_and_second_endings():
    (passage,) = find_repeats(measures("ABCDABCE"))["passages"]
    assert passage["type"] == "repeat_with_endings"
    assert (passage["start"], passage["end"]) == (1, 3)
    assert passage["first_ending"] == [4, 4]
    assert passage["second_ending"] == [8, 8]
    assert passage["duplicate_measures"] == [5, 7]


def test_passage_needs_every_staff_to_repeat():
    assert find_repeats(measures("ABCABC", "DDDEEE"))["passages"] == []


def test_measure_numbers_follow_the_input_range():
    ms = measures("CABAB")[1:]  # analyse measures 2-5 only
    (passage,) = find_repeats(ms)["passages"]
    assert (passage["start"], passage["end"], passage["duplicate_measures"]) == (2, 3, [4, 5])


def sign_bar(number, size=1):
    """A bar holding a measure-repeat sign, as the plugin reports it."""
    return [{"name": "MeasureRepeat", "numMeasures": size, "startTick": (number - 1) * BAR,
             "durationTicks": BAR, "voice": 0}]


def test_existing_repeat_signs_count_as_copies():
    ms = measures("ABCD")
    ms[1]["elements"]["staff0"] = sign_bar(2)  # bar 2 is a repeat sign of bar 1: A A C D
    fps, existing = staff_fingerprints(ms, "staff0")
    assert fps[1] == fps[0] and existing == {1}
    result = find_repeats(ms)
    assert result["measure_repeats"] == []  # bar 2 is already a sign
    assert result["passages"][0]["start"] == 1  # A A is still seen as a repeated passage


def test_two_bar_sign_resolves_both_bars():
    ms = measures("ABAB")
    ms[2]["elements"]["staff0"] = sign_bar(3, size=2)
    ms[3]["elements"]["staff0"] = [{"name": "Rest", "startTick": 3 * BAR, "durationTicks": BAR, "voice": 0}]
    fps, existing = staff_fingerprints(ms, "staff0")
    assert fps[2:] == fps[:2] and existing == {2, 3}


def groove_bar(number, kick_beats=(1, 3), snare_beats=(2, 4), extra=(), cymbal=42):
    """Kick and snare on the given beats, eighth notes on the cymbal (closed hi-hat by default)."""
    start = (number - 1) * BAR
    hits = [(b, 36) for b in kick_beats] + [(b, 38) for b in snare_beats] + list(extra)
    hits += [(b + h / 2, cymbal) for b in range(1, 5) for h in range(2)]
    by_tick = {}
    for beat, pitch in hits:
        by_tick.setdefault(int((beat - 1) * 480), []).append(pitch)
    return [{"name": "Chord", "startTick": start + t, "durationTicks": 240, "voice": 0,
             "notes": [{"pitchMidi": p} for p in sorted(ps)]} for t, ps in sorted(by_tick.items())]


def fill_bar(number):
    start = (number - 1) * BAR
    return [{"name": "Chord", "startTick": start + i * 240, "durationTicks": 240, "voice": 0,
             "notes": [{"pitchMidi": 45 + (i % 4)}]} for i in range(8)]


def drum_measures(bars):
    return [{"measure": i + 1, "startTick": i * BAR, "durationTicks": BAR, "elements": {"staff0": b}}
            for i, b in enumerate(bars)]


def test_sections_split_groove_near_copies_and_fills():
    bars = [groove_bar(n) for n in range(1, 5)]                 # 1-4 groove
    bars.append(groove_bar(5, extra=[(2.5, 36)]))              # 5 near: extra kick
    bars += [groove_bar(n) for n in (6, 7)]                    # 6-7 groove
    bars.append(fill_bar(8))                                   # 8 fill, section ends
    bars += [groove_bar(n, cymbal=51) for n in range(9, 13)]  # 9-12 new groove on the ride

    first, second = find_sections(drum_measures(bars), "staff0")
    assert (first["start"], first["end"], first["groove_bar"]) == (1, 8, 1)
    assert first["exact"] == [2, 3, 4, 6, 7] and first["fills"] == [8]
    (near,) = first["near"]
    assert near["bar"] == 5 and near["extra"] == ["kick on beat 2.5"] and near["missing"] == []
    # bar 6 follows the near bar 5, so a 1-bar sign there would repeat bar 5: not proposed
    assert first["proposed_repeat_signs"] == [{"start": 2, "end": 4, "bars": 1}, {"start": 7, "end": 7, "bars": 1}]
    assert first["collapse"] == {"start": 1, "end": 4, "play_count": 4}
    assert (second["start"], second["end"], second["exact"]) == (9, 12, [10, 11, 12])


def test_new_groove_without_fill_starts_a_section():
    bars = [groove_bar(n) for n in (1, 2)] + [groove_bar(n, cymbal=51) for n in (3, 4)]
    first, second = find_sections(drum_measures(bars), "staff0")
    assert (first["end"], first["fills"]) == (2, [])
    assert (second["start"], second["groove_bar"]) == (3, 3)


def test_mid_section_fill_keeps_the_section():
    bars = [groove_bar(1), groove_bar(2), fill_bar(3), groove_bar(4)]
    (section,) = find_sections(drum_measures(bars), "staff0")
    assert section["fills"] == [3] and section["exact"] == [2, 4]
    assert section["proposed_repeat_signs"] == [{"start": 2, "end": 2, "bars": 1}]


def test_two_extra_kicks_still_count_as_near():
    bars = [groove_bar(1), groove_bar(2, kick_beats=(1, 2, 3, 4))]
    (section,) = find_sections(drum_measures(bars), "staff0")
    assert section["near"][0]["extra"] == ["kick on beat 2", "kick on beat 4"]

