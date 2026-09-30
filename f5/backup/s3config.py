from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from ..devices import Partition


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
