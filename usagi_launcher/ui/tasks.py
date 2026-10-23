from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..errors import Cancelled

log = logging.getLogger("usagi")


class _Signals(QObject):
    finished = Signal(object, bool, object)  # task, ok, valor/erro
    progress = Signal(object, object)  # task, (fase, feito, total)


class Task(QRunnable):
    def __init__(self, fn: Callable[["Task"], Any], ok=None, err=None, progress=None):
        super().__init__()
        self.setAutoDelete(False)  # o TaskRunner mantém a referência até o fim
        self.fn = fn
        self.ok, self.err, self.on_progress = ok, err, progress
        self.cancel_event = threading.Event()
        self.signals = _Signals()

    def report(self, phase: str, done: int, total: int) -> None:
        self.signals.progress.emit(self, (phase, done, total))

    def run(self) -> None:
        try:
            value = self.fn(self)
        except Cancelled:
            self.signals.finished.emit(self, False, Cancelled("Cancelado"))
        except Exception as e:  # noqa: BLE001 - qualquer falha vira mensagem na UI
            log.exception("Tarefa falhou")
            self.signals.finished.emit(self, False, e)
        else:
            self.signals.finished.emit(self, True, value)


class TaskRunner(QObject):
    """Executa funções em threads e devolve o resultado na thread da interface."""

    def __init__(self, max_threads: int = 6):
        super().__init__()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max_threads)
        self._live: set[Task] = set()

    def run(self, fn, ok=None, err=None, progress=None) -> Task:
        task = Task(fn, ok, err, progress)
        task.signals.finished.connect(self._finished)
        task.signals.progress.connect(self._progress)
        self._live.add(task)
        self.pool.start(task)
        return task

    def _finished(self, task: Task, ok: bool, value: Any) -> None:
        try:
            if ok and task.ok:
                task.ok(value)
            elif not ok and task.err:
                task.err(value)
        finally:
            self._live.discard(task)

    def _progress(self, task: Task, payload: Any) -> None:
        if task.on_progress:
            task.on_progress(*payload)

    def busy(self) -> bool:
        return bool(self._live)

    def wait(self, ms: int = 3000) -> None:
        self.pool.waitForDone(ms)
