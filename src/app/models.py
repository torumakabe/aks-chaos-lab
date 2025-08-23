from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    timestamp: str
    request_id: str | None = None


class HealthResponse(BaseModel):
    status: str
    redis: dict[str, int | bool] | None = None
    timestamp: str


class MainResponse(BaseModel):
    message: str
    redis_data: str | None = None
    timestamp: str
