import asyncio
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from patchright.async_api import APIRequestContext

from core.logger import log


class CaptchaAPIService:
    """
    Thin wrapper around the Multibot API for solving hCaptcha tasks.
    Uses the Patchright request context to avoid additional dependencies.
    """

    def __init__(
        self,
        request_context: APIRequestContext,
        api_key: str,
        base_url: str = "https://api.multibot.in",
        *,
        poll_interval: float = 1.0,
        max_wait_time: float = 15.0,
    ) -> None:
        self._request_context = request_context
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.max_wait_time = max_wait_time

    async def create_task(
        self,
        task_payload: Any,
        *,
        task_type: str = "hCaptchaBase64",
    ) -> Optional[str]:
        """
        Create a Multibot task and return its identifier, or None on failure.
        """
        payload = {
            "clientKey": self.api_key,
            "type": task_type,
            "task": task_payload,
        }
        try:
            response = await self._request_context.post(
                f"{self.base_url}/createTask/index.php",
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=30 * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            log.failure(f"Failed to create task: {exc}")
            return None

        if not response.ok:
            log.failure(f"Multibot API returned HTTP {response.status}")
            return None

        data = await response.json()
        if data.get("errorId"):
            log.failure(
                f"createTask error [{data.get('errorCode')}]: {data.get('errorDescription')}"
            )
            return None

        task_id = data.get("taskId")
        if not task_id:
            log.failure("Multibot API response did not include taskId")
            return None

        return task_id

    async def wait_for_result(
        self,
        task_id: str,
        *,
        return_raw: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Poll Multibot for the task result.
        Returns the parsed answer, or the raw response when return_raw=True.
        """
        elapsed = 0.0
        while elapsed <= self.max_wait_time:
            result = await self._fetch_result(task_id)
            if result is None:
                return None

            status = result.get("status")
            if status == "ready":
                if return_raw:
                    return result

                answers = result.get("answers")
                if answers is None:
                    log.failure("Multibot returned an empty answers payload")
                    return None
                return answers

            if status == "failed":
                log.failure("Multibot failed to solve the challenge")
                return None

            await asyncio.sleep(self.poll_interval)
            elapsed += self.poll_interval

        log.failure(
            f"Timed out waiting for task {task_id} after {self.max_wait_time} seconds"
        )
        return None

    async def request_human_move(
        self, route: Sequence[Sequence[float]]
    ) -> Optional[List[Tuple[float, float, float]]]:
        """
        Request a human-like mouse path from Multibot.
        route: list of points [[x1, y1], [x2, y2], ...]
        Returns a list of tuples [x, y, delay_ms] or None on failure.
        """
        task_payload = [
            {
                "type": "move",
                "patch": [
                    [float(point[0]), float(point[1])] for point in route
                ],
            }
        ]

        task_id = await self.create_task(task_payload, task_type="humanMove")
        if not task_id:
            return None

        result = await self.wait_for_result(task_id, return_raw=True)
        if not result:
            return None

        if not isinstance(result, dict):
            log.failure("Multibot returned a non-dict payload for humanMove")
            return None

        answers = result.get("answers")
        if not isinstance(answers, list) or not answers:
            log.failure("Multibot returned an empty answers list for humanMove")
            return None

        normalized: List[Tuple[float, float, float]] = []
        for segment in answers:
            if not isinstance(segment, dict):
                continue
            path = segment.get("path")
            if not isinstance(path, list):
                continue
            for entry in path:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    continue
                x = float(entry[0])
                y = float(entry[1])
                delay_ms = 0.0
                if len(entry) > 2 and entry[2] is not None:
                    try:
                        delay_ms = float(entry[2])
                    except (TypeError, ValueError):
                        delay_ms = 0.0
                normalized.append((x, y, delay_ms))

        if not normalized:
            log.failure("Failed to normalize humanMove trajectory")
            return None

        return normalized

    async def _fetch_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Helper to retrieve a task result payload from Multibot.
        """
        payload = {"clientKey": self.api_key, "taskId": task_id}
        try:
            response = await self._request_context.post(
                f"{self.base_url}/getTaskResult/index.php",
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=30 * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            log.failure(f"Failed to fetch task {task_id} result: {exc}")
            return None

        if not response.ok:
            log.failure(
                f"HTTP {response.status} while retrieving result for task {task_id}"
            )
            return None

        data = await response.json()
        if data.get("errorId"):
            log.failure(
                f"getTaskResult error [{data.get('errorCode')}]: {data.get('errorDescription')}"
            )
            return None
        return data

