from queue import Queue
from threading import Thread

from .monitorconfig import MonitorConfig
from .partition import Partition
from .partitionremoved import PartitionRemoved

__all__ = [
    "MonitorConfig",
    "Partition",
    "PartitionRemoved",
    "make_monitor_thread",
]


def make_monitor_thread(
    partition_queue: Queue[Partition],
    config: MonitorConfig,
) -> Thread:
    from .monitorworker import MonitorWorker

    return MonitorWorker(
        partition_queue=partition_queue,
        config=config,
    )
