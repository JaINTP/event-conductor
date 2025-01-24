# tests/test_EventConductor.py

import pytest
import asyncio
import re
from typing import List

from event_conductor import BaseEvent, EventConductor

@pytest.mark.asyncio
async def test_basic_subscribe_publish():
    """
    Test that a subscribed callback receives an event upon publish.
    """
    conductor = EventConductor()
    
    received_events: List[str] = []

    async def async_callback(event: BaseEvent):
        received_events.append(event.event_name)

    # Subscribe to a specific event name
    await conductor.subscribe("TEST.EVENT", async_callback)

    # Publish a dict that will parse into BaseEvent
    await conductor.publish({"event_name": "TEST.EVENT"})

    # Let the event loop settle
    await asyncio.sleep(0.1)

    assert "TEST.EVENT" in received_events, "Callback should have received the TEST.EVENT"


@pytest.mark.asyncio
async def test_unsubscribe():
    """
    Test that unsubscribing actually prevents further callbacks.
    """
    conductor = EventConductor()
    
    call_count = 0

    def sync_callback(event: BaseEvent):
        nonlocal call_count
        call_count += 1

    await conductor.subscribe("ANOTHER.EVENT", sync_callback)

    # Publish once, the callback should be called
    await conductor.publish({"event_name": "ANOTHER.EVENT"})
    await asyncio.sleep(0.1)
    assert call_count == 1

    # Unsubscribe
    await conductor.unsubscribe("ANOTHER.EVENT", sync_callback)

    # Publish again
    await conductor.publish({"event_name": "ANOTHER.EVENT"})
    await asyncio.sleep(0.1)
    assert call_count == 1, "Callback count should not have increased after unsubscribe."


@pytest.mark.asyncio
async def test_wildcard_subscriptions():
    """
    Test that a wildcard subscription (regex) receives events that match the pattern.
    """
    conductor = EventConductor(enable_wildcards=True)
    
    received = []

    def wildcard_callback(event: BaseEvent):
        received.append(event.event_name)

    # Subscribe to anything starting with 'USER.'
    await conductor.subscribe(r"^USER\..*", wildcard_callback)

    # Publish multiple
    await conductor.publish({"event_name": "USER.CREATED"})
    await conductor.publish({"event_name": "USER.DELETED"})
    await conductor.publish({"event_name": "NOTUSER.EVENT"})  # should not match

    await asyncio.sleep(0.1)

    assert "USER.CREATED" in received
    assert "USER.DELETED" in received
    assert "NOTUSER.EVENT" not in received


@pytest.mark.asyncio
async def test_priority_handling():
    """
    Test that higher priority callbacks are invoked first.
    We'll store the order of invocation and check it.
    """
    conductor = EventConductor(enable_priorities=True)

    invocation_order: List[str] = []

    async def high_priority(event: BaseEvent):
        invocation_order.append("HIGH")

    async def low_priority(event: BaseEvent):
        invocation_order.append("LOW")

    # High-priority subscriber
    await conductor.subscribe("PRIORITY.EVENT", high_priority, priority=10)
    # Low-priority subscriber
    await conductor.subscribe("PRIORITY.EVENT", low_priority, priority=1)

    # Publish
    await conductor.publish({"event_name": "PRIORITY.EVENT"})
    await asyncio.sleep(0.1)

    # We expect "HIGH" first
    assert invocation_order == ["HIGH", "LOW"], (
        "High priority should be invoked before low priority."
    )


@pytest.mark.asyncio
async def test_sync_vs_async_publish():
    """
    Test that we can publish in sync vs. async modes.
    We'll confirm that the synchronous call blocks while
    the asynchronous call doesn't.
    """
    conductor = EventConductor(sync_by_default=False)  # default is async

    # We'll track each event in separate lists
    async_received = []
    sync_received = []

    async def async_callback(event: BaseEvent):
        async_received.append(event.event_name)

    def sync_callback(event: BaseEvent):
        sync_received.append(event.event_name)

    await conductor.subscribe("SYNC.ASYNC", async_callback, priority=5)
    await conductor.subscribe("SYNC.ASYNC", sync_callback, priority=5)

    # Default (async) publish
    await conductor.publish({"event_name": "SYNC.ASYNC"})
    await asyncio.sleep(0.1)
    assert len(async_received) == 1
    assert len(sync_received) == 1

    # Force sync
    await conductor.publish({"event_name": "SYNC.ASYNC"}, sync=True)
    # Because it's sync, we don't need sleep here, it's already finished
    assert len(async_received) == 2
    assert len(sync_received) == 2


@pytest.mark.asyncio
async def test_middleware_hooks():
    """
    Test the before/after hooks. 
    Before hook modifies event; after hook captures any exception.
    """
    conductor = EventConductor()

    # We'll store a custom field in the event data from the before hook
    # (we'll do it by monkey-patching the model's dictionary for demonstration).
    def before_hook(event: BaseEvent):
        event.event_name = event.event_name + ".MODIFIED"
        return event  # Must return the modified event

    caught_exceptions = []

    def after_hook(event: BaseEvent, exc: Exception):
        if exc:
            caught_exceptions.append(exc)

    conductor.add_before_hook(before_hook)
    conductor.add_after_hook(after_hook)

    # We'll do a callback that raises an exception
    def raising_callback(event: BaseEvent):
        raise ValueError("Intentional error in callback.")

    await conductor.subscribe("HOOKTEST.EVENT", raising_callback)

    await conductor.publish({"event_name": "HOOKTEST.EVENT"})
    await asyncio.sleep(0.1)

    # The event name was modified in the before hook
    # so the actual callback receives HOOKTEST.EVENT.MODIFIED
    # BUT the subscription is for "HOOKTEST.EVENT", so it still matches 
    # (because we haven't changed the subscription key).
    #
    # To confirm the event got changed:
    assert conductor._event_store[-1].event_name == "HOOKTEST.EVENT.MODIFIED"

    # The after hook captures our ValueError
    assert len(caught_exceptions) == 1
    assert isinstance(caught_exceptions[0], ValueError)


@pytest.mark.asyncio
async def test_event_sourcing_replay():
    """
    Test that events are stored and can be replayed.
    """
    conductor = EventConductor()

    captured = []

    def replay_callback(event: BaseEvent):
        captured.append(event.event_name)

    await conductor.subscribe("REPLAY.EVENT", replay_callback)

    # Publish two events
    await conductor.publish({"event_name": "REPLAY.EVENT"})
    await conductor.publish({"event_name": "REPLAY.EVENT"})

    await asyncio.sleep(0.1)
    assert len(captured) == 2

    # Clear out so we can see what happens after replay
    captured.clear()

    # Replay them all in synchronous mode for demonstration
    await conductor.replay_events(sync=True)

    assert len(captured) == 2, "We should see the same two events replayed."


@pytest.mark.asyncio
async def test_correlation_and_timestamp():
    """
    Ensure that correlation_id and timestamp are assigned if missing,
    and remain if they're already present.
    """
    conductor = EventConductor()

    custom_cid = "my-custom-id"
    custom_timestamp = 12345678.0

    # 1) Publish without correlation or timestamp
    await conductor.publish({"event_name": "CORRELATION.TEST"})
    event1 = conductor._event_store[-1]
    assert event1.correlation_id is not None, "Should auto-generate correlation_id"
    assert event1.timestamp is not None, "Should auto-generate timestamp"

    # 2) Publish with both correlation and timestamp
    await conductor.publish({
        "event_name": "CORRELATION.TEST.2",
        "correlation_id": custom_cid,
        "timestamp": custom_timestamp
    })
    event2 = conductor._event_store[-1]
    assert event2.correlation_id == custom_cid
    assert event2.timestamp == custom_timestamp
