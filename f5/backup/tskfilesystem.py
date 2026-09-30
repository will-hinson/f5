from collections.abc import Generator
from pathlib import Path

import pytsk3

from ..devices import Partition
from .filekey import FileKey


class TskFilesystem:
    _partition: Partition
    _filesystem: pytsk3.FS_Info

    def __init__(self: "TskFilesystem", partition: Partition) -> None:
        self._partition = partition
        self._filesystem = pytsk3.FS_Info(
            pytsk3.Img_Info(
                partition.device.device_node,
            )
        )

    def _walk_files(
        self: "TskFilesystem",
        directory: pytsk3.Directory,
        path: Path = Path("/"),
    ) -> Generator[FileKey]:
        """
        Discover files in a filesystem.
        """

        for entry in directory:
            # get the name for this entry as string. if this is the current directory
            # or parent directory, ignore it
            entry_name: str = entry.info.name.name.decode("utf-8", "replace")
            if entry_name in [".", ".."]:
                continue

            # ignore deleted/unallocated entries with no metadata
            if entry.info.meta is None:
                continue

            entry_path: Path = path / entry_name

            # if this is a directory, recurse into it
            if entry.info.meta.type == pytsk3.TSK_FS_META_TYPE_DIR:
                yield from self._walk_files(
                    entry.as_directory(),
                    entry_path,
                )
            else:
                yield FileKey(
                    path=entry_path,
                    size=entry.info.meta.size,
                )

    def discover_files(
        self: "TskFilesystem",
        root_path: Path = Path("/"),
    ) -> set[FileKey]:
        return set(
            self._walk_files(
                self._filesystem.open_dir(str(root_path)),
            )
        )

    def open(self: "TskFilesystem", path: Path):
        return self._filesystem.open(path.as_posix())
