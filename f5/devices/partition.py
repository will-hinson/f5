from threading import Event
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field
import pyudev

from .partitionremoved import PartitionRemoved


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
