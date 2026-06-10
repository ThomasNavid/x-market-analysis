"""Provider-agnostic LLM JSON-completion layer (Anthropic cloud, Ollama local)."""

from findb.core.llm.base import LLMError, LLMResponseError, ModelRef
from findb.core.llm.json_utils import extract_json_object
from findb.core.llm.router import create_json_completion

__all__ = [
    "LLMError",
    "LLMResponseError",
    "ModelRef",
    "create_json_completion",
    "extract_json_object",
]
