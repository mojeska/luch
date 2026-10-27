"""Answer a radio's Bluetooth PIN request on Linux.

MeshCore radios want a 6-digit PIN the first time a computer pairs with them.
macOS asks for it in a system dialog; on Linux, BlueZ asks whichever "agent"
program is registered, and often there is none. `pin_agent(pin)` registers a
short-lived agent that answers with the given PIN while LUCH connects, then
removes itself. Once the radio is paired the PIN isn't asked for again.
"""
# No `from __future__ import annotations` here: dbus_fast reads the D-Bus type
# strings ('o', 'u', 's', 'q') from the method annotations.

import contextlib
import logging
import sys

log = logging.getLogger("luch")

AGENT_PATH = "/io/github/mojeska/luch/agent"
LINUX = sys.platform.startswith("linux")


def valid_pin(text: str) -> str:
    """argparse type for --pin: exactly six digits."""
    import argparse
    if len(text) != 6 or not text.isdigit():
        raise argparse.ArgumentTypeError("the PIN is 6 digits, as shown on the radio's screen")
    return text


def make_agent(pin: str):
    """The org.bluez.Agent1 object. Built in a function so dbus_fast is only imported on Linux."""
    from dbus_fast import DBusError
    from dbus_fast.service import ServiceInterface, method

    class PinAgent(ServiceInterface):
        def __init__(self):
            super().__init__("org.bluez.Agent1")
            self.pin = pin
            self.asked = 0  # how many times BlueZ asked, for the log and tests

        @method()
        def Release(self):
            pass

        @method()
        def RequestPinCode(self, device: "o") -> "s":
            self.asked += 1
            log.info("PIN requested for %s", device)
            return self.pin

        @method()
        def RequestPasskey(self, device: "o") -> "u":
            self.asked += 1
            log.info("passkey requested for %s", device)
            return int(self.pin)

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):
            pass

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            pass

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):
            self.asked += 1
            if passkey != int(self.pin):
                raise DBusError("org.bluez.Error.Rejected", "passkey doesn't match --pin")

        @method()
        def RequestAuthorization(self, device: "o"):
            pass

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            pass

        @method()
        def Cancel(self):
            log.info("pairing cancelled by BlueZ")

    return PinAgent()


async def _agent_manager(bus, member: str, signature: str, body: list) -> None:
    from dbus_fast import Message, MessageType
    reply = await bus.call(Message(destination="org.bluez", path="/org/bluez",
                                   interface="org.bluez.AgentManager1", member=member,
                                   signature=signature, body=body))
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(f"BlueZ {member}: {reply.error_name} {reply.body}")


@contextlib.asynccontextmanager
async def pin_agent(pin: str | None):
    """While inside, BlueZ gets `pin` when the radio asks for one. Does nothing off Linux or without a PIN."""
    if not (pin and LINUX):
        yield None
        return
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    agent = make_agent(pin)
    bus.export(AGENT_PATH, agent)
    try:
        # KeyboardOnly: we type the passkey the radio shows. The default-agent
        # request matters because pairing is started on bleak's own D-Bus
        # connection, and BlueZ falls back to the default agent for it.
        await _agent_manager(bus, "RegisterAgent", "os", [AGENT_PATH, "KeyboardOnly"])
        await _agent_manager(bus, "RequestDefaultAgent", "o", [AGENT_PATH])
        yield agent
    finally:
        with contextlib.suppress(Exception):
            await _agent_manager(bus, "UnregisterAgent", "o", [AGENT_PATH])
        bus.disconnect()
