from threading import Lock

from rich.progress import Progress, TaskID


class UploadProgress:
    """boto3 Callback that feeds a per-file task and an overall task."""

    def __init__(self, progress: Progress, overall: TaskID, task: TaskID) -> None:
        self._progress = progress
        self._overall = overall
        self._task = task
        self._sent = 0
        self._lock = Lock()

    def __call__(self, n: int) -> None:
        with self._lock:
            self._sent += n

        try:
            self._progress.advance(self._task, n)
            self._progress.advance(self._overall, n)
        except KeyError:
            ...

    def reset(self) -> None:
        """Undo this file's contribution before a retry."""
        with self._lock:
            sent, self._sent = self._sent, 0

        try:
            self._progress.advance(self._overall, -sent)
            self._progress.reset(self._task)
        except KeyError:
            ...
