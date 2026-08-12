import asyncio
import logging
import os
from typing import Any

import httpx
from dotenv import load_dotenv

# 현재 파일 위치 기준으로 .env 로드 시도
load_dotenv()

logger = logging.getLogger(__name__)


class NexonAPIError(RuntimeError):
    """Nexon API 실패를 애플리케이션의 HTTP 오류로 변환하기 위한 예외."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        upstream_status: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.upstream_status = upstream_status


class NexonAPIHandler:
    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("NEXON_API_KEY", "")
        self.base_url = "https://open.api.nexon.com/maplestory/v1"
        self.headers = {"x-nxopen-api-key": self.api_key}
        self.client = client
        self._client_lock = asyncio.Lock()

        if self.api_key:
            logger.info("Nexon API key loaded")
        else:
            logger.warning("Nexon API key is missing")

    async def start(self) -> None:
        """공유 HTTP 클라이언트를 생성합니다. 여러 번 호출해도 하나만 유지됩니다."""
        async with self._client_lock:
            if self.client is not None and not self.client.is_closed:
                return

            self.client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=httpx.Timeout(10.0),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                ),
            )

    async def close(self) -> None:
        """애플리케이션 종료 시 연결 풀을 정리합니다."""
        async with self._client_lock:
            if self.client is not None and not self.client.is_closed:
                await self.client.aclose()
            self.client = None

    async def _get_client(self) -> httpx.AsyncClient:
        # lifespan 밖에서 직접 호출되는 테스트/스크립트도 안전하게 지원합니다.
        if self.client is None or self.client.is_closed:
            await self.start()
        assert self.client is not None
        return self.client

    async def _request(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self.api_key:
            raise NexonAPIError(
                "서버에 Nexon API 키가 설정되지 않았습니다.",
                status_code=503,
            )

        client = await self._get_client()

        try:
            response = await client.get(path, params=params)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise NexonAPIError(
                "Nexon API 응답 시간이 초과되었습니다.",
                status_code=504,
            ) from exc
        except httpx.HTTPStatusError as exc:
            upstream_status = exc.response.status_code
            if upstream_status == 400:
                message, status_code = "Nexon API 요청 정보가 올바르지 않습니다.", 400
            elif upstream_status in (401, 403):
                message, status_code = "Nexon API 인증에 실패했습니다.", 503
            elif upstream_status == 404:
                message, status_code = "Nexon API에서 요청한 데이터를 찾을 수 없습니다.", 404
            elif upstream_status == 429:
                message, status_code = "조회 요청이 많습니다. 잠시 후 다시 시도해주세요.", 429
            else:
                message, status_code = "Nexon API 요청에 실패했습니다.", 502

            raise NexonAPIError(
                message,
                status_code=status_code,
                upstream_status=upstream_status,
            ) from exc
        except httpx.RequestError as exc:
            raise NexonAPIError(
                "Nexon API에 연결할 수 없습니다.",
                status_code=502,
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise NexonAPIError(
                "Nexon API가 올바르지 않은 응답을 반환했습니다.",
                status_code=502,
                upstream_status=response.status_code,
            ) from exc

        if not isinstance(data, dict):
            raise NexonAPIError(
                "Nexon API가 올바르지 않은 응답을 반환했습니다.",
                status_code=502,
                upstream_status=response.status_code,
            )
        return data

    async def get_ocid(self, character_name: str) -> str:
        try:
            data = await self._request("/id", {"character_name": character_name})
        except NexonAPIError as exc:
            if exc.upstream_status in (400, 404):
                raise NexonAPIError(
                    "캐릭터를 찾을 수 없습니다.",
                    status_code=404,
                    upstream_status=exc.upstream_status,
                ) from exc
            raise

        ocid = data.get("ocid")
        if not isinstance(ocid, str) or not ocid:
            raise NexonAPIError("캐릭터를 찾을 수 없습니다.", status_code=404)
        return ocid

    async def get_character_basic(self, ocid: str) -> dict[str, Any]:
        """ocid로 캐릭터 기본 정보(이름, 월드, 직업, 레벨, 이미지)를 가져옵니다."""
        return await self._request("/character/basic", {"ocid": ocid})

    async def get_character_stat(self, ocid: str) -> dict[str, Any]:
        """ocid로 캐릭터의 상세 스탯(전투력 등)을 가져옵니다."""
        return await self._request("/character/stat", {"ocid": ocid})

    async def get_character_item(self, ocid: str) -> dict[str, Any]:
        """캐릭터의 장비 아이템 정보를 가져옵니다."""
        return await self._request("/character/item-equipment", {"ocid": ocid})
