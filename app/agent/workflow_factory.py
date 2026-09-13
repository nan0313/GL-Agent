from app.agent.workflow import AgentWorkflow


def get_agent_workflow(provider: str = "python"):
    if provider == "python":
        return AgentWorkflow()
    if provider == "langgraph":
        from app.agent.langgraph_workflow import LangGraphAgentWorkflow

        return LangGraphAgentWorkflow()
    raise NotImplementedError(f"Unsupported agent workflow provider: {provider}")
