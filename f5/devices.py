import logging
from queue import Queue
from threading import Event, Lock, Thread
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field
import pyudev

_logger: logging.Logger = logging.getLogger(__name__)


class PartitionRemoved(RuntimeError):
    """
    Raised when a partition has been removed and check_removed() is called
    """


class Partition(BaseModel):
    device: pyudev.Device
    parent_device: pyudev.Device
    uuid: str
    removed: Event = Field(default_factory=Event)

    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
    )

    def check_removed(self: "Partition") -> None:
        if self.removed.is_set():
            raise PartitionRemoved(f"{self.device.device_node} was removed")


class MonitorConfig(BaseModel):
    allow_fixed: bool


def _device_is_removable(device: pyudev.Device) -> bool:
    # seek up to the parent device that is the actual disk
    while device is not None and device.get("DEVTYPE") != "disk":
        device = device.parent

    if device is None:
        return False

    return bool(device.attributes.asint("removable"))


def _find_disk_parent(device: pyudev.Device) -> pyudev.Device | None:
    return device.find_parent("block", "disk")


def _enqueue_partition(
    device: pyudev.Device,
    partition_queue: Queue[Partition],
    active_partitions: dict[str, Partition],
    partition_queue_mutex: Lock,
) -> None:
    parent_device: pyudev.Device = _find_disk_parent(device)

    _logger.info(
        f"Detected new partition {device.device_node} on device "
        f"{parent_device.device_node if parent_device else 'unknown'}"
    )
    new_partition: Partition = Partition(
        device=device,
        parent_device=parent_device,
        uuid=device.properties["ID_FS_UUID"],
    )

    with partition_queue_mutex:
        partition_queue.put(new_partition)
        active_partitions[new_partition.uuid] = new_partition


def _dequeue_partition(
    device: pyudev.Device,
    partition_queue: Queue[Partition],
    active_partitions: dict[str, Partition],
    partition_queue_mutex: Lock,
) -> None:
    parent_device: pyudev.Device | None = _find_disk_parent(device)

    _logger.info(
        f"Detected removal of partition {device.device_node} on device "
        f"{parent_device.device_node if parent_device else 'unknown'}"
    )

    with partition_queue_mutex:
        # remove the partition from the queue if it exists
        for partition in list(partition_queue.queue):
            if partition.device.device_node == device.device_node:
                partition_queue.queue.remove(partition)
                _logger.info(f"Removed partition {device.device_node} from queue")

        # set the removed event for this partition
        fs_uuid: str = device.properties["ID_FS_UUID"]
        if fs_uuid in active_partitions:
            active_partitions[fs_uuid].removed.set()
            active_partitions.pop(fs_uuid)


def make_monitor_thread(
    partition_queue: Queue[Partition],
    config: MonitorConfig,
) -> Thread:
    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="block")

    active_partitions: dict[str, Partition] = {}
    partition_queue_mutex: Lock = Lock()

    def handle(device: pyudev.Device) -> None:
        # ignore things that aren't partitions with filesystems
        if device.device_type != "partition":
            return

        if (
            # device.action will be None when we feed in the partitions at startup
            device.action is None or device.action == "add"
        ) and device.device_type == "partition":
            if not _device_is_removable(device) and not config.allow_fixed:
                _logger.info(
                    f"Detected non-removable device {device.device_node}, ignoring"
                )
                return

            _enqueue_partition(
                device=device,
                partition_queue=partition_queue,
                active_partitions=active_partitions,
                partition_queue_mutex=partition_queue_mutex,
            )
        elif device.action == "remove" and device.device_type == "partition":
            _dequeue_partition(
                device=device,
                partition_queue=partition_queue,
                active_partitions=active_partitions,
                partition_queue_mutex=partition_queue_mutex,
            )

    def _monitor_run() -> None:
        # push events for all connected partitions on startup
        for device in context.list_devices(
            subsystem="block",
            DEVTYPE="partition",
        ):
            handle(device)

        # then, start an observer to listen for new connection events
        observer: pyudev.MonitorObserver = pyudev.MonitorObserver(
            monitor,
            callback=handle,
        )
        observer.start()
        observer.join()

    thread = Thread(target=_monitor_run, daemon=True)
    return thread
