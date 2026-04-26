import json
import logging

import openai

from axiom.agent.state import GlobalAgentState
from axiom.config import settings

logger = logging.getLogger(__name__)

_SQL_AGENT_ENTRY = (
    "SQL_AGENT",
    "Query databases, run analytics, build charts, perform root-cause analysis. "
    "Route all data and metrics questions here.",
)


class SupervisorNode:
    """Classifies user intent and routes to the appropriate domain sub-graph."""

    def __init__(self) -> None:
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: GlobalAgentState) -> dict:
        question = state.get("question", "")
        tenant_id = state.get("tenant_id", "")

        # Build the agent list dynamically from connected apps + SQL_AGENT
        agent_lines = [f"- {_SQL_AGENT_ENTRY[0]}: {_SQL_AGENT_ENTRY[1]}"]

        try:
            from axiom.connectors.apps.factory import AppConnectorFactory
            connected = await AppConnectorFactory.get_connected_for_tenant(tenant_id)
            for manifest in connected:
                agent_lines.append(
                    f"- {manifest.name.upper()}_AGENT: {manifest.description}"
                )
        except Exception as exc:
            logger.warning("Could not load connected apps for tenant '%s': %s", tenant_id, exc)

        # User-defined agents (phase 2 — reads user_agents table when implemented)
        # user_agents = await UserAgentStore.list(tenant_id)
        # for ua in user_agents:
        #     agent_lines.append(f"- {ua.name.upper()}_AGENT: {ua.description}")

        agent_list = "\n".join(agent_lines)
        prompt = f"""You are the Master Orchestrator for Axiom Brain.
Route the user query to the most appropriate agent.

### AVAILABLE AGENTS:
{agent_list}

### USER QUERY:
"{question}"

### INSTRUCTIONS:
Respond strictly with valid JSON:
{{"next_agent": "<AGENT_NAME>"}}

Use exactly the agent name shown above (e.g. "SQL_AGENT", "GMAIL_AGENT").
Default to SQL_AGENT when the query is ambiguous."""

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content.strip())
            next_agent = result.get("next_agent", "SQL_AGENT")
            logger.info("Supervisor routed query to: %s", next_agent)
            return {"next_agent": next_agent, "agent_thought": f"Routing to {next_agent}."}
        except Exception as exc:
            logger.warning("Supervisor routing failed: %s. Defaulting to SQL_AGENT.", exc)
            return {"next_agent": "SQL_AGENT"}
