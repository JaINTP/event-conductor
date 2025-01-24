# event_conductor.py

import asyncio
import inspect
import logging
import re
import heapq
from collections import defaultdict
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Dict, List, Optional, Pattern, Set, Tuple, Union
)
from pydantic import ValidationError

from .base_event import BaseEvent


class SingletonMeta(type):
    """
    A threadsafe singleton metaclass that returns a single instance
    of any class using it.
    """
    _instances = {}

    def __call__(cls, *args, **kwargs):
        """
        Return the singleton instance if it exists.
        Otherwise, create and store a new one.
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
    """
    Wraps a callback with a priority so we can store them
    in a min-heap (using heapq). 
    By default, lower 'priority' means 'pops first'.
    We'll just invert or sort in reverse if we want 
    higher values to be considered "higher priority."
    """
    priority: int
    callback: EventCallback = field(compare=False)


class EventConductor(metaclass=SingletonMeta):
    """
    A multi-featured event bus that includes:
    - Priority subscriptions
    - Wildcard/regex subscriptions
    - Global 'before' and 'after' hooks (middleware)
    - Sync vs. async publishing
    - Event sourcing (storing and replaying events)
    - Correlation IDs (ensured by BaseEvent)
    """

    def __init__(
        self,
        include_traceback: bool = False,
        enable_priorities: bool = True,
        enable_wildcards: bool = True,
        sync_by_default: bool = False
    ):
        """
        :param include_traceback: If True, we'll use `logger.exception()` for 
                                  full tracebacks; otherwise, `logger.error()`.
        :param enable_priorities: If True, we handle subscriber priority via a heap.
        :param enable_wildcards:  If True, we allow regex-based subscriptions.
        :param sync_by_default:   If True, calls to publish() will block 
                                  instead of dispatching asynchronously.
        """
        self.logger = logging.getLogger(__name__)
        self.logger.propagate = True

        self.include_traceback = include_traceback
        self.enable_priorities = enable_priorities
        self.enable_wildcards  = enable_wildcards
        self.sync_by_default   = sync_by_default

        self._exact_subscribers: Dict[str, List[PrioritizedCallback]] = defaultdict(list)
        self._wildcard_subscribers: List[Tuple[Pattern, PrioritizedCallback]] = []

        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        self._before_hooks: List[Callable[[BaseEvent], Optional[BaseEvent]]] = []
        self._after_hooks: List[Callable[[BaseEvent, Optional[Exception]], None]] = []

        self._event_store: List[BaseEvent] = []

    def add_before_hook(self, hook: Callable[[BaseEvent], Optional[BaseEvent]]) -> None:
        """
        Add a function that runs before publishing events.
        The hook can modify the event (return the new event),
        or return None to keep it unchanged.
        """
        self._before_hooks.append(hook)

    def add_after_hook(self, hook: Callable[[BaseEvent, Optional[Exception]], None]) -> None:
        """
        Add a function that runs after publishing events, 
        receiving the event and the first raised exception (if any).
        """
        self._after_hooks.append(hook)

    async def subscribe(self, event_name: str, callback: EventCallback, priority: int = 0) -> None:
        """
        Subscribe a callback to an event name.
        If wildcard subscriptions are enabled and event_name 
        is recognized as a regex pattern, we store it separately.

        :param event_name: The event name or pattern (if enable_wildcards=True).
        :param callback:   The function or coroutine to invoke.
        :param priority:   Higher means higher priority (if enable_priorities).
        """
        pc = PrioritizedCallback(priority=priority, callback=callback)

        if self.enable_wildcards and self._is_regex_pattern(event_name):
            pattern = re.compile(event_name)
            self._wildcard_subscribers.append((pattern, pc))
        else:
            async with self._locks[event_name]:
                heapq.heappush(self._exact_subscribers[event_name], pc)

    async def unsubscribe(self, event_name: str, callback: EventCallback) -> None:
        """
        Unsubscribe a callback from an event name or pattern.
        We'll do a linear search to remove it. 
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
        """
        Publish an event to all subscribed callbacks.

        :param event: Either a dict (parsed into BaseEvent) or an BaseEvent instance.
        :param sync:  If None, uses `sync_by_default`. If True, runs sync/blocking 
                    in sequence; if False, schedules them concurrently via tasks.
        """
        if sync is None:
            sync = self.sync_by_default

        # Convert dict to BaseEvent if needed
        if isinstance(event, dict):
            try:
                event = BaseEvent.model_validate(event)
            except ValidationError as exc:
                self._log_error("Validation error constructing event object", exc)
                return

        # Ensure correlation/timestamp are set
        event.ensure_correlation_id()

        # Capture the original event_name for subscription lookup
        original_event_name = event.event_name

        # Event Sourcing: store the event
        self._event_store.append(event)

        # Run "before" hooks (they might alter the event_name)
        for hook in self._before_hooks:
            updated = hook(event)
            if updated is not None:
                event = updated

        # The event_name may have changed, but we still look up subscribers by the original
        mutated_event_name = event.event_name

        # Collect callbacks (exact + wildcard)
        callbacks = []

        # Exact matches
        async with self._locks[original_event_name]:
            if original_event_name in self._exact_subscribers:
                temp_list = []
                while self._exact_subscribers[original_event_name]:
                    temp_list.append(
                        heapq.heappop(self._exact_subscribers[original_event_name])
                    )
                # Put them back
                for pc in temp_list:
                    heapq.heappush(self._exact_subscribers[original_event_name], pc)
                callbacks.extend(temp_list)

        # Wildcard matches (match the final event name, or original if you prefer)
        for (pattern, pc) in self._wildcard_subscribers:
            if pattern.match(mutated_event_name):
                callbacks.append(pc)

        # Sort them by priority (highest first) if enabled
        if self.enable_priorities:
            callbacks.sort(reverse=True, key=lambda pc: pc.priority)

        # Dispatch
        exceptions = []

        if sync:
            # Process each callback in sequence (blocking) within this event loop
            for pc in callbacks:
                cb = pc.callback
                try:
                    if inspect.iscoroutinefunction(cb):
                        # Since we're already inside an async function, we can just await
                        await cb(event)
                    else:
                        cb(event)  # purely sync call
                except Exception as exc:
                    exceptions.append(exc)
                    self._log_error(f"Error in synchronous callback: {cb}", exc)
        else:
            # Launch all callbacks concurrently
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

        # Pass the first exception (if any) to after_hooks
        exc_obj = exceptions[0] if exceptions else None
        for hook in self._after_hooks:
            hook(event, exc_obj)

    async def replay_events(
        self,
        from_index: int = 0,
        to_index: Optional[int] = None,
        sync: bool = True
    ) -> None:
        """
        Replay previously published events from the event store.

        :param from_index: The start index within the event store.
        :param to_index:   The end index (exclusive). If None, goes to the end.
        :param sync:       Replay in sync mode (blocking) by default to keep order consistent.
        """
        if to_index is None:
            to_index = len(self._event_store)

        for ev in self._event_store[from_index:to_index]:
            await self.publish(ev, sync=sync)

    async def _invoke_async(self, callback: EventCallback, event: BaseEvent) -> None:
        """Helper to run async callbacks safely."""
        try:
            await callback(event)
        except Exception as exc:
            self._log_error(f"Error in async callback: {callback}", exc)
            raise

    async def _invoke_sync(self, callback: EventCallback, event: BaseEvent) -> None:
        """
        Helper to run sync callbacks in a thread executor
        so they don't block the event loop.
        """
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, callback, event)
        except Exception as exc:
            self._log_error(f"Error in sync callback: {callback}", exc)
            raise

    def _log_error(self, msg: str, exc: Exception) -> None:
        """
        Log an error with or without full traceback based on `include_traceback`.
        """
        if self.include_traceback:
            self.logger.exception(msg)
        else:
            self.logger.error(f"{msg}: {exc}")

    def _is_regex_pattern(self, event_name: str) -> bool:
        """
        Naive check: if it contains typical regex meta chars, treat it as a pattern.
        """
        special_chars = ['*', '.', '^', '$', '?', '+', '{', '}', '[', ']', '\\', '|']
        return any(ch in event_name for ch in special_chars)
