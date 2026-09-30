import logging
from queue import Queue
from threading import Lock, Thread

import pyudev

from .monitorconfig import MonitorConfig
from ..devices import Partition

_logger: logging.Logger = logging.getLogger(__name__)


class MonitorWorker(Thread):
    _active_partitions: dict[str, Partition]
    _config: MonitorConfig
    _device_context: pyudev.Context
    _monitor: pyudev.Monitor
    _partition_queue_mutex: Lock
    _partition_queue: Queue[Partition]

    def __init__(
        self: "MonitorWorker",
        partition_queue: Queue[Partition],
        config: MonitorConfig,
        daemon: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(daemon=daemon, **kwargs)

        self._config = config
        self._device_context = pyudev.Context()
        self._monitor = pyudev.Monitor.from_netlink(self._device_context)
        self._monitor.filter_by(subsystem="block")

        self._active_partitions = {}
        self._partition_queue_mutex = Lock()
        self._partition_queue = partition_queue

    def run(self: "MonitorWorker") -> None:
        # push events for all connected partitions on startup
        for device in self._device_context.list_devices(
            subsystem="block",
            DEVTYPE="partition",
        ):
            self._handle_device(device)

        # then, start an observer to listen for new connection events
        observer: pyudev.MonitorObserver = pyudev.MonitorObserver(
            self._monitor,
            callback=self._handle_device,
        )
        observer.start()
        observer.join()

    def _device_included(self: "MonitorWorker", *devices: pyudev.Device | None) -> bool:
        names = {d.device_node for d in devices if d is not None and d.device_node}

        include = self._config.include_devices
        exclude = self._config.exclude_devices

        if include and names.isdisjoint(include):
            return False
        return names.isdisjoint(exclude)

    def _handle_device(self: "MonitorWorker", device: pyudev.Device) -> None:
        # ignore things that aren't partitions with filesystems
        if device.device_type != "partition":
            return

        if not self._device_included(device, self._find_disk_parent(device)):
            _logger.info(
                f"Detected device {device.device_node} is excluded by config, ignoring"
            )
            return

        if (
            # device.action will be None when we feed in the partitions at startup
            device.action is None or device.action == "add"
        ) and device.device_type == "partition":
            if not self._device_is_removable(device) and not self._config.allow_fixed:
                _logger.info(
                    f"Detected non-removable device {device.device_node}, ignoring"
                )
                return

            self._enqueue_partition(device=device)
        elif device.action == "remove" and device.device_type == "partition":
            self._dequeue_partition(device=device)

    def _device_is_removable(self: "MonitorWorker", device: pyudev.Device) -> bool:
        # seek up to the parent device that is the actual disk
        while device is not None and device.get("DEVTYPE") != "disk":
            device = device.parent

        if device is None:
            return False

        return bool(device.attributes.asint("removable"))

    def _find_disk_parent(
        self: "MonitorWorker", device: pyudev.Device
    ) -> pyudev.Device | None:
        return device.find_parent("block", "disk")

    def _enqueue_partition(
        self: "MonitorWorker",
        device: pyudev.Device,
    ) -> None:
        parent_device: pyudev.Device = self._find_disk_parent(device)

        _logger.info(
            f"Detected new partition {device.device_node} on device "
            f"{parent_device.device_node if parent_device else 'unknown'}"
        )
        new_partition: Partition = Partition(
            device=device,
            parent_device=parent_device,
            uuid=device.properties["ID_FS_UUID"],
        )

        with self._partition_queue_mutex:
            self._partition_queue.put(new_partition)
            self._active_partitions[new_partition.uuid] = new_partition

    def _dequeue_partition(
        self: "MonitorWorker",
        device: pyudev.Device,
    ) -> None:
        parent_device: pyudev.Device | None = self._find_disk_parent(device)

        _logger.info(
            f"Detected removal of partition {device.device_node} on device "
            f"{parent_device.device_node if parent_device else 'unknown'}"
        )

        with self._partition_queue_mutex:
            # remove the partition from the queue if it exists
            for partition in list(self._partition_queue.queue):
                if partition.device.device_node == device.device_node:
                    self._partition_queue.queue.remove(partition)
                    _logger.info(f"Removed partition {device.device_node} from queue")

            # set the removed event for this partition
            fs_uuid: str = device.properties["ID_FS_UUID"]
            if fs_uuid in self._active_partitions:
                self._active_partitions[fs_uuid].removed.set()
                self._active_partitions.pop(fs_uuid)
