"""Event Conductor - A simple event handling system.

This module provides a simple event handling system that allows for the creation
and management of events in a Python application.

Classes:
    BaseEvent: Base class for all events.
    EventConductor: Main class for managing and dispatching events.
"""

from event_conductor.base_event import BaseEvent
from event_conductor.event_conductor import EventConductor

__all__ = ['BaseEvent', 'EventConductor']