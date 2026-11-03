"""사용자에게 그대로 내려가는 애플리케이션 오류.

main.py 의 예외 핸들러가 이 예외를 {"error", "code", "retry_after"} JSON 과 Retry-After 헤더로 바꿉니다.
"""


class AppError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        code: str = "INTERNAL_ERROR",
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after

    def to_payload(self) -> dict:
        payload = {"error": self.message, "code": self.code}
        if self.retry_after is not None:
            payload["retry_after"] = self.retry_after
        return payload
