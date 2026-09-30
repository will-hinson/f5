import logging
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError
from tenacity import (
    RetryCallState,
    before_sleep_log,
    retry,
    retry_base,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


from ..devices import Partition
from .filekey import FileKey
from .s3config import S3Config
from .tskfilesystem import TskFilesystem
from .tskreader import TskReader
from .uploadprogress import UploadProgress

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


_logger: logging.Logger = logging.getLogger(__name__)


class _retry_unless_partition_removed(retry_base):
    def __call__(self, retry_state: RetryCallState) -> bool:
        partition: Partition = retry_state.kwargs["partition"]
        return not partition.removed.is_set()


class S3Filesystem:
    _client: S3Client
    _config: S3Config

    def __init__(self: "S3Filesystem", config: S3Config) -> None:
        self._config = config
        self._client = boto3.Session(
            profile_name=config.profile,
        ).client("s3")

    def get_partition_files(
        self: "S3Filesystem",
        partition: Partition,
    ) -> set[FileKey]:
        remote_files: set[FileKey] = set()

        for page in self._client.get_paginator("list_objects_v2").paginate(
            Bucket=self._config.bucket,
            Prefix=self._config.get_partition_prefix(partition),
        ):
            for obj in page.get("Contents", []):
                if "Key" not in obj or "Size" not in obj:
                    continue

                remote_files.add(
                    FileKey(
                        path=(
                            # normalize the path to how it will appear in the actual
                            # filesystem (i.e., replace the partition prefix with /)
                            Path("/")
                            / Path(obj["Key"]).relative_to(
                                self._config.get_partition_prefix(partition)
                            )
                        ),
                        size=obj["Size"],
                    )
                )

        return remote_files

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, max=60),
        retry=(
            retry_if_exception_type((S3UploadFailedError, BotoCoreError, ClientError))
            & _retry_unless_partition_removed()
        ),
        before_sleep=before_sleep_log(_logger, logging.WARNING),
        reraise=True,
    )
    def upload_partition_file(
        self: "S3Filesystem",
        partition: Partition,
        filesystem: TskFilesystem,
        file_key: FileKey,
        callback: UploadProgress | None = None,
    ) -> None:
        partition.check_removed()

        s3_key: str = self._config.get_partition_file_key(
            partition,
            file_key.path,
        )

        with TskReader(
            filesystem.open(file_key.path),
            cancel_event=partition.removed,
        ) as reader:
            self._client.upload_fileobj(
                reader,  # type: ignore
                Bucket=self._config.bucket,
                Key=s3_key,
                Callback=callback,
            )

        # check that the file actually exists. head_object() will raise
        # ClientError for us if the file doesn't exist which @retry
        # will catch
        self._client.head_object(
            Bucket=self._config.bucket,
            Key=s3_key,
        )
