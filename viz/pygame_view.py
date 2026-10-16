"""Live pygame view of the SMT line.

Usage:
    python viz/pygame_view.py [--config path.yaml] [--seed 42] [--speed 60]
    python viz/pygame_view.py --scenario nozzle_fault

Controls:
    Up / Down (or + / -)   faster / slower simulation
    Space                  pause / resume
    Esc or Q               quit

Design: this file is the only place that imports pygame. `LineView` only
*reads* the simulator (tags, buffers, event log, metrics) and draws it; it
never changes simulation state. The `run()` loop is the controller: it decides
how many ticks to advance per frame to hit the chosen speed, then asks the
view to draw. The simulation core still runs headless without any of this.
"""

import argparse
import sys
import time
from pathlib import Path

import pygame

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # allow running without install

from smt_sim import Simulator  # noqa: E402
from smt_sim.events import StationState  # noqa: E402
from smt_sim.metrics import line_oee, quality_report, station_oee  # noqa: E402
from smt_sim.scenarios import load_scenario  # noqa: E402

WIDTH, HEIGHT = 1280, 720
FPS = 30
SPEEDS = [1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600]   # sim seconds per real second
MAX_TICKS_PER_FRAME = 20_000   # if the PC can't keep up, run slower instead of freezing
METRICS_REFRESH_S = 0.5        # recomputing OEE scans the whole event log, so not every frame

STATE_COLORS = {
    StationState.RUNNING: (46, 160, 67),
    StationState.STARVED: (219, 180, 40),
    StationState.BLOCKED: (232, 120, 30),
    StationState.DOWN: (200, 45, 45),
    StationState.STARVED_MATERIAL: (140, 70, 170),
    StationState.SETUP: (50, 110, 200),
    StationState.PM: (120, 120, 120),
}
BACKGROUND = (24, 26, 30)
PANEL = (38, 41, 47)
TEXT = (225, 228, 232)
DIM_TEXT = (150, 155, 162)
SLOT_EMPTY = (60, 64, 72)
SLOT_FULL = (180, 190, 205)

STATION_W, STATION_H = 160, 190
BUFFER_W = 40
LINE_Y = 150


class LineView:
    """Draws the current state of a Simulator onto a pygame surface. Read-only."""

    def __init__(self, sim: Simulator) -> None:
        self.sim = sim
        self.font = pygame.font.SysFont("consolas", 15)
        self.small = pygame.font.SysFont("consolas", 13)
        self.big = pygame.font.SysFont("consolas", 22, bold=True)
        self._metrics_at_wall = -1e9
        self._line_oee = None
        self._station_oee = {}
        self._quality = None

    # ---- public -------------------------------------------------------------

    def draw(self, surface: pygame.Surface, speed: float, paused: bool) -> None:
        self._refresh_metrics()
        surface.fill(BACKGROUND)
        self._draw_header(surface, speed, paused)
        self._draw_line(surface)
        self._draw_recent_events(surface)
        self._draw_legend(surface)

    # ---- pieces ---------------------------------------------------------------

    def _refresh_metrics(self) -> None:
        now = time.perf_counter()
        if now - self._metrics_at_wall < METRICS_REFRESH_S or self.sim.time_s == 0:
            return
        self._metrics_at_wall = now
        self._line_oee = line_oee(self.sim)
        self._station_oee = station_oee(self.sim)
        self._quality = quality_report(self.sim)

    def _draw_header(self, surface: pygame.Surface, speed: float, paused: bool) -> None:
        t = self.sim.time_s
        clock = f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"
        speed_txt = "PAUSED" if paused else f"{speed:g}x"
        self._text(surface, f"SMT line   sim time {clock}   speed {speed_txt}", (20, 15), self.big)

        oee = f"{self._line_oee.oee:.1%}" if self._line_oee else "-"
        fpy = f"{self._quality.first_pass_yield:.1%}" if self._quality and self._quality.boards_finished else "-"
        rejects = (self._quality.rework + self._quality.scrap) if self._quality else 0
        self._text(surface, f"Line OEE {oee}   first-pass yield {fpy}   good boards out "
                            f"{self.sim.boards_completed}   rejected {rejects}", (20, 50), self.font)
        upcoming = "finished" if self.sim.source.is_empty() else self.sim.source.peek().product
        self._text(surface, f"Next board to release: {upcoming}", (20, 72), self.font, DIM_TEXT)

    def _draw_line(self, surface: pygame.Surface) -> None:
        tags = self.sim.get_tags()
        x = 20
        for i, st in enumerate(self.sim.stations):
            self._draw_station(surface, st, tags[st.name], pygame.Rect(x, LINE_Y, STATION_W, STATION_H))
            x += STATION_W
            if i < len(self.sim.buffers):
                buf = self.sim.buffers[i]
                self._draw_buffer(surface, len(buf), buf.capacity, x + 4, LINE_Y + STATION_H // 2)
                x += BUFFER_W

    def _draw_station(self, surface: pygame.Surface, st, tags: dict, rect: pygame.Rect) -> None:
        color = STATE_COLORS[st.state]
        pygame.draw.rect(surface, PANEL, rect, border_radius=8)
        pygame.draw.rect(surface, color, (rect.x, rect.y, rect.w, 34), border_top_left_radius=8,
                         border_top_right_radius=8)
        self._text(surface, st.name.upper(), (rect.x + 8, rect.y + 7), self.font)

        lines = [
            (st.state.value, TEXT),
            (tags["reason_code"] or "", DIM_TEXT),
            (st.current_product or "-", DIM_TEXT),
            (f"done {tags['part_count']}   rej {tags['reject_count']}", TEXT),
        ]
        m = self._station_oee.get(st.name)
        if m is not None:
            lines.append((f"OEE {m.oee:.0%}  A {m.availability:.0%}", TEXT))
        lines.append((self._station_detail(st, tags), DIM_TEXT))
        y = rect.y + 42
        for text, col in lines:
            self._text(surface, text, (rect.x + 8, y), self.small, col)
            y += 20

    def _station_detail(self, st, tags: dict) -> str:
        """One station-specific line, from the station's own tags."""
        if "paste_g" in tags:
            return f"paste {tags['paste_g']:.0f} g  wipe {tags.get('prints_since_wipe', 0)}"
        if "mispick_count" in tags:
            low = min((v for k, v in tags.items() if k.endswith("_qty") and v > 0), default=0)
            return f"lowest reel {low}"
        if "peak_zone_offset_c" in tags:
            inside = len(st.boards_inside())
            return f"{inside}/{st.capacity} in  {tags['peak_zone_offset_c']:+.1f} C"
        return ""

    def _draw_buffer(self, surface: pygame.Surface, level: int, capacity: int, x: int, y_mid: int) -> None:
        slot_h, gap = 14, 4
        total_h = capacity * slot_h + (capacity - 1) * gap
        y = y_mid - total_h // 2
        for slot in range(capacity):
            filled = slot >= capacity - level   # fill from the bottom up
            pygame.draw.rect(surface, SLOT_FULL if filled else SLOT_EMPTY,
                             (x, y + slot * (slot_h + gap), BUFFER_W - 8, slot_h), border_radius=3)

    def _draw_recent_events(self, surface: pygame.Surface) -> None:
        top = LINE_Y + STATION_H + 30
        self._text(surface, "Recent stops and state changes", (20, top), self.font)
        events = [e for e in self.sim.event_log.events[-200:]
                  if e.new_state not in (StationState.RUNNING, StationState.STARVED, StationState.BLOCKED)]
        for i, e in enumerate(events[-12:]):
            t = e.sim_time_s
            stamp = f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"
            self._text(surface, f"{stamp}  {e.station:8s} {e.new_state.value:17s} {e.reason_code or ''}",
                       (20, top + 24 + i * 18), self.small, STATE_COLORS[e.new_state])

        if self._quality and self._quality.detected_pareto:
            x = 700
            self._text(surface, "Detected defects (blamed station, type)", (x, top), self.font)
            for i, (origin, typ, n) in enumerate(self._quality.detected_pareto[:8]):
                self._text(surface, f"{n:5d}  {origin:8s} {typ}", (x, top + 24 + i * 18), self.small)

    def _draw_legend(self, surface: pygame.Surface) -> None:
        x, y = 20, HEIGHT - 50
        for state, color in STATE_COLORS.items():
            pygame.draw.rect(surface, color, (x, y, 14, 14), border_radius=3)
            label = self.small.render(state.value, True, TEXT)
            surface.blit(label, (x + 20, y))
            x += 40 + label.get_width()
        self._text(surface, "Up/Down: speed   Space: pause   Esc: quit", (20, HEIGHT - 26), self.small, DIM_TEXT)

    # ---- helpers ---------------------------------------------------------------

    def _text(self, surface, text, pos, font, color=TEXT) -> None:
        surface.blit(font.render(text, True, color), pos)


def advance(sim: Simulator, speed: float, frame_s: float, tick_debt: float) -> float:
    """Step the sim for one frame at `speed` (sim s per real s); return the leftover tick fraction.

    Carrying the fraction over means e.g. 1x speed at 30 fps (1/60 tick per
    frame) still advances exactly 1 sim second per real second.
    """
    tick_debt += speed * frame_s / sim.dt_s
    n_ticks = int(tick_debt)
    tick_debt -= n_ticks
    for _ in range(min(n_ticks, MAX_TICKS_PER_FRAME)):
        sim.step()
    return tick_debt


def run(sim: Simulator, speed: float = 60.0) -> None:
    """Open a window and run the simulation live until the user quits."""
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("SMT line simulator")
    clock = pygame.time.Clock()
    view = LineView(sim)
    speed_index = min(range(len(SPEEDS)), key=lambda i: abs(SPEEDS[i] - speed))
    paused = False
    tick_debt = 0.0   # fractional ticks carried to the next frame, so slow speeds stay exact

    running = True
    while running:
        frame_s = clock.tick(FPS) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key == pygame.K_SPACE:
                    paused = not paused
                elif event.key in (pygame.K_UP, pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                    speed_index = min(speed_index + 1, len(SPEEDS) - 1)
                elif event.key in (pygame.K_DOWN, pygame.K_MINUS, pygame.K_KP_MINUS):
                    speed_index = max(speed_index - 1, 0)

        if not paused:
            tick_debt = advance(sim, SPEEDS[speed_index], frame_s, tick_debt)

        view.draw(screen, SPEEDS[speed_index], paused)
        pygame.display.flip()

    pygame.quit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--speed", type=float, default=60.0, help="sim seconds per real second")
    parser.add_argument("--scenario", default=None, help="name in config/scenarios/ or a path")
    args = parser.parse_args()
    if args.scenario:
        sim = load_scenario(args.scenario).build_simulator(seed=args.seed)
    else:
        sim = Simulator(args.config, seed=args.seed)
    run(sim, speed=args.speed)


if __name__ == "__main__":
    main()
