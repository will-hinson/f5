import io
from threading import Lock
from typing import Annotated, override, TYPE_CHECKING

import pytsk3

if TYPE_CHECKING:
    from _typeshed import MaybeNone, WriteableBuffer

_tsk_lock: Annotated[
    Lock, "Lock for pytsk3 operations since pytsk3 is not threadsafe"
] = Lock()


class TskReader(io.RawIOBase):
    _file: pytsk3.File
    _size: int
    _position: int

    def __init__(self: "TskReader", file: pytsk3.File) -> None:
        self._file = file
        self._size = file.info.meta.size
        self._position = 0

    @override
    def readable(self: "TskReader") -> bool:
        return True

    @override
    def seekable(self: "TskReader") -> bool:
        return True

    def seek(self: "TskReader", offset: int, whence: int = io.SEEK_SET) -> int:
        match whence:
            case io.SEEK_SET:
                self._position = 0 + offset
            case io.SEEK_CUR:
                self._position += offset
            case io.SEEK_END:
                self._position = self._size
            case _:
                raise ValueError(f"Unknown whence value {whence}")

        return self._position

    @override
    def readinto(self, buffer: WriteableBuffer, /) -> int | MaybeNone:
        # get the target number of bytes for this read. it's either the
        # remaining bytes available of length of the target buffer,
        # whichever's smaller
        byte_count: int = min(len(buffer), self._size - self._position)
        if byte_count <= 0:
            return 0

        # read the bytes with a mutex since pytsk3 isn't threadsafe
        data: bytes
        with _tsk_lock:
            data = self._file.read_random(self._position, byte_count)

        # put the result in the buffer and seek our position ahead
        buffer[: len(data)] = data
        self._position += len(data)

        return len(data)
