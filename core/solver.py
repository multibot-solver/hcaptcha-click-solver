import asyncio
import base64
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from patchright.async_api import (
    ElementHandle,
    Frame,
    Page,
    Request,
    Route,
    TimeoutError as PatchrightTimeoutError,
)

from core.api_service import CaptchaAPIService
from core.logger import log
from core.motion import MouseMotion, Point


class HCaptchaSolver:
    """
    High-level hCaptcha solver backed by Multibot for image classification and
    human-like cursor movement.

    Parameters:
        page: Playwright/Patchright page instance used to interact with hCaptcha iframes.
        api_key: Multibot API key for fetching challenge solutions and human-like paths.
        attempt: Maximum number of solve iterations before giving up.
        last_mouse_position: Optional cursor coordinates to resume from between solver runs.
        intercept_token: When True, intercepts the hCaptcha network response and returns
            the token immediately without waiting for page scripts.
    """

    def __init__(
        self,
        page: Page,
        api_key: str,
        attempt: int = 10,
        last_mouse_position: Optional[Dict[str, float]] = None,
        intercept_token: bool = False,
    ) -> None:
        self.page = page
        self.api_key = api_key
        self.motion = MouseMotion(page)
        self.api_service = CaptchaAPIService(
            page.context.request,
            api_key,
        )
        self.attempt = attempt
        self.intercept_token = intercept_token
        viewport = getattr(page, "viewport_size", None) or {}
        width = viewport.get("width") or 1920
        height = viewport.get("height") or 1080
        default_position = {
            "x": width * 0.45 + random.random() * width * 0.1,
            "y": height * 0.45 + random.random() * height * 0.1,
        }
        if last_mouse_position is not None:
            self.last_mouse_position = {
                "x": float(last_mouse_position.get("x", default_position["x"])),
                "y": float(last_mouse_position.get("y", default_position["y"])),
            }
        else:
            self.last_mouse_position = default_position
        self.token = None
        self.checkbox_frame = None
        self.challenge_frame = None
        self._token_event = asyncio.Event()
        self._response_listener_attached = False
        
    def _get_last_mouse_position(self) -> Tuple[float, float]:
        position = self.last_mouse_position
        return float(position["x"]), float(position["y"])

    def _set_last_mouse_position(self, x: float, y: float) -> None:
        self.last_mouse_position = {"x": float(x), "y": float(y)}

    async def _token_aware_sleep(self, delay: float) -> None:
        if delay <= 0:
            return
        if not self.intercept_token:
            await asyncio.sleep(delay)
            return
        if self.token or self._token_event.is_set():
            return

        sleep_task = asyncio.create_task(asyncio.sleep(delay))
        event_task = asyncio.create_task(self._token_event.wait())
        done, pending = await asyncio.wait(
            [sleep_task, event_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _handle_checkbox(self) -> bool:
        """
        Attempts to click the hCaptcha checkbox. Returns True if a click was attempted.
        """
        if self.checkbox_frame is None:
            return False

        checkbox = await self.checkbox_frame.query_selector("#checkbox")
        if not checkbox:
            await asyncio.sleep(0.5)
            return True

        aria_checked = await checkbox.get_attribute("aria-checked")
        if aria_checked == "true":
            await asyncio.sleep(0.5)
            return True

        box = await checkbox.bounding_box()
        if not box:
            await asyncio.sleep(0.5)
            return True

        target_x = box["x"] + box["width"] / 2
        target_y = box["y"] + box["height"] / 2
        start_x, start_y = self._get_last_mouse_position()

        human_path = await self.api_service.request_human_move([[start_x, start_y], [target_x, target_y]])
        if human_path:
            await self.page.mouse.move(start_x, start_y, steps=1)
            self.motion.set_position(start_x, start_y)
            trace = [
                (px, py, (delay_ms or 0.0) / 1_000)
                for px, py, delay_ms in human_path
            ]
            await self.motion.move_points(trace)
            await self.motion.click_here()
            last_x, last_y, _ = human_path[-1]
            self._set_last_mouse_position(last_x, last_y)
        else:
            await self.motion.click(target_x, target_y)
            self._set_last_mouse_position(target_x, target_y)

        await asyncio.sleep(0.8)
        return True

    async def _handle_challenge_round(self) -> bool:
        """
        Collects challenge data, submits to Multibot, and applies the returned answer.
        Returns True if the challenge flow completed successfully.
        """
        frame = self.challenge_frame
        if frame is None:
            return False

        await self._ensure_english_language()

        task_payload = await self._collect_challenge_data()
        if not task_payload:
            return False

        request_type = str(task_payload.get("request_type") or "")
        task_id = await self.api_service.create_task(task_payload)
        if not task_id:
            return False

        answers = await self.api_service.wait_for_result(task_id)
        if not answers:
            return False

        if self.token:
            return True

        applied = await self._apply_answers(frame, request_type, answers)
        if not applied:
            return False

        if self.token:
            return True

        if self.token:
            return True

        delay = 5 if await self._is_last_task() else 1
        if self.token:
            return True
        await self._token_aware_sleep(delay)
        return True

    async def solve(self) -> Optional[str]:
        if not (self.api_key and str(self.api_key).strip()):
            log.failure("Multibot API key is missing; aborting solve()")
            return None

        self.token = None
        if self._token_event.is_set():
            self._token_event.clear()

        if self.intercept_token:
            await self._ensure_network_listener()

        for _ in range(self.attempt):
            if self.token:
                return self.token

            token = await self.wait_token(1_000)
            if token:
                self.token = token
                return self.token

            self.challenge_frame = await self._find_challenge_visual_frame()

            if self.challenge_frame is None:
                self.checkbox_frame = await self._find_checkbox_frame()
                if await self._handle_checkbox():
                    continue
                await asyncio.sleep(0.5)
                continue

            if await self._handle_challenge_round():
                continue

            await self._click_submit_button(self.challenge_frame)
            await self._token_aware_sleep(0.8)

        return self.token

    async def wait_token(self, timeout: int = 10_000) -> Optional[str]:
        if self.token:
            return self.token

        if self.intercept_token:
            wait_seconds: Optional[float]
            if timeout is None or timeout < 0:
                wait_seconds = None
            else:
                wait_seconds = timeout / 1_000

            try:
                await asyncio.wait_for(self._token_event.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                return self.token
            except Exception:  # noqa: BLE001
                return self.token
            return self.token

        try:
            token_handle = await self.page.wait_for_function(
                """
                () => {
                    if (typeof hcaptcha !== "undefined" && hcaptcha) {
                        const token = hcaptcha.getResponse();
                        return token && token.trim() !== "" ? token : null;
                    }
                    return null;
                }
                """,
                timeout=timeout,
            )
        except PatchrightTimeoutError:
            return None
        except Exception:  # noqa: BLE001
            return None

        try:
            return await token_handle.json_value()
        except Exception:  # noqa: BLE001
            return None

    async def _ensure_network_listener(self) -> None:
        if not self.intercept_token or self._response_listener_attached:
            return

        async def _route_handler(route: Route, request: Request) -> None:
            if "checkcaptcha" not in (request.url or ""):
                await route.continue_()
                return

            try:
                api_response = await route.fetch()
            except Exception:
                await route.abort("failed")
                return

            try:
                data = await api_response.json()
            except Exception:
                await route.abort("failed")
                return

            token = (
                data.get("generated_pass_UUID")
            )
            if isinstance(token, str) and token.strip():
                self.token = token.strip()
                self._token_event.set()

            await route.abort("failed")

        await self.page.route("**/checkcaptcha/**", _route_handler)
        self._response_listener_attached = True
 
    async def _is_last_task(self) -> bool:
        try:
            result = await self.challenge_frame.evaluate(
                """
                () => {
                    const crumbBgs = document.querySelectorAll('.crumb-bg');
                    const lastCrumb = crumbBgs.length ? crumbBgs[crumbBgs.length - 1] : null;
                    if (!lastCrumb) {
                        return true;
                    }
                    const color = window.getComputedStyle(lastCrumb).backgroundColor;
                    return color === 'rgb(245, 245, 245)';
                }
                """
            )
            return bool(result)
        except Exception:
            return True
 
 
    async def _find_challenge_visual_frame(self) -> Optional[Frame]:
        for frame in self.page.frames:
            url = frame.url or ""
            if "#frame=challenge" in url:
                question = await frame.evaluate(
                    "() => document.querySelector('h2.prompt-text')"
                )
                if question:
                    return frame
        return None
    
    
    async def _find_checkbox_frame(self) -> Optional[Frame]:
        for frame in self.page.frames:
            url = frame.url or ""
            if "#frame=checkbox" in url:
                pulse_is_hidden = await frame.evaluate(
                    """
                    () => {
                        const pulseElement = document.querySelector("#anchor-state > div.pulse");
                        if (!pulseElement) {
                            return false;
                        }
                        return window.getComputedStyle(pulseElement).display === "none";
                    }
                    """
                )
                if pulse_is_hidden:
                    return frame
        return None
        

    async def _ensure_english_language(self) -> None:
        try:
            current_lang = await self.challenge_frame.evaluate(
                """
                () => document.querySelector('div.display-language.button > div:nth-child(2)')?.innerText || null
                """
            )
            if current_lang == "EN":
                return

            await self.challenge_frame.evaluate(
                """
                () => {
                    const option = document.querySelector('.language-selector .option:nth-child(23)');
                    option?.click();
                }
                """
            )
            await asyncio.sleep(0.2)
        except Exception:  # noqa: BLE001
            pass
         

    async def _element_to_base64(
        self,
        element: ElementHandle,
        *,
        quality: int = 90,
    ) -> Optional[str]:
        try:
            bytes_data = await element.screenshot(
                type="jpeg",
                quality=quality,
                animations="disabled",
            )
        except Exception:  # noqa: BLE001
            return None
        result = base64.b64encode(bytes_data).decode("ascii")
        return result


    async def _collect_example_images(
        self,
        selector: str,
    ) -> List[str]:
        frame = self.challenge_frame
        if frame is None:
            return []

        try:
            elements = await frame.query_selector_all(selector)
        except Exception:  # noqa: BLE001
            return []

        results: List[str] = []
        for element in elements:
            try:
                encoded = await self._element_to_base64(element)
            except Exception:  # noqa: BLE001
                encoded = None
            if encoded:
                results.append(encoded)
            try:
                await element.dispose()
            except Exception:  # noqa: BLE001
                pass
        return results


    async def _collect_challenge_data(self) -> Optional[Dict[str, Any]]:
        frame = self.challenge_frame
        if frame is None:
            return None

        question = await frame.evaluate(
            "() => document.querySelector('.prompt-text')?.textContent?.trim() || null"
        )
        if not question:
            return None

        payload = await self._collect_grid_challenge(frame, question)
        if not payload:
            payload = await self._collect_canvas_challenge(frame, question)
        if not payload:
            return None

        human_move_payload = await self._build_human_move_payload(frame)
        if human_move_payload:
            payload["humanMove"] = human_move_payload

        return payload

    async def _collect_grid_challenge(
        self,
        frame: Frame,
        question: str,
    ) -> Optional[Dict[str, Any]]:
        grid = await frame.query_selector(".task-grid")
        if not grid:
            return None

        tiles = await frame.query_selector_all(".task-grid .image")
        if len(tiles) != 9:
            return None

        await asyncio.sleep(1.0)
        body = await self._element_to_base64(grid)
        if not body:
            return None

        examples = await self._collect_example_images(".challenge-example .image")
        return {
            "question": question,
            "request_type": "Grid",
            "body": body,
            "examples": examples,
        }

    async def _collect_canvas_challenge(
        self,
        frame: Frame,
        question: str,
    ) -> Optional[Dict[str, Any]]:
        canvas = await self._find_primary_canvas(frame)
        if not canvas:
            return None

        await asyncio.sleep(1.0)
        body = await self._element_to_base64(canvas, quality=92)
        if not body:
            return None

        has_header = await frame.query_selector(".challenge-header") is not None
        question_lower = question.lower()
        is_canvas = has_header and "drag" not in question_lower
        request_type = "Canvas" if is_canvas else "Drag"
        examples = await self._collect_example_images(".example-wrapper .image")

        return {
            "question": question,
            "request_type": request_type,
            "body": body,
            "examples": examples,
        }

    async def _build_human_move_payload(self, frame: Frame) -> Optional[List[List[float]]]:
        frame_offset_x = 0.0
        frame_offset_y = 0.0
        try:
            frame_element = await frame.frame_element()
            if frame_element:
                frame_box = await frame_element.bounding_box()
                if frame_box:
                    frame_offset_x = float(frame_box["x"])
                    frame_offset_y = float(frame_box["y"])
        except Exception:  # noqa: BLE001
            pass

        current_position = self.motion.current_position
        if current_position:
            start_x, start_y = current_position
        else:
            start_x, start_y = self._get_last_mouse_position()

        submit_button = await frame.query_selector(".button-submit")
        if not submit_button:
            submit_button = await frame.query_selector('button[type="submit"]')
        if not submit_button:
            return None

        submit_box = await submit_button.bounding_box()
        if not submit_box:
            return None

        submit_center_x = float(submit_box["x"] + submit_box["width"] / 2)
        submit_center_y = float(submit_box["y"] + submit_box["height"] / 2)

        start_point = [
            max(0.0, round(start_x - frame_offset_x, 2)),
            max(0.0, round(start_y - frame_offset_y, 2)),
        ]
        end_point = [
            max(0.0, round(submit_center_x - frame_offset_x, 2)),
            max(0.0, round(submit_center_y - frame_offset_y, 2)),
        ]

        payload = [
            [float(start_point[0]), float(start_point[1])],
            [float(end_point[0]), float(end_point[1])],
        ]
        return payload

    async def _find_primary_canvas(self, frame: Frame) -> Optional[ElementHandle]:
        canvases = await frame.query_selector_all("canvas")
        for canvas in canvases:
            box = await canvas.bounding_box()
            if not box:
                continue
            if box["width"] < 100 or box["height"] < 100:
                continue
            return canvas
        return None

    async def _apply_answers(
        self,
        frame: Frame,
        request_type: str,
        answers: Union[List[Any], Dict[str, Any]],
    ) -> bool:
        try:
            if isinstance(answers, dict):
                actions = answers.get("actions") or answers.get("steps")
                if isinstance(actions, Sequence):
                    return await self._execute_actions(frame, request_type, list(actions))
                answers_list = answers.get("answers")
                if isinstance(answers_list, Sequence):
                    answers = list(answers_list)
                else:
                    return False

            if not isinstance(answers, Sequence):
                return False

            if answers and isinstance(answers[0], dict):
                return await self._execute_actions(frame, request_type, list(answers))  # type: ignore[arg-type]

            if request_type == "Grid":
                indices = [int(index) for index in answers]
                await self._click_grid_tiles(frame, indices)
                return True

            if request_type == "Canvas":
                await self._click_canvas_points(frame, answers)  # type: ignore[arg-type]
                return True

            if request_type == "Drag":
                await self._drag_canvas_pairs(frame, answers)  # type: ignore[arg-type]
                return True

            return False
        except Exception:
            return False

    async def _execute_actions(
        self,
        frame: Frame,
        request_type: str,
        actions: List[Dict[str, Any]],
    ) -> bool:
        if not actions:
            return False

        if request_type == "Grid":
            target_root = await frame.query_selector(".task-grid")
        else:
            target_root = await self._find_primary_canvas(frame)

        submit_button = await frame.query_selector(".button-submit")
        if not target_root or not submit_button:
            return False

        root_box = await target_root.bounding_box()
        submit_box = await submit_button.bounding_box()
        if not root_box or not submit_box:
            return False

        frame_offset_x = 0.0
        frame_offset_y = 0.0
        try:
            frame_element = await frame.frame_element()
            if frame_element:
                frame_box = await frame_element.bounding_box()
                if frame_box:
                    frame_offset_x = float(frame_box["x"])
                    frame_offset_y = float(frame_box["y"])
        except Exception:  # noqa: BLE001
            pass

        canvas_relative = request_type in {"Canvas", "Drag"} and self._is_canvas_path_relative(actions, root_box)

        grid_margin_x = float(root_box.get("width", 0.0) * 0.25 + 40.0)
        grid_margin_y = float(root_box.get("height", 0.0) * 0.25 + 40.0)

        def point_converter(px: Any, py: Any) -> Tuple[float, float]:
            x = float(px)
            y = float(py)
            if request_type == "Grid":
                within_x = -grid_margin_x <= x <= root_box["width"] + grid_margin_x
                within_y = -grid_margin_y <= y <= root_box["height"] + grid_margin_y
                if within_x and within_y:
                    return (root_box["x"] + x, root_box["y"] + y)
                return (frame_offset_x + x, frame_offset_y + y)
            if request_type in {"Canvas", "Drag"}:
                return (
                    root_box["x"] + x if canvas_relative else frame_offset_x + x,
                    root_box["y"] + y if canvas_relative else frame_offset_y + y,
                )
            return (frame_offset_x + x, frame_offset_y + y)

        did_anything = False
        last_position: Optional[Tuple[float, float]] = None

        for action in actions:
            action_type = str(action.get("type", "")).lower()

            raw_path = action.get("path") or []
            if action_type == "drag" and not raw_path:
                start = action.get("start")
                end = action.get("end")
                if (
                    isinstance(start, (list, tuple))
                    and isinstance(end, (list, tuple))
                    and len(start) >= 2
                    and len(end) >= 2
                ):
                    raw_path = [
                        [float(start[0]), float(start[1]), 20],
                        [float(end[0]), float(end[1]), 20],
                    ]

            converted_path = self._convert_action_path(point_converter, raw_path)
            if not converted_path:
                fallback = action.get("start")
                if isinstance(fallback, (list, tuple)) and len(fallback) >= 2:
                    sx, sy = point_converter(fallback[0], fallback[1])
                    converted_path = [(sx, sy, 0.02)]
                    end_fallback = action.get("end")
                    if isinstance(end_fallback, (list, tuple)) and len(end_fallback) >= 2:
                        ex, ey = point_converter(end_fallback[0], end_fallback[1])
                        converted_path.append((ex, ey, 0.02))

            if action_type == "drag":
                await self._perform_drag_action(converted_path)
                if converted_path:
                    last_x, last_y, _ = converted_path[-1]
                    last_position = (last_x, last_y)
                    did_anything = True
                continue

            if converted_path:
                await self.motion.move_points(converted_path)
                last_x, last_y, _ = converted_path[-1]
                last_position = (last_x, last_y)
                did_anything = True
                if submit_box and self._point_inside_box(submit_box, last_x, last_y):
                    await self.motion.move_direct(last_x, last_y)
                    await self.motion.click_here()
                    self._set_last_mouse_position(last_x, last_y)
                    continue
                if request_type == "Grid":
                    await self._click_grid_coordinate(target_root, last_x, last_y)
                else:
                    await self.motion.move_direct(last_x, last_y)
                    await self.motion.click_here()
                continue

            target = action.get("target")
            if isinstance(target, (list, tuple)) and len(target) >= 2:
                tx, ty = point_converter(target[0], target[1])
                await self.motion.move_direct(tx, ty)
                await self.motion.click_here()
                last_position = (tx, ty)
                did_anything = True

        if last_position:
            self._set_last_mouse_position(last_position[0], last_position[1])

        return did_anything

    def _convert_action_path(
        self,
        converter,
        path: Sequence[Sequence[Any]],
    ) -> List[Tuple[float, float, Optional[float]]]:
        converted: List[Tuple[float, float, Optional[float]]] = []
        for entry in path:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            try:
                x, y = converter(entry[0], entry[1])
            except Exception:  # noqa: BLE001
                continue
            delay: Optional[float] = None
            if len(entry) >= 3:
                try:
                    delay = max(0.0, float(entry[2]) / 1000.0)
                except (TypeError, ValueError):
                    delay = None
            converted.append((x, y, delay))
        return converted

    async def _perform_drag_action(
        self,
        path_points: Sequence[Tuple[float, float, Optional[float]]],
    ) -> None:
        if not path_points:
            return

        first_x, first_y, first_delay = path_points[0]
        initial_delay = first_delay if first_delay is not None else 0.0
        await self.motion.move_direct(first_x, first_y, delay=initial_delay)
        await self.page.mouse.down()

        for x, y, delay in path_points[1:]:
            await self.motion.move_direct(x, y, delay=delay)

        await self.page.mouse.up()
        self._set_last_mouse_position(path_points[-1][0], path_points[-1][1])

    async def _click_grid_coordinate(self, grid: ElementHandle, x: float, y: float) -> None:
        tile = await self._find_grid_tile(grid, x, y)
        if tile:
            box = await tile.bounding_box()
            if box:
                target_x = box["x"] + box["width"] / 2
                target_y = box["y"] + box["height"] / 2
                await self.motion.move_direct(target_x, target_y)
                await self.motion.click_here()
                self._set_last_mouse_position(target_x, target_y)
                return
        await self.motion.move_direct(x, y)
        await self.motion.click_here()
        self._set_last_mouse_position(x, y)

    async def _find_grid_tile(
        self,
        grid: ElementHandle,
        x: float,
        y: float,
    ) -> Optional[ElementHandle]:
        tiles = await grid.query_selector_all(".task, .image")
        for tile in tiles:
            box = await tile.bounding_box()
            if not box:
                continue
            within_x = box["x"] <= x <= box["x"] + box["width"]
            within_y = box["y"] <= y <= box["y"] + box["height"]
            if within_x and within_y:
                return tile
        return None

    async def _click_grid_tiles(self, frame: Frame, indices: Sequence[int]) -> None:
        grid = await frame.query_selector(".task-grid")
        if not grid:
            return

        tiles = await grid.query_selector_all(".image, .task")
        for index in indices:
            if index >= len(tiles):
                continue
            tile = tiles[index]
            box = await tile.bounding_box()
            if not box:
                continue

            offset_x = random.uniform(-10, 10)
            offset_y = random.uniform(-10, 10)
            target_x = box["x"] + box["width"] / 2 + offset_x
            target_y = box["y"] + box["height"] / 2 + offset_y

            await self.motion.move_direct(target_x, target_y)
            await self.motion.click_here()
            self._set_last_mouse_position(target_x, target_y)

    async def _click_canvas_points(self, frame: Frame, points: Sequence[Sequence[float]]) -> None:
        canvas = await self._find_primary_canvas(frame)
        if not canvas:
            return
        box = await canvas.bounding_box()
        if not box:
            return

        for point in points:
            if len(point) < 2:
                continue
            x = box["x"] + float(point[0])
            y = box["y"] + float(point[1])
            await self.motion.click(x, y)
            self._set_last_mouse_position(x, y)

    async def _drag_canvas_pairs(self, frame: Frame, coordinates: Sequence[Sequence[float]]) -> None:
        canvas = await self._find_primary_canvas(frame)
        if not canvas:
            return
        box = await canvas.bounding_box()
        if not box:
            return

        iterator = iter(coordinates)
        for start_point in iterator:
            try:
                end_point = next(iterator)
            except StopIteration:
                break
            if (
                not isinstance(start_point, (list, tuple))
                or not isinstance(end_point, (list, tuple))
                or len(start_point) < 2
                or len(end_point) < 2
            ):
                continue
            start = Point(
                x=box["x"] + float(start_point[0]),
                y=box["y"] + float(start_point[1]),
            )
            end = Point(
                x=box["x"] + float(end_point[0]),
                y=box["y"] + float(end_point[1]),
            )
            await self.motion.drag_and_drop(start, end, steps=35)
            self._set_last_mouse_position(end.x, end.y)

    @staticmethod
    def _point_inside_box(
        box: Dict[str, float],
        x: float,
        y: float,
    ) -> bool:
        left = float(box.get("x", 0.0))
        top = float(box.get("y", 0.0))
        width = float(box.get("width", 0.0))
        height = float(box.get("height", 0.0))
        right = left + width
        bottom = top + height
        return left <= x <= right and top <= y <= bottom

    def _is_canvas_path_relative(
        self,
        actions: Sequence[Dict[str, Any]],
        root_box: Dict[str, float],
    ) -> bool:
        width = root_box.get("width", 0.0)
        height = root_box.get("height", 0.0)
        if width <= 0 or height <= 0:
            return False

        threshold_x = width + 80.0
        threshold_y = height + 80.0

        for action in actions:
            for key in ("path", "start", "end"):
                points = action.get(key)
                if points is None:
                    continue
                if isinstance(points, (list, tuple)) and points and not isinstance(points[0], (list, tuple)):
                    candidates = (points,)
                else:
                    candidates = points or []
                for entry in candidates:
                    if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                        continue
                    try:
                        px = float(entry[0])
                        py = float(entry[1])
                    except (TypeError, ValueError):
                        continue
                    if px < -80.0 or py < -80.0 or px > threshold_x or py > threshold_y:
                        return False
        return True

    async def _click_submit_button(self, frame: Frame) -> None:
        submit = await frame.query_selector(".button-submit")
        if not submit:
            submit = await frame.query_selector('button[type="submit"]')
        if not submit:
            return

        box = await submit.bounding_box()
        if not box:
            return

        target_x = box["x"] + box["width"] / 2
        target_y = box["y"] + box["height"] / 2
        await self.motion.click(target_x, target_y)
        self._set_last_mouse_position(target_x, target_y)

        
        
        
