# Event Conductor

[![GitHub - Jai Brown](https://img.shields.io/badge/GitHub-jaintp-181717.svg?style=flat&logo=github)](https://github.com/jaintp)
![Coverage](assets/coverage.svg)

---

**Event Conductor** is a powerful Python library designed for event-driven architectures. It provides advanced capabilities such as priority subscriptions, middleware hooks, wildcard matching, and event sourcing, enabling seamless event management across your applications.

---

## Features

- **Priority Subscriptions**: Define the execution order of callbacks with numeric priorities.
- **Wildcard/Regex Matching**: Subscribe to multiple events using patterns like `USER.*`.
- **Sync and Async Modes**: Publish events in a blocking or non-blocking manner.
- **Middleware Hooks**: Add `before` and `after` hooks to transform or monitor events.
- **Traceability**: Includes correlation IDs and timestamps for enhanced debugging and tracking.
- **Event Sourcing**: Record and replay events for debugging or state reconstruction.

---

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/jaintp/event-conductor.git
   cd event-conductor
   ```

2. Install dependencies with `uv`:

   ```bash
   uv install
   ```

   Alternatively, use `pip`:

   ```bash
   pip install -r requirements.txt
   ```

3. Install testing dependencies:

   ```bash
   pip install pytest pytest-asyncio
   ```

---

## Usage

### Basic Setup

```python
from event_conductor import EventConductor

async def main():
    # Create an instance of EventConductor
    conductor = EventConductor()

    async def on_user_created(event):
        print(f"User created: {event}")

    # Subscribe to an event
    await conductor.subscribe("USER.CREATED", on_user_created)

    # Publish an event
    await conductor.publish({"event_name": "USER.CREATED", "user_id": 42})

if __name__ == '__main__':
    asyncio.run(main())
```
Or...
```python
import asyncio
from event_conductor import BaseEvent, EventConductor

class UserCreated(BaseEvent):
    event_name: str = 'USER.CREATED'
    user_id: int

async def main():
    # Create an instance of EventConductor
    conductor = EventConductor()

    async def on_user_created(event):
        print(f"User created: {event}")

    # Create event instance
    event = UserCreated(user_id=42)

    # Subscribe to an event
    await conductor.subscribe("USER.CREATED", on_user_created)

    # Publish an event
    await conductor.publish(event)

if __name__ == '__main__':
    asyncio.run(main())
```

---

### Advanced Features

#### Wildcard Matching

```python
async def order_handler(event):
    print(f"Order event: {event.event_name}")

# Subscribe to all events matching the pattern
await conductor.subscribe(r"^ORDER\\..*", order_handler)

# Publish events
await conductor.publish({"event_name": "ORDER.PLACED"})
await conductor.publish({"event_name": "ORDER.CANCELLED"})
```

#### Priority Subscriptions

```python
async def high_priority(event):
    print("High priority executed")

async def low_priority(event):
    print("Low priority executed")

# Subscribe with priorities
await conductor.subscribe("TEST.EVENT", high_priority, priority=10)
await conductor.subscribe("TEST.EVENT", low_priority, priority=1)

await conductor.publish({"event_name": "TEST.EVENT"})
```

#### Middleware Hooks

```python
def before_hook(event):
    event.event_name = event.event_name.upper()
    return event

def after_hook(event, exc):
    if exc:
        print(f"Error: {exc}")
    else:
        print(f"Successfully handled {event.event_name}")

conductor.add_before_hook(before_hook)
conductor.add_after_hook(after_hook)

await conductor.publish({"event_name": "middleware.test"})
```

#### Event Sourcing and Replay

```python
# Publish events
await conductor.publish({"event_name": "REPLAY.ONE"})
await conductor.publish({"event_name": "REPLAY.TWO"})

# Replay stored events
await conductor.replay_events()
```

---

## Running Tests

Run tests using `pytest`:

```bash
pytest
```

For coverage:

```bash
pytest --cov=event_conductor --cov-report=term-missing
```

---

## Contributing

1. Fork the repository.
2. Create a feature branch:

   ```bash
   git checkout -b feature/your-feature
   ```

3. Commit your changes:

   ```bash
   git commit -m "Add your feature description"
   ```

4. Push your branch:

   ```bash
   git push origin feature/your-feature
   ```

5. Create a pull request.

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.

## Author

Developed by Jai Brown (JaINTP) (<jaintp.dev@gmail.com>)

--- 

Let me know if there are any specific areas you'd like to refine further!