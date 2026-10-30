"""Adapter: OpenAI-compatible model servers (vLLM), probed and called with httpx."""

from .openai import OpenAiModels

__all__ = ["OpenAiModels"]
