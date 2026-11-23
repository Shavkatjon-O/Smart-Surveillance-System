import pytest

from event_bus import MemoryBus, provision


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def bus(clock):
    memory = MemoryBus(clock=clock)
    await memory.connect()
    await provision(memory)
    return memory
