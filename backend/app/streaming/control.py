import aiomqtt

from app.core.config import Settings
from app.streaming import messages as M


async def publish_command(settings: Settings, command: M.SimCommand) -> None:
    """Send a control command to the simulator over MQTT (short-lived connection)."""
    async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port, timeout=3) as client:
        await client.publish(M.CONTROL_TOPIC, command.model_dump_json(exclude_none=True), qos=1)
