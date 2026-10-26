"""HTTP scheduling doubles; production limits remain active under a virtual clock."""
from insight_agent.materials.transport import ControlledTransport


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def fake_http_transport(client):
    clock = FakeClock()
    return ControlledTransport(client, clock=clock, sleep=clock.sleep)
