from pydantic import BaseModel


class MonitorConfig(BaseModel):
    allow_fixed: bool
    include_devices: set[str]
    exclude_devices: set[str]
