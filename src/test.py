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