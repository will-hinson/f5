import logging
from queue import Queue
from threading import Thread
from typing import ClassVar

from pydantic import BaseModel, ConfigDict
import pyudev

_logger: logging.Logger = logging.getLogger(__name__)


class Partition(BaseModel):
    device: pyudev.Device
    parent_device: pyudev.Device
    uuid: str

    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
    )


def make_monitor_thread(partition_queue: Queue[Partition]) -> Thread:
    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="block")

    def handle(device: pyudev.Device) -> None:
        parent_device: pyudev.Device | None = device.find_parent("block", "disk")
        if device.action == "add" and device.device_type == "partition":
            _logger.info(
                f"Detected new partition {device.device_node} on device "
                f"{parent_device.device_node if parent_device else 'unknown'}"
            )
            partition_queue.put(
                Partition(
                    device=device,
                    parent_device=parent_device,
                    uuid=device.properties["ID_FS_UUID"],
                )
            )
        elif device.action == "remove" and device.device_type == "partition":
            _logger.info(
                f"Detected removal of partition {device.device_node} on device "
                f"{parent_device.device_node if parent_device else 'unknown'}"
            )

            # remove the partition from the queue if it exists
            for partition in list(partition_queue.queue):
                if partition.device_node == device.device_node:
                    partition_queue.queue.remove(partition)
                    _logger.info(f"Removed partition {device.device_node} from queue")

            # TODO: Implement logic to handle stopping any ongoing backup processes for
            # the removed partition

    observer: pyudev.MonitorObserver = pyudev.MonitorObserver(
        monitor,
        callback=handle,
        name="partition-watch",
    )

    return observer
