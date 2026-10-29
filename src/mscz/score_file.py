"""Read and write MuseScore score files (.mscz zip archives or plain .mscx XML)."""

import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple


class ScoreFileError(Exception):
    pass


class ScoreFile:
    """A score file whose main .mscx is parsed into an ElementTree.

    For .mscz archives every other entry (style, thumbnail, settings...) is kept byte for byte.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._entries: List[Tuple[zipfile.ZipInfo, bytes]] = []
        self._mscx_name: Optional[str] = None

        if self.path.suffix.lower() == ".mscx":
            data = self.path.read_bytes()
        else:
            try:
                with zipfile.ZipFile(self.path) as z:
                    self._entries = [(info, z.read(info.filename)) for info in z.infolist()]
            except zipfile.BadZipFile as e:
                raise ScoreFileError(f"{self.path} is not a MuseScore .mscz file: {e}")
            self._mscx_name = self._find_main_mscx()
            data = dict((info.filename, d) for info, d in self._entries)[self._mscx_name]

        self.root = ET.fromstring(data)
        if self.root.tag != "museScore" or self.root.find("Score") is None:
            raise ScoreFileError(f"{self.path} does not contain a MuseScore score")

    def _find_main_mscx(self) -> str:
        names = [info.filename for info, _ in self._entries]
        if "META-INF/container.xml" in names:
            container = ET.fromstring(dict((i.filename, d) for i, d in self._entries)["META-INF/container.xml"])
            for rootfile in container.iter("rootfile"):
                full_path = rootfile.get("full-path", "")
                if full_path.endswith(".mscx") and full_path in names:
                    return full_path
        candidates = [n for n in names if n.endswith(".mscx") and "/" not in n]
        if not candidates:
            raise ScoreFileError(f"No .mscx score found in {self.path}")
        return candidates[0]

    @property
    def score(self) -> ET.Element:
        return self.root.find("Score")

    @property
    def version(self) -> str:
        return self.root.get("version", "")

    def staves(self) -> List[ET.Element]:
        """Body <Staff id="N"> elements (staff index N-1), in order."""
        return self.score.findall("Staff")

    def to_bytes(self) -> bytes:
        return ET.tostring(self.root, encoding="UTF-8", xml_declaration=True)

    def save(self, path=None) -> Path:
        """Write the score (to its own path by default) via a temporary file and an atomic rename."""
        target = Path(path) if path else self.path
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        os.close(fd)
        try:
            if target.suffix.lower() == ".mscx":
                Path(tmp).write_bytes(self.to_bytes())
            else:
                with zipfile.ZipFile(tmp, "w") as z:
                    for info, data in self._entries:
                        if info.filename == self._mscx_name:
                            data = self.to_bytes()
                        z.writestr(info, data)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return target
