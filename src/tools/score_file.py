"""Tools that edit the open score's file directly, for changes the plugin API cannot make.

The flow is: the plugin saves the score, the file is backed up and edited here, and the plugin
closes and reopens it. The tab shows the change, but MuseScore's undo history starts over, so
every edit keeps a backup that restore_backup can bring back.
"""

import shutil
from pathlib import Path
from typing import Annotated, Dict, List, Literal, Optional

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from ..client import MuseScoreClient
from ..mscz import ScoreFile, ScoreFileError, apply_measure_repeats as write_measure_repeats
from ..mscz.locate import backup_score, find_score_file, list_backups


class RepeatGroup(BaseModel):
    start: int = Field(description="First measure to turn into repeat signs (1-based)")
    end: Optional[int] = Field(None, description="Last measure (1-based, inclusive). Defaults to start + bars - 1.")
    bars: Literal[1, 2, 4] = Field(1, description="Size of each sign: 1, 2 or 4 bars; start..end is split into signs of this size")


def split_groups(groups: List[RepeatGroup]) -> List[tuple]:
    """(first measure, size) for every sign in the requested runs."""
    pairs = []
    for g in groups:
        end = g.end if g.end is not None else g.start + g.bars - 1
        length = end - g.start + 1
        if length < 1 or length % g.bars:
            raise ToolError(f"Measures {g.start}-{end} cannot be split into {g.bars}-bar repeat signs")
        pairs += [(start, g.bars) for start in range(g.start, end + 1, g.bars)]
    return pairs


def setup_score_file_tools(mcp, client: MuseScoreClient):
    """Setup tools that edit the score file on disk."""

    async def current_score_path(score_path: Optional[str]) -> Path:
        info = await client.request("scoreFileInfo")
        try:
            return find_score_file(info.get("scoreName", ""), score_path)
        except ScoreFileError as e:
            raise ToolError(str(e))

    async def reopen(path: Path) -> Dict:
        res = await client.send_command("reopenScore", {"path": str(path)})
        result = res.get("result")
        if isinstance(result, dict) and result.get("success"):
            return {"reopened": True}
        return {
            "reopened": False,
            "next_step": (
                f"The file was updated on disk but MuseScore did not reload it. Close the score in MuseScore "
                f"without saving, open {path} again (File > Open Recent) and restart the MuseScore API Server plugin."
            ),
        }

    @mcp.tool(
        description=(
            "Turn bars of one staff into measure-repeat signs (1-, 2- or 4-bar) automatically. MuseScore's plugin "
            "API cannot place these signs, so this saves the score, backs the file up, writes the signs into the "
            "file and reloads it in MuseScore. The reload resets MuseScore's undo history: use restore_backup to undo. "
            "Each sign repeats the bars just before it, and the replaced bars' notes are deleted. Bars with ties or "
            "slurs crossing their barlines are skipped and reported. Pass all groups for a staff in one call."
        ),
        tags={"structure"},
    )
    async def apply_measure_repeats(
        staff: Annotated[int, "Staff index (0-based)"],
        groups: Annotated[List[RepeatGroup], 'Runs of bars to replace, e.g. [{"start": 7, "end": 12, "bars": 1}]'],
        score_path: Annotated[Optional[str], "Path of the score file. Defaults to the file of the score open in MuseScore."] = None,
    ):
        pairs = split_groups(groups)
        await client.request("saveScore")
        path = await current_score_path(score_path)

        try:
            score = ScoreFile(path)
            report = write_measure_repeats(score, staff, pairs)
        except ScoreFileError as e:
            raise ToolError(str(e))
        result = {"file": str(path), **report}
        if not report["applied"]:
            result["message"] = "Nothing to change; the score was not reloaded."
            return result

        result["backup"] = str(backup_score(path))
        score.save()
        result.update(await reopen(path))
        result["undo"] = "restore_backup() brings back the version before this change."
        return result

    @mcp.tool(
        description=(
            "Bring back the version of the open score saved before an apply_measure_repeats edit (the newest backup "
            "by default) and reload it in MuseScore. The current version is backed up first."
        ),
        tags={"structure"},
    )
    async def restore_backup(
        backup: Annotated[Optional[str], "Backup file to restore. Defaults to the newest backup of this score."] = None,
        score_path: Annotated[Optional[str], "Path of the score file. Defaults to the file of the score open in MuseScore."] = None,
    ):
        path = await current_score_path(score_path)
        if backup:
            source = Path(backup).expanduser()
            if not source.is_file():
                raise ToolError(f"Backup not found: {source}")
        else:
            backups = list_backups(path)
            if not backups:
                raise ToolError(f"No backups of {path.name} found")
            source = backups[0]

        await client.request("saveScore")
        current = backup_score(path)
        shutil.copy2(source, path)
        return {"file": str(path), "restored": str(source), "previous_version_backup": str(current), **(await reopen(path))}
