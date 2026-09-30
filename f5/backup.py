from collections.abc import Generator
import io
import logging
from pathlib import Path, PurePosixPath
from queue import Queue
from threading import Lock, Thread
from typing import TYPE_CHECKING, override

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError
import humanize
from pydantic import BaseModel
import pytsk3
import rich
from rich.console import Console
from rich.progress import (
    Progress,
    TextColumn,
    BarColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
    TaskID,
)

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .devices import Partition

if TYPE_CHECKING:
    from _typeshed import MaybeNone, WriteableBuffer
    from mypy_boto3_s3 import S3Client


_logger: logging.Logger = logging.getLogger(__name__)
_tsk_lock: Lock = Lock()


def _make_progress() -> Progress:
    console: Console = rich.get_console()

    return Progress(
        TextColumn("[bold]{task.description}", justify="right"),
        BarColumn(),
        DownloadColumn(binary_units=True),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        disable=not console.is_terminal,  # no bars when piped or run under a service
    )


class UploadProgress:
    """boto3 Callback that feeds a per-file task and an overall task."""

    def __init__(self, progress: Progress, overall: TaskID, task: TaskID):
        self._progress = progress
        self._overall = overall
        self._task = task
        self._sent = 0
        self._lock = Lock()

    def __call__(self, n: int) -> None:
        with self._lock:
            self._sent += n
        self._progress.advance(self._task, n)
        self._progress.advance(self._overall, n)

    def reset(self) -> None:
        """Undo this file's contribution before a retry."""
        with self._lock:
            sent, self._sent = self._sent, 0
        self._progress.advance(self._overall, -sent)
        self._progress.reset(self._task)


class S3Config(BaseModel):
    profile: str
    bucket: str
    prefix: str

    def get_partition_prefix(self: "S3Config", partition: Partition) -> str:
        return (
            (PurePosixPath("/") / self.prefix / partition.uuid)
            .relative_to("/")
            .as_posix()
        )

    def get_partition_file_key(
        self: "S3Config", partition: Partition, file: Path
    ) -> str:
        return (
            PurePosixPath("/") / self.prefix / partition.uuid / file.relative_to("/")
        ).as_posix()


class TskReader(io.RawIOBase):
    _file: pytsk3.File
    _size: int
    _position: int

    def __init__(self: "TskReader", file: pytsk3.File) -> None:
        self._file = file
        self._size = file.info.meta.size
        self._position = 0

    @override
    def readable(self: "TskReader") -> bool:
        return True

    @override
    def seekable(self: "TskReader") -> bool:
        return True

    def seek(self: "TskReader", offset: int, whence: int = io.SEEK_SET) -> int:
        match whence:
            case io.SEEK_SET:
                self._position = 0 + offset
            case io.SEEK_CUR:
                self._position += offset
            case io.SEEK_END:
                self._position = self._size
            case _:
                raise ValueError(f"Unknown whence value {whence}")

        return self._position

    @override
    def readinto(self, buffer: WriteableBuffer, /) -> int | MaybeNone:
        # get the target number of bytes for this read. it's either the
        # remaining bytes available of length of the target buffer,
        # whichever's smaller
        byte_count: int = min(len(buffer), self._size - self._position)
        if byte_count <= 0:
            return 0

        # read the bytes with a mutex since pytsk3 isn't threadsafe
        data: bytes
        with _tsk_lock:
            data = self._file.read_random(self._position, byte_count)

        # put the result in the buffer and seek our position ahead
        buffer[: len(data)] = data
        self._position += len(data)

        return len(data)


def _walk_files(
    directory: pytsk3.Directory,
    path: Path = Path("/"),
) -> Generator[tuple[Path, int]]:
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
            yield from _walk_files(
                entry.as_directory(),
                entry_path,
            )
        else:
            yield entry_path, entry.info.meta.size


def _discover_files(
    filesystem: pytsk3.FS_Info, root_path: Path = Path("/")
) -> set[tuple[Path, int]]:
    return set(
        _walk_files(
            filesystem.open_dir(str(root_path)),
        )
    )


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, max=60),
    retry=retry_if_exception_type((S3UploadFailedError, BotoCoreError, ClientError)),
    before_sleep=before_sleep_log(_logger, logging.WARNING),
    reraise=True,
)
def _upload_file(
    s3: S3Client,
    partition: Partition,
    filesystem: pytsk3.FS_Info,
    target_file: Path,
    config: S3Config,
    callback: UploadProgress,
) -> None:
    with TskReader(filesystem.open(target_file.as_posix())) as reader:
        s3.upload_fileobj(
            reader,
            Bucket=config.bucket,
            Key=config.get_partition_file_key(partition, target_file),
            Callback=callback,
        )


def _get_remote_files(
    s3: S3Client,
    config: S3Config,
    partition: Partition,
) -> set[tuple[Path, int]]:
    remote_files: set[tuple[Path, int]] = set()

    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=config.bucket,
        Prefix=config.get_partition_prefix(partition),
    ):
        for obj in page.get("Contents", []):
            remote_files.add(
                (
                    (
                        Path("/")
                        / Path(obj["Key"]).relative_to(
                            config.get_partition_prefix(partition)
                        )
                    ),
                    obj["Size"],
                )
            )

    return remote_files


def _backup_partition(
    partition: Partition,
    s3_config: S3Config,
) -> None:
    # discover all the files on the filesystem
    filesystem: pytsk3.FS_Info = pytsk3.FS_Info(
        pytsk3.Img_Info(
            partition.device.device_node,
        )
    )
    local_files: set[tuple[Path, int]] = _discover_files(filesystem)
    _logger.info(
        "Discovered %s total file%s on %s",
        f"{len(local_files):,}",
        ("s" if len(local_files) != 1 else ""),
        partition.device.device_node,
    )

    # check which of the objects exist on the remote
    s3: S3Client = boto3.Session(
        profile_name=s3_config.profile,
    ).client("s3")
    remote_files: set[tuple[Path, int]] = _get_remote_files(
        s3,
        config=s3_config,
        partition=partition,
    )

    target_files: set[tuple[Path, int]] = local_files - remote_files
    total_size: int = sum(entry[1] for entry in target_files)
    _logger.info(
        "Found %s file%s to sync from %s to remote storage (%s)",
        f"{len(target_files)}",
        ("s" if len(target_files) != 1 else ""),
        partition.device.device_node,
        humanize.naturalsize(
            total_size,
            binary=True,
        ),
    )

    with _make_progress() as progress:
        overall_task: TaskID = progress.add_task(
            "Total",
            total=total_size,
        )

        for index, (target_file, target_file_size) in enumerate(target_files):
            file_task: TaskID = progress.add_task(
                f"{index}/{len(target_files)} {target_file.name}",
                total=target_file_size,
            )
            _logger.info(
                "Uploading %s of size %s from %s (%s/%s)",
                target_file.as_posix(),
                humanize.naturalsize(
                    target_file_size,
                    binary=True,
                ),
                partition.device.device_node,
                index + 1,
                len(target_files),
            )

            _upload_file(
                s3,
                partition=partition,
                filesystem=filesystem,
                target_file=target_file,
                config=s3_config,
                callback=UploadProgress(
                    progress,
                    overall_task,
                    file_task,
                ),
            )
            progress.remove_task(file_task)

        progress.remove_task(overall_task)


def make_backup_thread(
    partition_queue: Queue[Partition],
    s3_config: S3Config,
) -> Thread:
    """
    Start a thread to handle backups for new partitions.
    """

    def backup_worker() -> None:
        while True:
            partition = partition_queue.get()
            if partition is None:
                break

            _backup_partition(
                partition,
                s3_config=s3_config,
            )

            _logger.info(
                "Partition %s of device %s is fully backed up",
                partition.device.device_node,
                partition.parent_device.device_node,
            )

    return Thread(
        target=backup_worker,
        name="backup-worker",
        daemon=True,
    )
