# tests/conftest.py
# Custom pytest configuration file.

import os
import pytest
from event_conductor import EventConductor

@pytest.fixture(autouse=True)
def reset_singleton():
    """
    Runs before each test to ensure a fresh EventBus singleton.
    """
    EventConductor._instances.clear()
    yield
    EventConductor._instances.clear()



def pytest_sessionfinish(session, exitstatus):
    """
    Hook to execute a command after the pytest session finishes.
    
    :param session: The pytest session object.
    :param exitstatus: The exit status of the pytest session.
    """
    os.system("uv run coverage-badge -f -o assets/coverage.svg")