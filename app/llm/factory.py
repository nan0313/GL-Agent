from app.llm.base import BaseLLMPlanner
from app.llm.lcel_mock_planner import LCELMockPlanner
from app.llm.mock_llm import MockLLMPlanner
from app.llm.qwen_planner import QwenPlanner


def get_llm_planner(provider: str = "mock") -> BaseLLMPlanner:
    if provider == "mock":
        return MockLLMPlanner()
    if provider == "lcel_mock":
        return LCELMockPlanner()
    if provider == "qwen":
        return QwenPlanner()
    if provider == "dawatt":
        raise NotImplementedError(f"LLM planner provider is not implemented yet: {provider}")

    raise NotImplementedError(f"LLM planner provider is not implemented yet: {provider}")
