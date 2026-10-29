"""Score coloring tools for MuseScore MCP."""

import re
from typing import Annotated, List, Literal, Optional

from fastmcp.exceptions import ToolError

from ..client import MuseScoreClient


def range_params(start_measure, end_measure, first_staff, last_staff) -> dict:
    """Plugin range parameters; the plugin's endStaff is exclusive, the tools' last_staff inclusive."""
    params = {
        "startMeasure": start_measure,
        "endMeasure": end_measure if end_measure is not None else start_measure,
        "startStaff": first_staff,
    }
    if last_staff is not None:
        params["endStaff"] = last_staff + 1
    return params


def beat_params(params: dict, start_beat, end_beat) -> dict:
    """Add the optional beat range (start_beat in the first measure, end_beat inclusive in the last)."""
    if start_beat is not None:
        params["startBeat"] = start_beat
    if end_beat is not None:
        params["endBeat"] = end_beat
    return params


START_BEAT_HELP = (
    "Beat in start_measure where coloring starts (1-based; fractions allowed, e.g. 3.5 for the 'and' of 3). "
    "A beat is the time signature's denominator: a quarter in 4/4, an eighth in 6/8. Defaults to the start of the measure."
)
END_BEAT_HELP = "Last beat in end_measure to include, the whole beat (e.g. 4 = through the end of beat 4). Defaults to the end of the measure."


def translucent(color: str, opacity: float) -> str:
    """"#rrggbb" plus an opacity (0-1) as Qt's "#aarrggbb"."""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ToolError(f'Color must look like "#rrggbb", got {color!r}')
    if not 0 < opacity <= 1:
        raise ToolError("opacity must be between 0 (exclusive) and 1")
    return f"#{round(opacity * 255):02x}{color[1:]}"


def setup_formatting_tools(mcp, client: MuseScoreClient):
    """Setup score coloring tools."""

    @mcp.tool(
        description=(
            "Color the notes and rests (with their stems, beams, accidentals, dots and ties) "
            "in a range of measures and staves, e.g. to highlight a passage. Use start_beat/end_beat to "
            "color only part of a bar, e.g. a fill on beats 3-4: notes and rests starting in that span are colored."
        ),
        tags={"formatting"},
    )
    async def color_range(
        start_measure: Annotated[int, "First measure to color (1-based)"],
        end_measure: Annotated[Optional[int], "Last measure to color (1-based, inclusive). Defaults to start_measure."] = None,
        first_staff: Annotated[int, "First staff to color (0-based)"] = 0,
        last_staff: Annotated[Optional[int], "Last staff to color (0-based, inclusive). Defaults to the last staff."] = None,
        color: Annotated[str, 'Color as "#rrggbb" (e.g. "#d62728" for red)'] = "#d62728",
        voices: Annotated[Optional[List[int]], "Voices to color (0-3). Defaults to all voices."] = None,
        start_beat: Annotated[Optional[float], START_BEAT_HELP] = None,
        end_beat: Annotated[Optional[float], END_BEAT_HELP] = None,
    ):
        params = beat_params(range_params(start_measure, end_measure, first_staff, last_staff), start_beat, end_beat)
        params["color"] = color
        if voices:
            params["voices"] = voices
        return await client.request("colorRange", params)

    @mcp.tool(
        description="Reset notes and rests in a range of measures and staves (optionally only some beats) to the default black.",
        tags={"formatting"},
    )
    async def clear_color(
        start_measure: Annotated[int, "First measure to reset (1-based)"],
        end_measure: Annotated[Optional[int], "Last measure to reset (1-based, inclusive). Defaults to start_measure."] = None,
        first_staff: Annotated[int, "First staff to reset (0-based)"] = 0,
        last_staff: Annotated[Optional[int], "Last staff to reset (0-based, inclusive). Defaults to the last staff."] = None,
        start_beat: Annotated[Optional[float], START_BEAT_HELP] = None,
        end_beat: Annotated[Optional[float], END_BEAT_HELP] = None,
    ):
        params = beat_params(range_params(start_measure, end_measure, first_staff, last_staff), start_beat, end_beat)
        params["color"] = "#000000"
        return await client.request("colorRange", params)

    @mcp.tool(
        description=(
            "Highlight bars like a highlighter pen: a thick, semi-transparent band behind the notes of each staff in "
            "the range, following the bars across line breaks. start_beat/end_beat limit it to part of a bar. "
            "Saved in the score; remove with clear_highlights. Experimental: check the result in the score."
        ),
        tags={"formatting"},
    )
    async def highlight_bars(
        start_measure: Annotated[int, "First measure to highlight (1-based)"],
        end_measure: Annotated[Optional[int], "Last measure (1-based, inclusive). Defaults to start_measure."] = None,
        first_staff: Annotated[int, "First staff (0-based)"] = 0,
        last_staff: Annotated[Optional[int], "Last staff (0-based, inclusive). Defaults to first_staff."] = None,
        color: Annotated[str, 'Highlight color as "#rrggbb" (default yellow)'] = "#ffe600",
        opacity: Annotated[float, "Opacity from 0 to 1"] = 0.35,
        start_beat: Annotated[Optional[float], START_BEAT_HELP] = None,
        end_beat: Annotated[Optional[float], END_BEAT_HELP] = None,
    ):
        params = beat_params(range_params(start_measure, end_measure, first_staff,
                                          last_staff if last_staff is not None else first_staff), start_beat, end_beat)
        params["color"] = translucent(color, opacity)
        return await client.request("addHighlight", params)

    @mcp.tool(
        description=(
            "Add a label in a colored box (a boxed staff text) at the start of a measure, e.g. a section tag like "
            "'Groove x7' or 'Fill'. Saved in the score; remove with clear_highlights."
        ),
        tags={"formatting"},
    )
    async def add_label(
        measure: Annotated[int, "Measure number (1-based)"],
        text: Annotated[str, "Label text"],
        staff: Annotated[int, "Staff to attach the label to (0-based)"] = 0,
        background: Annotated[str, 'Box color as "#rrggbb" (default pale yellow)'] = "#fff2a8",
        border: Annotated[str, 'Box border color as "#rrggbb"'] = "#000000",
    ):
        for value in (background, border):
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                raise ToolError(f'Colors must look like "#rrggbb", got {value!r}')
        return await client.request("addLabel", {
            "measure": measure, "text": text, "staff": staff, "background": background, "border": border,
        })

    @mcp.tool(
        description=(
            "Remove highlights (highlight_bars) and boxed labels (add_label) from a range of measures and staves. "
            "Note colors from color_range are reset with clear_color instead."
        ),
        tags={"formatting"},
    )
    async def clear_highlights(
        start_measure: Annotated[int, "First measure (1-based)"] = 1,
        end_measure: Annotated[Optional[int], "Last measure (1-based, inclusive). Defaults to the end of the score."] = None,
        first_staff: Annotated[int, "First staff (0-based)"] = 0,
        last_staff: Annotated[Optional[int], "Last staff (0-based, inclusive). Defaults to the last staff."] = None,
        kinds: Annotated[
            List[Literal["highlights", "labels"]], "What to remove: highlight bands, boxed labels, or both"
        ] = ["highlights", "labels"],
    ):
        if end_measure is None:
            end_measure = (await client.request("scoreFileInfo"))["numMeasures"]
        params = range_params(start_measure, end_measure, first_staff, last_staff)
        params["kinds"] = kinds
        return await client.request("clearHighlights", params)

