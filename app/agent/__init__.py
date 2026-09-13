"""Agent workflow exports."""

from app.agent.planner import IntentPlanner

__all__ = [
    "AgentWorkflow",
    "IntentPlanner",
]


def __getattr__(name: str):
    if name == "AgentWorkflow":
        from app.agent.workflow import AgentWorkflow

        return AgentWorkflow
    raise AttributeError(name)
