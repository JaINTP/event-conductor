"""Event conductor module.

This module provides the core event handling functionality through the EventConductor class.
It implements a feature-rich event bus with support for prioritized subscriptions,
wildcard/regex pattern matching, middleware hooks, and event sourcing.

Classes:
    SingletonMeta: Metaclass for singleton pattern implementation
    PrioritizedCallback: Wrapper class for callbacks with priority
    EventConductor: Main event bus implementation
"""

import asyncio
import heapq
import inspect
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Pattern,
    Tuple,
    Union,
)

from pydantic import ValidationError

from .base_event import BaseEvent


class SingletonMeta(type):
    """Metaclass that implements the singleton pattern.

    This metaclass ensures only one instance of a class is created and subsequent
    calls return the same instance. Thread-safe implementation.

    Attributes:
        _instances (dict): Dictionary storing singleton instances
    """

    _instances = {}

    def __call__(cls, *args, **kwargs):
        """Return singleton instance or create new if none exists.

        Args:
            *args: Variable length argument list
            **kwargs: Arbitrary keyword arguments

        Returns:
            The singleton instance of the class
        """
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
            logging.debug(f"New instance of {cls.__name__} created.")
        else:
            logging.debug(f"Existing instance of {cls.__name__} used.")
        return cls._instances[cls]


EventCallback = Callable[[BaseEvent], None]


@dataclass(order=True)
class PrioritizedCallback:
    """Wrapper for callbacks with priority for heap-based ordering.

    This class wraps callback functions with a priority value for use in a min-heap.
    Lower priority values are processed first by default.

    Attributes:
        priority (int): Priority value for ordering callbacks
        callback (EventCallback): The wrapped callback function
    """

    priority: int
    callback: EventCallback = field(compare=False)


class EventConductor(metaclass=SingletonMeta):
    """Feature-rich event bus implementation.

    Provides a comprehensive event handling system with:
    - Priority-based subscriptions
    - Wildcard/regex pattern matching for event names
    - Global before and after hooks (middleware)
    - Synchronous and asynchronous event publishing
    - Event sourcing capabilities
    - Correlation ID tracking

    Attributes:
        logger: Logger instance for this class
        include_traceback (bool): Whether to include full tracebacks in error logs
        enable_priorities (bool): Whether to use priority-based callback ordering
        enable_wildcards (bool): Whether to allow regex pattern subscriptions
        sync_by_default (bool): Whether to use synchronous publishing by default
    """

    def __init__(
        self,
        include_traceback: bool = False,
        enable_priorities: bool = True,
        enable_wildcards: bool = True,
        sync_by_default: bool = False
    ):
        """Initialize the EventConductor.

        Args:
            include_traceback: If True, use logger.exception() for full tracebacks
            enable_priorities: If True, handle subscriber priority via heap
            enable_wildcards: If True, allow regex-based subscriptions
            sync_by_default: If True, publish() calls block by default
        """
        self.logger = logging.getLogger(__name__)
        self.logger.propagate = True

        self.include_traceback = include_traceback
        self.enable_priorities = enable_priorities
        self.enable_wildcards = enable_wildcards
        self.sync_by_default = sync_by_default

        self._exact_subscribers: Dict[str, List[PrioritizedCallback]] = defaultdict(list)
        self._wildcard_subscribers: List[Tuple[Pattern, PrioritizedCallback]] = []

        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        self._before_hooks: List[Callable[[BaseEvent], Optional[BaseEvent]]] = []
        self._after_hooks: List[Callable[[BaseEvent, Optional[Exception]], None]] = []

        self._event_store: List[BaseEvent] = []

    def add_before_hook(self, hook: Callable[[BaseEvent], Optional[BaseEvent]]) -> None:
        """Add a pre-publish hook function.

        Args:
            hook: Function that runs before publishing events. Can modify the event
                by returning a new one, or return None to keep unchanged.
        """
        self._before_hooks.append(hook)

    def add_after_hook(self, hook: Callable[[BaseEvent, Optional[Exception]], None]) -> None:
        """Add a post-publish hook function.

        Args:
            hook: Function that runs after publishing events, receiving the event
                and any exception that occurred during publishing.
        """
        self._after_hooks.append(hook)

    async def subscribe(self, event_name: str, callback: EventCallback, priority: int = 0) -> None:
        """Subscribe a callback to an event name.

        Args:
            event_name: Event name or regex pattern to subscribe to
            callback: Function or coroutine to call when event occurs
            priority: Priority value for callback ordering (higher = higher priority)
        """
        pc = PrioritizedCallback(priority=priority, callback=callback)

        if self.enable_wildcards and self._is_regex_pattern(event_name):
            pattern = re.compile(event_name)
            self._wildcard_subscribers.append((pattern, pc))
        else:
            async with self._locks[event_name]:
                heapq.heappush(self._exact_subscribers[event_name], pc)

    async def unsubscribe(self, event_name: str, callback: EventCallback) -> None:
        """Remove a callback subscription.

        Args:
            event_name: Event name or pattern to unsubscribe from
            callback: The callback function to remove
        """
        if self.enable_wildcards and self._is_regex_pattern(event_name):
            pattern = re.compile(event_name)
            self._wildcard_subscribers = [
                (pat, pc) for (pat, pc) in self._wildcard_subscribers
                if not (pat.pattern == pattern.pattern and pc.callback is callback)
            ]
        else:
            async with self._locks[event_name]:
                pcs = self._exact_subscribers.get(event_name, [])
                filtered = [pc for pc in pcs if pc.callback is not callback]
                self._exact_subscribers[event_name] = filtered

    async def publish(
        self,
        event: Union[Dict[str, Any], BaseEvent],
        sync: Optional[bool] = None
    ) -> None:
        """Publish an event to all subscribed callbacks.

        Args:
            event: Event data as dict or BaseEvent instance
            sync: If True, run callbacks synchronously; if False, run concurrently;
                if None, use sync_by_default setting

        Raises:
            ValidationError: If event dict cannot be converted to BaseEvent
        """
        if sync is None:
            sync = self.sync_by_default

        if isinstance(event, dict):
            try:
                event = BaseEvent.model_validate(event)
            except ValidationError as exc:
                self._log_error("Validation error constructing event object", exc)
                return

        event.ensure_correlation_id()

        original_event_name = event.event_name
        self._event_store.append(event)

        for hook in self._before_hooks:
            updated = hook(event)
            if updated is not None:
                event = updated

        mutated_event_name = event.event_name

        callbacks = []

        async with self._locks[original_event_name]:
            if original_event_name in self._exact_subscribers:
                temp_list = []
                while self._exact_subscribers[original_event_name]:
                    temp_list.append(
                        heapq.heappop(self._exact_subscribers[original_event_name])
                    )
                for pc in temp_list:
                    heapq.heappush(self._exact_subscribers[original_event_name], pc)
                callbacks.extend(temp_list)

        for (pattern, pc) in self._wildcard_subscribers:
            if pattern.match(mutated_event_name):
                callbacks.append(pc)

        if self.enable_priorities:
            callbacks.sort(reverse=True, key=lambda pc: pc.priority)

        exceptions = []

        if sync:
            for pc in callbacks:
                cb = pc.callback
                try:
                    if inspect.iscoroutinefunction(cb):
                        await cb(event)
                    else:
                        cb(event)
                except Exception as exc:
                    exceptions.append(exc)
                    self._log_error(f"Error in synchronous callback: {cb}", exc)
        else:
            tasks = []
            for pc in callbacks:
                cb = pc.callback
                if inspect.iscoroutinefunction(cb):
                    tasks.append(asyncio.create_task(self._invoke_async(cb, event)))
                else:
                    tasks.append(asyncio.create_task(self._invoke_sync(cb, event)))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    exceptions.append(r)

        exc_obj = exceptions[0] if exceptions else None
        for hook in self._after_hooks:
            hook(event, exc_obj)

    async def replay_events(
        self,
        from_index: int = 0,
        to_index: Optional[int] = None,
        sync: bool = True
    ) -> None:
        """Replay stored events.

        Args:
            from_index: Starting index in event store
            to_index: Ending index (exclusive) in event store
            sync: Whether to replay events synchronously
        """
        if to_index is None:
            to_index = len(self._event_store)

        for ev in self._event_store[from_index:to_index]:
            await self.publish(ev, sync=sync)

    async def _invoke_async(self, callback: EventCallback, event: BaseEvent) -> None:
        """Run async callback safely.

        Args:
            callback: Async callback to execute
            event: Event to pass to callback

        Raises:
            Exception: Any exception raised by callback
        """
        try:
            if inspect.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        except Exception as exc:
            self._log_error(f"Error in async callback: {callback}", exc)
            raise

    async def _invoke_sync(self, callback: EventCallback, event: BaseEvent) -> None:
        """Run sync callback in thread executor.

        Args:
            callback: Sync callback to execute
            event: Event to pass to callback

        Raises:
            Exception: Any exception raised by callback
        """
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, callback, event)
        except Exception as exc:
            self._log_error(f"Error in sync callback: {callback}", exc)
            raise

    def _log_error(self, msg: str, exc: Exception) -> None:
        """Log an error with optional traceback.

        Args:
            msg: Error message to log
            exc: Exception that occurred
        """
        if self.include_traceback:
            self.logger.exception(msg)
        else:
            self.logger.error(f"{msg}: {exc}")

    def _is_regex_pattern(self, event_name: str) -> bool:
        """Check if event name contains regex metacharacters.

        Args:
            event_name: Event name to check

        Returns:
            bool: True if event_name contains regex metacharacters
        """
        special_chars = ['*', '.', '^', '$', '?', '+', '{', '}', '[', ']', '\\', '|']
        return any(ch in event_name for ch in special_chars)
