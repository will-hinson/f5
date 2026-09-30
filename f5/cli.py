import logging
from queue import Queue
from threading import Thread

import click

from .backup import make_backup_thread, S3Config
from .devices import MonitorConfig, Partition, make_monitor_thread
from .logging import setup_logging


_logger: logging.Logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--exclude-device",
    "-e",
    multiple=True,
    help="Exclude a specific block device",
    required=False,
    type=click.Path(
        exists=False,
        file_okay=True,
        dir_okay=False,
        readable=False,
    ),
)
@click.option(
    "--include-device",
    "-i",
    multiple=True,
    help="Include a specific block device",
    required=False,
    type=click.Path(
        exists=False,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
)
@click.option(
    "--profile",
    "-p",
    help="AWS profile to use for S3 access",
    required=False,
    type=str,
    default="",
)
@click.option(
    "--bucket",
    "-b",
    help="S3 bucket to use for backups",
    required=True,
    type=str,
)
@click.option(
    "--prefix",
    "-x",
    help="S3 prefix to use for backups",
    required=False,
    type=str,
    default="f5",
)
@click.option(
    "--allow-fixed/--no-allow-fixed",
    help="Allow partitions from fixed devices to be backed up",
    required=False,
    type=bool,
    default=False,
)
@click.version_option()
def cli(
    include_device: tuple[str, ...],
    exclude_device: tuple[str, ...],
    profile: str,
    bucket: str,
    prefix: str,
    allow_fixed: bool,
) -> None:
    """
    f5 - Automatic backups to S3-compatible storage
    """

    setup_logging()

    # start a monitor thread to watch for new partitions
    partition_queue: Queue[Partition] = Queue()
    threads: list[Thread] = [
        make_backup_thread(
            partition_queue=partition_queue,
            s3_config=S3Config(
                profile=profile,
                bucket=bucket,
                prefix=prefix,
            ),
        ),
        make_monitor_thread(
            partition_queue=partition_queue,
            config=MonitorConfig(
                allow_fixed=allow_fixed,
                include_devices=set(include_device),
                exclude_devices=set(exclude_device),
            ),
        ),
    ]
    for thread in threads:
        thread.start()
        _logger.info(f"Started thread {thread.name}")
    for thread in threads:
        thread.join()
