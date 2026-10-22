"""A pluggable, bounded LLM adapter: :class:`MockAdapter` for CI and local dev with no
key, :class:`GroqAdapter` for live use. Nothing in this package calls a model
unprompted, holds a conversation, or retries on its own — see
``services/research_agent`` for the bounded-run logic built on top of it.
"""

from alphadots_llm.adapter import ChatMessage, ChatResult, LlmAdapter, LlmError, LlmErrorReason
from alphadots_llm.groq import GroqAdapter
from alphadots_llm.mock import MockAdapter
from alphadots_llm.pricing import TokenRate, estimate_cost_usd

__all__ = [
    "ChatMessage",
    "ChatResult",
    "GroqAdapter",
    "LlmAdapter",
    "LlmError",
    "LlmErrorReason",
    "MockAdapter",
    "TokenRate",
    "estimate_cost_usd",
]
