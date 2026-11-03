import asyncio
import logging
import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv

from app.errors import AppError

# 현재 파일 위치 기준으로 .env 로드 시도
load_dotenv()

logger = logging.getLogger(__name__)

NEXON_BASE_URL = "https://open.api.nexon.com/maplestory/v1"

# Nexon Open API 오류 코드 -> (우리 HTTP 상태, 사용자 메시지, Retry-After 초).
# 공식 표(openapi.nexon.com/ko/guide/request-api)의 코드 대부분이 HTTP 400 으로 내려오므로
# 상태 코드가 아니라 응답 본문의 error.name 으로 구분해야 "없는 캐릭터"와 "키 오류·점검"을 가를 수 있습니다.
NEXON_ERROR_MAP: dict[str, tuple[int, str, int | None]] = {
    "OPENAPI00001": (502, "Nexon API 서버에 일시적인 오류가 있습니다. 잠시 후 다시 시도해주세요.", 30),
    "OPENAPI00002": (503, "Nexon API 접근 권한이 없습니다. 운영자 확인이 필요합니다.", None),
    "OPENAPI00003": (404, "캐릭터 정보를 찾을 수 없습니다. 잠시 후 다시 시도해주세요.", None),
    "OPENAPI00004": (400, "Nexon API 요청 정보가 올바르지 않습니다.", None),
    "OPENAPI00005": (503, "서버의 Nexon API 키 설정에 문제가 있습니다. 운영자 확인이 필요합니다.", None),
    "OPENAPI00006": (502, "Nexon API 요청 경로가 올바르지 않습니다. 운영자 확인이 필요합니다.", None),
    "OPENAPI00007": (429, "조회 요청이 많습니다. 잠시 후 다시 시도해주세요.", 10),
    "OPENAPI00009": (503, "Nexon 데이터가 준비 중입니다. 잠시 후 다시 시도해주세요.", 60),
    "OPENAPI00010": (503, "메이플스토리 점검 중입니다. 점검이 끝난 뒤 다시 시도해주세요.", 600),
    "OPENAPI00011": (503, "Nexon Open API 점검 중입니다. 잠시 후 다시 시도해주세요.", 600),
}

# 본문에서 코드를 읽지 못했을 때 HTTP 상태만으로 고르는 예비 매핑.
_STATUS_FALLBACK: dict[int, tuple[int, str, int | None]] = {
    400: (400, "Nexon API 요청 정보가 올바르지 않습니다.", None),
    401: (503, "Nexon API 인증에 실패했습니다.", None),
    403: (503, "Nexon API 인증에 실패했습니다.", None),
    404: (404, "Nexon API에서 요청한 데이터를 찾을 수 없습니다.", None),
    429: (429, "조회 요청이 많습니다. 잠시 후 다시 시도해주세요.", 5),
}
_GENERIC_FAILURE = (502, "Nexon API 요청에 실패했습니다.", None)

# /id 에서 "그런 닉네임이 없다"로 해석하는 코드. 키 오류·점검 코드는 여기에 없으므로 그대로 전달됩니다.
_MISSING_CHARACTER_CODES = {"OPENAPI00004", "NEXON_HTTP_400", "NEXON_HTTP_404"}

# 운영자가 바로 봐야 하는 코드. 요청마다 찍지 않도록 간격을 둡니다.
_OPERATOR_ALERT_CODES = {"OPENAPI00002", "OPENAPI00005", "OPENAPI00006"}
_ALERT_INTERVAL_SEC = 60.0


class NexonAPIError(AppError):
    """Nexon API 실패를 애플리케이션의 HTTP 오류로 변환하기 위한 예외."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        code: str = "NEXON_ERROR",
        retry_after: int | None = None,
        upstream_status: int | None = None,
    ):
        super().__init__(message, status_code=status_code, code=code, retry_after=retry_after)
        self.upstream_status = upstream_status


def parse_error_code(response: httpx.Response) -> str | None:
    """Nexon 오류 본문 {"error": {"name": "OPENAPI000xx", "message": ...}} 에서 코드를 꺼냅니다."""
    try:
        data = response.json()
    except ValueError:
        return None
    error = data.get("error") if isinstance(data, dict) else None
    name = error.get("name") if isinstance(error, dict) else None
    return name if isinstance(name, str) and name else None


class NexonAPIHandler:
    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: httpx.Timeout | None = None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("NEXON_API_KEY", "")
        self.base_url = NEXON_BASE_URL
        self.headers = {"x-nxopen-api-key": self.api_key}
        self.client = client
        # 연결 3초, 읽기 6초. 진단 1건이 슬롯을 붙잡는 최악 시간을 줄여 대기열이 밀리지 않게 합니다.
        self.timeout = timeout or httpx.Timeout(connect=3.0, read=6.0, write=6.0, pool=5.0)
        self._client_lock = asyncio.Lock()
        self._last_alert_at: float | None = None

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
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
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

    def _alert_operator(self, code: str, upstream_status: int) -> None:
        now = time.monotonic()
        # monotonic 은 부팅 후 경과 시간이라 갓 켜진 머신(CI, 새 컨테이너)에서는 60 보다 작을 수 있습니다. 0 과 비교하면 첫 경고가 사라집니다.
        if self._last_alert_at is not None and now - self._last_alert_at < _ALERT_INTERVAL_SEC:
            return
        self._last_alert_at = now
        logger.critical(
            "Nexon API rejected the server's credentials or request path",
            extra={"nexon_code": code, "upstream_status": upstream_status},
        )

    def _error_from_response(self, response: httpx.Response) -> NexonAPIError:
        code = parse_error_code(response)
        mapped = NEXON_ERROR_MAP.get(code) if code else None
        if mapped is None:
            mapped = _STATUS_FALLBACK.get(response.status_code, _GENERIC_FAILURE)
        status_code, message, retry_after = mapped
        if code in _OPERATOR_ALERT_CODES:
            self._alert_operator(code, response.status_code)
        return NexonAPIError(
            message,
            status_code=status_code,
            code=code or f"NEXON_HTTP_{response.status_code}",
            retry_after=retry_after,
            upstream_status=response.status_code,
        )

    async def _request(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self.api_key:
            raise NexonAPIError(
                "서버에 Nexon API 키가 설정되지 않았습니다.",
                status_code=503,
                code="NEXON_KEY_MISSING",
            )

        client = await self._get_client()

        try:
            response = await client.get(path, params=params)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise NexonAPIError(
                "Nexon API 응답 시간이 초과되었습니다.",
                status_code=504,
                code="NEXON_TIMEOUT",
                retry_after=10,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise self._error_from_response(exc.response) from exc
        except httpx.RequestError as exc:
            raise NexonAPIError(
                "Nexon API에 연결할 수 없습니다.",
                status_code=502,
                code="NEXON_UNREACHABLE",
                retry_after=30,
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise NexonAPIError(
                "Nexon API가 올바르지 않은 응답을 반환했습니다.",
                status_code=502,
                code="NEXON_BAD_RESPONSE",
                upstream_status=response.status_code,
            ) from exc

        if not isinstance(data, dict):
            raise NexonAPIError(
                "Nexon API가 올바르지 않은 응답을 반환했습니다.",
                status_code=502,
                code="NEXON_BAD_RESPONSE",
                upstream_status=response.status_code,
            )
        return data

    async def get_ocid(self, character_name: str) -> str:
        try:
            data = await self._request("/id", {"character_name": character_name})
        except NexonAPIError as exc:
            # /id 의 파라미터 오류(OPENAPI00004)는 "그런 닉네임이 없다"는 뜻입니다.
            # 같은 HTTP 400 이라도 키 오류·점검 코드는 변환하지 않고 그대로 전달합니다.
            if exc.code in _MISSING_CHARACTER_CODES:
                raise NexonAPIError(
                    "캐릭터를 찾을 수 없습니다.",
                    status_code=404,
                    code="CHARACTER_NOT_FOUND",
                    upstream_status=exc.upstream_status,
                ) from exc
            raise

        ocid = data.get("ocid")
        if not isinstance(ocid, str) or not ocid:
            raise NexonAPIError("캐릭터를 찾을 수 없습니다.", status_code=404, code="CHARACTER_NOT_FOUND")
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
