import logging
from queue import Queue
from threading import Thread
from typing import TYPE_CHECKING

import humanize
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

from ..devices import Partition
from .filekey import FileKey
from .s3 import get_remote_files, get_s3_client, upload_file
from .s3config import S3Config
from .tskfilesystem import TskFilesystem
from .uploadprogress import UploadProgress

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


_logger: logging.Logger = logging.getLogger(__name__)


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


def _backup_partition(
    partition: Partition,
    s3_config: S3Config,
) -> None:
    # discover all the files on the filesystem
    filesystem: TskFilesystem = TskFilesystem(partition=partition)
    local_files: set[FileKey] = filesystem.discover_files()
    _logger.info(
        "Discovered %s total file%s on %s",
        f"{len(local_files):,}",
        ("s" if len(local_files) != 1 else ""),
        partition.device.device_node,
    )

    # check which of the objects exist on the remote
    s3: S3Client = get_s3_client(s3_config)
    remote_files: set[FileKey] = get_remote_files(
        s3,
        config=s3_config,
        partition=partition,
    )

    target_files: set[FileKey] = local_files - remote_files
    total_size: int = sum(
        map(
            lambda file_key: file_key.size,
            target_files,
        )
    )
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

        for index, target_file_key in enumerate(target_files):
            file_task: TaskID = progress.add_task(
                f"{index}/{len(target_files)} {target_file_key.path.name}",
                total=target_file_key.size,
            )
            _logger.info(
                "Uploading %s of size %s from %s (%s/%s)",
                target_file_key.path.as_posix(),
                humanize.naturalsize(
                    target_file_key.size,
                    binary=True,
                ),
                partition.device.device_node,
                index + 1,
                len(target_files),
            )

            upload_file(
                s3,
                partition=partition,
                filesystem=filesystem,
                target_file_key=target_file_key,
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
