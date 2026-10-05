"""Tầng gọi model ngôn ngữ: cổng + adapter. Xem `ports.py` cho hợp đồng."""

from .ports import ChatModel, LLMUnavailable, Message, Reply

__all__ = ["ChatModel", "LLMUnavailable", "Message", "Reply"]
