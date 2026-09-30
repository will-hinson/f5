from pathlib import Path

from pydantic import BaseModel


class FileKey(BaseModel):
    path: Path
    size: int

    def __eq__(self: "FileKey", other: object) -> bool:
        if not isinstance(other, FileKey):
            return NotImplemented

        return self.path == other.path and self.size == other.size

    def __hash__(self: "FileKey") -> int:
        return hash(
            (
                self.path.as_posix(),
                self.size,
            )
        )
