"""Milestone 6: pygame view. Rendered off-screen (no window) with SDL's dummy driver."""

import os
import subprocess
import sys

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
pygame = pytest.importorskip("pygame")

from smt_sim import Simulator  # noqa: E402
from viz.pygame_view import HEIGHT, WIDTH, LineView, advance  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def pygame_session():
    pygame.init()
    yield
    pygame.quit()


def snapshot(sim: Simulator) -> tuple:
    return (sim.tick, len(sim.event_log), sim.boards_completed,
            [(st.state, st.part_count) for st in sim.stations], [len(b) for b in sim.buffers])


def test_view_draws_at_many_moments_without_changing_the_sim():
    sim = Simulator(seed=1)
    view = LineView(sim)
    surface = pygame.Surface((WIDTH, HEIGHT))
    for _ in range(12):            # 0 h .. 5.5 h, includes changeovers and stops
        before = snapshot(sim)
        view._metrics_at_wall = -1e9   # force a metrics refresh every frame
        view.draw(surface, speed=60, paused=False)
        view.draw(surface, speed=60, paused=True)
        assert snapshot(sim) == before   # drawing is read-only
        sim.run(1800)


def test_advance_keeps_exact_speed_with_fractional_ticks():
    sim = Simulator(seed=1)
    debt = 0.0
    for _ in range(300):   # 10 real seconds at 30 fps, speed 1x -> 1/60 tick per frame
        debt = advance(sim, speed=1, frame_s=1 / 30, tick_debt=debt)
    assert sim.time_s == pytest.approx(10.0, abs=sim.dt_s)

    start = sim.time_s
    for _ in range(30):    # 1 real second at 600x
        debt = advance(sim, speed=600, frame_s=1 / 30, tick_debt=debt)
    assert sim.time_s - start == pytest.approx(600.0, abs=sim.dt_s)


def test_simulation_core_never_imports_pygame():
    code = ("import sys; from smt_sim import Simulator; from smt_sim import metrics, export; "
            "Simulator().run(60); print('pygame' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert out.stdout.strip() == "False"
