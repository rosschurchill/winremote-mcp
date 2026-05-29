"""Task manager with concurrency control, error resilience, and cancellation."""

from __future__ import annotations

import functools
import logging
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from winremote.tool_registry import TOOL_REGISTRY

logger = logging.getLogger("winremote.taskmanager")

# Thread-local storage so subprocess-based tools can access the current task's cancel event
# without needing it passed as a parameter.
_thread_locals: threading.local = threading.local()


def get_current_cancel_event() -> threading.Event | None:
    """Return the cancel event for the tool currently executing on this thread, if any."""
    return getattr(_thread_locals, "cancel_event", None)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolCategory(str, Enum):
    """Tool concurrency categories."""

    # Desktop interaction — mouse/keyboard/screen capture. Must be exclusive.
    DESKTOP = "desktop"
    # File system operations — can run concurrently with each other.
    FILE = "file"
    # System queries — read-only, fully concurrent.
    QUERY = "query"
    # Shell commands — semi-concurrent (limited pool).
    SHELL = "shell"
    # Network tools — fully concurrent.
    NETWORK = "network"


# Derive TOOL_CATEGORIES from the single-source TOOL_REGISTRY
TOOL_CATEGORIES: dict[str, ToolCategory] = {
    name: ToolCategory(meta.category)
    for name, meta in TOOL_REGISTRY.items()
    if meta.category in {cat.value for cat in ToolCategory}
}

# Max concurrent tasks per category
CATEGORY_LIMITS: dict[ToolCategory, int] = {
    ToolCategory.DESKTOP: 1,  # Exclusive — only one at a time
    ToolCategory.FILE: 5,
    ToolCategory.QUERY: 10,
    ToolCategory.SHELL: 3,
    ToolCategory.NETWORK: 5,
}

# Max seconds to wait for a category semaphore before returning a timeout error
CATEGORY_ACQUIRE_TIMEOUTS: dict[ToolCategory, int] = {
    ToolCategory.DESKTOP: 30,
    ToolCategory.FILE: 30,
    ToolCategory.QUERY: 15,
    ToolCategory.SHELL: 60,
    ToolCategory.NETWORK: 15,
}


@dataclass
class TaskInfo:
    """Tracks a running task."""

    task_id: str
    tool_name: str
    category: ToolCategory
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    result: Any = None
    error: str | None = None
    _cancel_event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def duration(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.completed_at or time.time()
        return round(end - self.started_at, 2)

    def cancel(self) -> bool:
        """Request cancellation. Thread-safe — sets cancel event and transitions to CANCELLED atomically."""
        with self._lock:
            if self.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                self._cancel_event.set()
                self.status = TaskStatus.CANCELLED
                self.completed_at = time.time()
                return True
            return False

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "category": self.category.value,
            "status": self.status.value,
            "duration": self.duration,
            "error": self.error,
        }


class TaskManager:
    """Manages task execution with concurrency control and cancellation."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        # Thread-based semaphores for concurrency control
        self._thread_semaphores: dict[ToolCategory, threading.Semaphore] = {
            cat: threading.Semaphore(limit) for cat, limit in CATEGORY_LIMITS.items()
        }
        self._lock = threading.Lock()
        # Keep max N completed tasks in history
        self._max_history = 100

    def create_task(self, tool_name: str) -> TaskInfo:
        """Register a new task."""
        category = TOOL_CATEGORIES.get(tool_name, ToolCategory.QUERY)
        task = TaskInfo(
            task_id=uuid.uuid4().hex[:12],
            tool_name=tool_name,
            category=category,
        )
        with self._lock:
            self._tasks[task.task_id] = task
            self._cleanup_old()
        return task

    def cancel_task(self, task_id: str) -> dict:
        """Cancel a task by ID."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return {"error": f"Task {task_id} not found"}
            if task.cancel():
                return {"status": "cancelled", "task_id": task_id, "tool_name": task.tool_name}
            return {"error": f"Task {task_id} is already {task.status.value}"}

    def list_tasks(self, status: str | None = None) -> list[dict]:
        """List tasks, optionally filtered by status."""
        with self._lock:
            tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        # Most recent first
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tasks[:50]]

    def get_task(self, task_id: str) -> dict | None:
        """Get task info by ID."""
        with self._lock:
            task = self._tasks.get(task_id)
        return task.to_dict() if task else None

    def _cleanup_old(self) -> None:
        """Remove old completed tasks beyond max_history."""
        completed = [
            t
            for t in self._tasks.values()
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
        ]
        if len(completed) > self._max_history:
            completed.sort(key=lambda t: t.created_at)
            for t in completed[: len(completed) - self._max_history]:
                self._tasks.pop(t.task_id, None)

    def wrap_sync_tool(self, tool_name: str, func: Callable) -> Callable:
        """Wrap a synchronous tool function with error handling + concurrency control."""
        category = TOOL_CATEGORIES.get(tool_name, ToolCategory.QUERY)
        sem = self._thread_semaphores.get(category)
        acquire_timeout = CATEGORY_ACQUIRE_TIMEOUTS.get(category, 30)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            task = self.create_task(tool_name)

            # Try to acquire semaphore
            if sem and not sem.acquire(timeout=acquire_timeout):
                with task._lock:
                    if task.status != TaskStatus.CANCELLED:
                        task.status = TaskStatus.FAILED
                        task.error = f"Timeout waiting for {category.value} lock (another {category.value} task is running)"
                        task.completed_at = time.time()
                return f"[task:{task.task_id}] Error: {task.error}"

            try:
                if task.is_cancelled:
                    return f"[task:{task.task_id}] Cancelled: awaiting execution (semaphore not yet acquired)"

                with task._lock:
                    if task.status != TaskStatus.CANCELLED:
                        task.status = TaskStatus.RUNNING
                        task.started_at = time.time()

                if task.is_cancelled:
                    return f"[task:{task.task_id}] Cancelled: before execution started"

                _thread_locals.cancel_event = task._cancel_event
                try:
                    result = func(*args, **kwargs)
                finally:
                    _thread_locals.cancel_event = None

                if task.is_cancelled:
                    return f"[task:{task.task_id}] Cancelled: during execution"

                with task._lock:
                    if task.status != TaskStatus.CANCELLED:
                        task.status = TaskStatus.COMPLETED
                        task.completed_at = time.time()

                # Prepend task_id to text results
                if isinstance(result, str):
                    return f"[task:{task.task_id}] {result}"
                if isinstance(result, list):
                    # For multi-content results (images + text), prepend task_id to first text
                    injected = False
                    for item in result:
                        if hasattr(item, "text") and not injected:
                            item.text = f"[task:{task.task_id}] {item.text}"
                            injected = True
                    if not injected:
                        # Add task_id as first element
                        from mcp.types import TextContent

                        result.insert(0, TextContent(type="text", text=f"[task:{task.task_id}]"))
                    return result
                return result

            except Exception as e:
                with task._lock:
                    if task.status != TaskStatus.CANCELLED:
                        task.status = TaskStatus.FAILED
                        task.error = str(e)
                        task.completed_at = time.time()
                logger.error("Tool %s failed: %s\n%s", tool_name, e, traceback.format_exc())
                return f"[task:{task.task_id}] Error in {tool_name}: {e}"

            finally:
                if sem:
                    sem.release()

        return wrapper


# Global task manager instance
manager = TaskManager()
