import logging
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError
from tenacity import (
    before_sleep_log,
    retry,
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


def get_remote_files(
    s3: S3Client,
    config: S3Config,
    partition: Partition,
) -> set[FileKey]:
    remote_files: set[FileKey] = set()

    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=config.bucket,
        Prefix=config.get_partition_prefix(partition),
    ):
        for obj in page.get("Contents", []):
            if "Key" not in obj or "Size" not in obj:
                continue

            remote_files.add(
                FileKey(
                    path=(
                        Path("/")
                        / Path(obj["Key"]).relative_to(
                            config.get_partition_prefix(partition)
                        )
                    ),
                    size=obj["Size"],
                )
            )

    return remote_files


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, max=60),
    retry=retry_if_exception_type((S3UploadFailedError, BotoCoreError, ClientError)),
    before_sleep=before_sleep_log(_logger, logging.WARNING),
    reraise=True,
)
def upload_file(
    s3: S3Client,
    partition: Partition,
    filesystem: TskFilesystem,
    target_file_key: FileKey,
    config: S3Config,
    callback: UploadProgress,
) -> None:
    with TskReader(filesystem.open(target_file_key.path)) as reader:
        s3.upload_fileobj(
            reader,
            Bucket=config.bucket,
            Key=config.get_partition_file_key(
                partition,
                target_file_key.path,
            ),
            Callback=callback,
        )


def get_s3_client(config: S3Config) -> S3Client:
    return boto3.Session(
        profile_name=config.profile,
    ).client("s3")
