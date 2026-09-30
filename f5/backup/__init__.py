import logging
from queue import Queue
from threading import Thread

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

from ..console import console
from ..devices import Partition, PartitionRemoved
from .filekey import FileKey
from .s3config import S3Config
from .s3filesystem import S3Filesystem
from .tskfilesystem import TskFilesystem
from .uploadprogress import UploadProgress


_logger: logging.Logger = logging.getLogger(__name__)


def _make_progress() -> Progress:
    return Progress(
        TextColumn("[bold]{task.description}", justify="right"),
        BarColumn(),
        DownloadColumn(binary_units=True),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        disable=not console.is_terminal,  # no bars when piped or run under a service
    )


def _get_target_files(
    partition: Partition,
    tsk: TskFilesystem,
    s3: S3Filesystem,
) -> tuple[set[FileKey], int]:
    # discover all the files on the local filesystem
    local_files: set[FileKey] = tsk.discover_files()
    _logger.info(
        "Discovered %s total file%s on %s",
        f"{len(local_files):,}",
        ("s" if len(local_files) != 1 else ""),
        partition.device.device_node,
    )

    # check which of the objects exist on the remote and only return those that don't
    target_files: set[FileKey] = local_files - s3.get_partition_files(
        partition=partition
    )
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
    return target_files, total_size


def _upload_with_progress(
    progress: Progress,
    partition: Partition,
    target_file_key: FileKey,
    index: int,
    total_files: int,
    overall_task: TaskID,
    s3: S3Filesystem,
    tsk: TskFilesystem,
) -> None:
    try:
        file_task: TaskID = progress.add_task(
            f"{index + 1}/{total_files} {target_file_key.path.name}",
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
            total_files,
        )

        s3.upload_partition_file(
            partition=partition,
            filesystem=tsk,
            file_key=target_file_key,
            callback=UploadProgress(
                progress,
                overall_task,
                file_task,
            ),
        )
        progress.remove_task(file_task)

    finally:
        # always remove any remaining task entries to clear the console up
        for task_id in list(progress.task_ids):
            try:
                progress.remove_task(task_id)
            except KeyError:
                ...


def _backup_partition(
    partition: Partition,
    s3_config: S3Config,
) -> None:
    tsk: TskFilesystem = TskFilesystem(partition=partition)
    s3: S3Filesystem = S3Filesystem(config=s3_config)

    # get the target files to sync and the total size of all of them
    target_files, total_size = _get_target_files(partition, tsk, s3)
    with _make_progress() as progress:
        overall_task: TaskID = progress.add_task(
            "Total",
            total=total_size,
        )

        for index, target_file_key in enumerate(
            sorted(
                # iterate over all of the file in reverse size order, starting
                # with the largest file first
                target_files,
                key=lambda file_key: file_key.size,
                reverse=True,
            )
        ):
            _upload_with_progress(
                progress=progress,
                s3=s3,
                tsk=tsk,
                partition=partition,
                target_file_key=target_file_key,
                index=index,
                total_files=len(target_files),
                overall_task=overall_task,
            )

        try:
            progress.remove_task(overall_task)
        except KeyError:
            ...


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

            partition_name: str = partition.device.device_node
            try:
                partition.check_removed()
                _backup_partition(
                    partition,
                    s3_config=s3_config,
                )
            except (PartitionRemoved, OSError) as exc:
                if isinstance(exc, OSError) and not partition.removed.is_set():
                    _logger.exception(f"Backup of partition {partition_name} failed")
                else:
                    _logger.warning(
                        f"Backup of partition {partition_name} stopped: device removed"
                    )
            except Exception:
                _logger.exception(f"Backup of partition {partition_name} failed")
            else:
                _logger.info(
                    "Partition %s of device %s is fully backed up",
                    partition.device.device_node,
                    partition.parent_device.device_node,
                )
            finally:
                partition_queue.task_done()

    return Thread(
        target=backup_worker,
        name="backup-worker",
        daemon=True,
    )
