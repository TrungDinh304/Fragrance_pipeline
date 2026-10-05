"""Adapter cho endpoint kiểu OpenAI — dùng với 9router, và với mọi thứ nói cùng
giao thức đó (OpenAI, Groq, Together, Ollama, vLLM).

VỀ 9ROUTER
Nó là một cổng chạy TRÊN MÁY HOST, mặc định `http://localhost:20128/v1`, gộp 40+
nhà cung cấp sau một endpoint kiểu OpenAI và tự chuyển sang nhà khác khi một nhà
hết hạn mức. Đo trên máy này: 51 model chat, **0 model embedding** — nên embedding
phải tự chạy local, xem `embedding/onnx.py`.

HAI CÁI BẪY ĐÃ GẶP THẬT, GHI LẠI
  1. **`localhost` trong container là chính container đó.** 9router chạy trên host,
     nên từ container phải gọi `http://host.docker.internal:20128/v1`. Vì vậy
     compose dùng biến `COMPOSE_LLM_BASE_URL` riêng, KHÔNG dùng chung
     `LLM_BASE_URL` với host — y hệt chuyện đã xảy ra với `S3_ENDPOINT`.
  2. **`/v1/models` mở nhưng `/v1/chat/completions` đòi khoá.** Thử endpoint bằng
     `/v1/models` rồi kết luận "không cần khoá" là sai; lỗi chỉ hiện ra ở lần chat
     đầu tiên với `Missing API key`.

Dùng `requests` vì nó đã là phụ thuộc lõi của project — không thêm `openai` SDK
chỉ để POST một JSON.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Sequence

from .ports import LLMUnavailable, Message, Reply

log = logging.getLogger(__name__)

BASE_URL_MAC_DINH = "http://localhost:20128/v1"
MODEL_MAC_DINH = "kr/claude-haiku-4.5"
TIMEOUT = 90


def base_url() -> str:
    return (os.environ.get("LLM_BASE_URL") or BASE_URL_MAC_DINH).rstrip("/")


def model_name() -> str:
    return (os.environ.get("LLM_MODEL") or MODEL_MAC_DINH).strip()


def api_key() -> str:
    return (os.environ.get("LLM_API_KEY") or "").strip()


def configured() -> bool:
    """Có đủ cấu hình để gọi chưa. Dùng để tầng trên tắt phần chat cho gọn
    thay vì để người dùng bấm rồi nhận lỗi."""
    return bool(api_key())


class RouterChatModel:
    """Gọi một endpoint kiểu OpenAI /chat/completions."""

    def __init__(self, model: str | None = None, url: str | None = None,
                 key: str | None = None, timeout: int = TIMEOUT) -> None:
        self._model = model or model_name()
        self._url = (url or base_url()).rstrip("/")
        self._key = key if key is not None else api_key()
        self._timeout = timeout

    @property
    def model_id(self) -> str:
        return self._model

    def __str__(self) -> str:
        return f"{self._model} @ {self._url}"

    # ------------------------------------------------------------------ gọi
    def complete(self, messages: Sequence[Message], max_tokens: int = 512,
                 temperature: float = 0.0) -> Reply:
        import requests

        if not self._key:
            raise LLMUnavailable(
                "Chưa có LLM_API_KEY. Lấy khoá trong dashboard 9router "
                "(http://localhost:20128) rồi thêm vào .env:\n"
                "    LLM_API_KEY=...\n"
                "Lưu ý: /v1/models không đòi khoá nhưng /v1/chat/completions thì "
                "có, nên endpoint trả 200 không có nghĩa là đã đủ cấu hình.")

        than = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content}
                         for m in messages],
        }
        try:
            res = requests.post(
                f"{self._url}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}",
                         "Content-Type": "application/json"},
                json=than, timeout=self._timeout)
        except requests.RequestException as exc:
            raise LLMUnavailable(
                f"Không gọi được {self._url}: {exc}\n"
                "Nếu đang chạy trong Docker, 9router nằm trên MÁY HOST nên phải "
                "là http://host.docker.internal:20128/v1, không phải localhost."
            ) from exc

        if res.status_code != 200:
            raise LLMUnavailable(
                f"{self._url} trả {res.status_code}: {res.text[:300]}")
        try:
            d = res.json()
            noi_dung = d["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMUnavailable(
                f"Trả về không đúng dạng OpenAI: {res.text[:300]}") from exc

        dung = d.get("usage") or {}
        return Reply(text=noi_dung or "", model=d.get("model") or self._model,
                     tokens_in=int(dung.get("prompt_tokens") or 0),
                     tokens_out=int(dung.get("completion_tokens") or 0))


def models(url: str | None = None, key: str | None = None) -> list[str]:
    """Danh sách model mà endpoint đang có. Dùng để kiểm cấu hình."""
    import requests
    u = (url or base_url()).rstrip("/")
    k = key if key is not None else api_key()
    try:
        res = requests.get(f"{u}/models", timeout=20, headers=(
            {"Authorization": f"Bearer {k}"} if k else {}))
        res.raise_for_status()
        return sorted(m.get("id", "") for m in res.json().get("data", []))
    except Exception as exc:                            # noqa: BLE001
        raise LLMUnavailable(f"Không đọc được {u}/models: {exc}") from exc


def chat_model(model: str | None = None) -> RouterChatModel:
    return RouterChatModel(model)
