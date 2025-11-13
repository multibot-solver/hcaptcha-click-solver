import asyncio
import math
import random
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from patchright.async_api import Page

@dataclass
class Point:
    x: float
    y: float


class MouseMotion:
    """
    Simulates pointer movement on the page, supporting human-like trajectories.
    """

    def __init__(
        self,
        page: Page,
    ) -> None:
        self.page = page
        self._position: Optional[Point] = None
        self._human_trace: Optional[List[Tuple[float, float]]] = None

    @property
    def current_position(self) -> Optional[Tuple[float, float]]:
        if self._position is None:
            return None
        return (self._position.x, self._position.y)

    @property
    def human_trace(self) -> Optional[List[Tuple[float, float]]]:
        return self._human_trace

    def set_position(self, x: float, y: float) -> None:
        """
        Forcefully sets the current pointer position.
        """
        self._position = Point(x, y)

    async def move_to(self, x: float, y: float, *, record_trace: bool = False) -> None:
        target = Point(x, y)

        start = self._position or await self._init_position()
        path = self._build_path(start, target)
        if record_trace:
            self._human_trace = [(start.x, start.y)] + [(point.x, point.y) for point in path]
        else:
            self._human_trace = None

        for point in path:
            await self.page.mouse.move(point.x, point.y, steps=1)

        self._position = Point(target.x, target.y)

    async def move_direct(
        self,
        x: float,
        y: float,
        *,
        delay: Optional[float] = None,
        steps: Optional[int] = None,
    ) -> None:
        if self._position is None:
            await self._init_position()

        target = Point(x, y)
        pause = max(0.0, delay or 0.0)
        if pause > 0:
            await asyncio.sleep(pause)

        step_count = steps or 1
        await self.page.mouse.move(target.x, target.y, steps=step_count)
        self._position = target

    async def move_points(
        self,
        points: Sequence[Tuple[float, float, Optional[float]]],
    ) -> None:
        if not points:
            return
        for x, y, delay in points:
            await self.move_direct(x, y, delay=delay)

    async def click(
        self,
        x: float,
        y: float,
        *,
        delay_before: float = 0.0,
        delay_after: float = 0.0,
        record_trace: bool = False,
    ) -> None:
        await self.move_to(x, y, record_trace=record_trace)
        if delay_before > 0:
            await asyncio.sleep(delay_before)
        await self.page.mouse.down()
        await self.page.mouse.up()
        if delay_after > 0:
            await asyncio.sleep(delay_after)

    async def drag_and_drop(self, start: Point, end: Point, steps: int = 30) -> None:
        await self.move_to(start.x, start.y)
        await self.page.mouse.down()

        path = self._build_path(start, end, steps=max(10, int(steps * 1.2)))
        for point in path:
            await self.page.mouse.move(point.x, point.y, steps=1)

        await self.page.mouse.up()
        self._position = Point(end.x, end.y)

    async def click_here(
        self,
        *,
        delay_before: float = 0.0,
        delay_after: float = 0.0,
    ) -> None:
        if delay_before > 0:
            await asyncio.sleep(delay_before)
        await self.page.mouse.down()
        await self.page.mouse.up()
        if delay_after > 0:
            await asyncio.sleep(delay_after)

    async def _init_position(self) -> Point:
        viewport = self.page.viewport_size
        if viewport:
            start = Point(
                x=random.uniform(viewport["width"] * 0.2, viewport["width"] * 0.8),
                y=random.uniform(viewport["height"] * 0.2, viewport["height"] * 0.8),
            )
        else:
            # Fallback when viewport is undefined
            start = Point(x=random.uniform(200, 600), y=random.uniform(200, 500))

        await self.page.mouse.move(start.x, start.y)
        self._position = start
        return start

    def _build_path(self, start: Point, end: Point, steps: Optional[int] = None) -> List[Point]:
        distance = math.dist((start.x, start.y), (end.x, end.y))
        step_count = steps or max(12, min(45, int(distance / 12)))

        control_scale = max(distance * 0.25, 40)
        angle = math.atan2(end.y - start.y, end.x - start.x)
        control_angle = angle + random.uniform(-0.9, 0.9)

        control1 = Point(
            x=start.x + math.cos(control_angle) * control_scale,
            y=start.y + math.sin(control_angle) * control_scale,
        )
        control2 = Point(
            x=end.x - math.cos(control_angle) * control_scale,
            y=end.y - math.sin(control_angle) * control_scale,
        )

        path: List[Point] = []
        for i in range(1, step_count + 1):
            t = i / step_count
            x = (
                (1 - t) ** 3 * start.x
                + 3 * (1 - t) ** 2 * t * control1.x
                + 3 * (1 - t) * t**2 * control2.x
                + t**3 * end.x
            )
            y = (
                (1 - t) ** 3 * start.y
                + 3 * (1 - t) ** 2 * t * control1.y
                + 3 * (1 - t) * t**2 * control2.y
                + t**3 * end.y
            )

            jitter = min(6, max(1.2, distance / 60))
            x += random.uniform(-jitter, jitter)
            y += random.uniform(-jitter, jitter)
            path.append(Point(x=x, y=y))

        path.append(end)
        return path
