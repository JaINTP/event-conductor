# event_base.py

import time
import uuid
from pydantic import BaseModel

class BaseEvent(BaseModel):
    """
    Base Pydantic model for all events.
    Includes correlation_id and timestamp for tracing.
    """
    event_name: str
    correlation_id: str | None = None
    timestamp: float = None

    def ensure_correlation_id(self) -> None:
        """
        If this event doesn't have a correlation_id yet,
        generate a unique one.
        """
        if not self.correlation_id:
            self.correlation_id = str(uuid.uuid4())

        if not self.timestamp:
            self.timestamp = time.time()
