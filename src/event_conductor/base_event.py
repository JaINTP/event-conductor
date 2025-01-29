"""Event base module.

This module provides the base event class that all events in the system inherit from.
It handles correlation IDs and timestamps for event tracing.

Classes:
    BaseEvent: Base class for all events in the system.
"""

from pydantic import BaseModel
import time
import uuid


class BaseEvent(BaseModel):
    """Base Pydantic model for all events.
    
    This class serves as the foundation for all events in the system. It provides
    correlation ID and timestamp functionality for event tracing and debugging.

    Attributes:
        event_name (str): The name identifier for this event type
        correlation_id (str | None): Unique ID to trace related events, auto-generated if None
        timestamp (float | None): Unix timestamp when event occurred, auto-set if None
    """
    event_name: str
    correlation_id: str | None = None 
    timestamp: float | None = None

    def ensure_correlation_id(self) -> None:
        """Ensures the event has both a correlation ID and timestamp.
        
        If correlation_id is None, generates a new UUID.
        If timestamp is None, sets it to the current time.
        """
        if not self.correlation_id:
            self.correlation_id = str(uuid.uuid4())

        if not self.timestamp:
            self.timestamp = time.time()
