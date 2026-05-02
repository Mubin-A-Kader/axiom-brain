import json
import logging

import openai

from axiom.agent.state import GlobalAgentState
from axiom.config import settings

logger = logging.getLogger(__name__)

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
        history_context = state.get("history_context", "")

        import asyncpg
        from axiom.connectors.apps.factory import AppConnectorFactory

        agent_lines = []
        data_sources_desc = []
        
        # 1. Dynamically fetch connected structured data sources (Databases, Warehouses, Spreadsheets)
        try:
            app_names = {m.name for m in AppConnectorFactory.all_manifests()}
            conn = await asyncpg.connect(settings.database_url)
            try:
                rows = await conn.fetch(
                    "SELECT name, db_type, description FROM data_sources WHERE tenant_id = $1 AND status = 'active'",
                    tenant_id
                )
                for row in rows:
                    if row["db_type"] not in app_names:
                        desc = f"{row['name']} ({row['db_type']}): {row['description'] or 'Structured data'}"
                        data_sources_desc.append(desc)
            finally:
                await conn.close()
        except Exception as exc:
            logger.warning("Failed to load data sources for tenant '%s': %s", tenant_id, exc)

        if data_sources_desc:
            ds_list = "\n    - ".join(data_sources_desc)
            data_agent_desc = f"Core reasoning engine. Route all analytics, metrics, charts, and queries for these connected sources here:\n    - {ds_list}"
        else:
            data_agent_desc = "Core reasoning engine. Route all data, metrics, and tabular analysis questions here."

        agent_lines.append(f"- DATA_AGENT: {data_agent_desc}")

        # PLANNER_AGENT is available when there are 2+ data sources and the question
        # clearly needs to correlate or compare data across them.
        if len(data_sources_desc) >= 2:
            agent_lines.append(
                "- PLANNER_AGENT: Multi-step cross-source reasoning engine. "
                "Use this when the question explicitly requires querying MULTIPLE different data sources "
                "and correlating or comparing their results (e.g. 'compare our internal sales with market trends', "
                "'how does our retention compare to industry benchmarks', "
                "'what caused the drop — check both our DB and the market feed'). "
                "Do NOT use for single-source queries, even complex ones."
            )

        # 2. Dynamically fetch connected App Connectors (Gmail, Slack, etc.)
        try:
            connected_apps = await AppConnectorFactory.get_connected_for_tenant(tenant_id)
            for manifest in connected_apps:
                agent_lines.append(
                    f"- {manifest.name.upper()}_AGENT: {manifest.description}"
                )
        except Exception as exc:
            logger.warning("Could not load connected apps for tenant '%s': %s", tenant_id, exc)

        agent_list = "\n".join(agent_lines)
        from axiom.agent.prompt_registry import registry
        system_msg = registry.render_system("master_supervisor")
        context_msg = registry.render_context(
            "master_supervisor",
            agent_list=agent_list,
            history_context=history_context or "No prior history.",
            question=question
        )

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": context_msg}
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content.strip())
            next_agent = result.get("next_agent", "DATA_AGENT")
            logger.info("Supervisor routed query to: %s", next_agent)
            
            if next_agent == "AMBIGUOUS_AGENT":
                return {
                    "next_agent": next_agent,
                    "agent_thought": "Query intent is ambiguous based on available agents.",
                    "response_text": "I'm not sure which data source to use for this request. Could you clarify if you mean the database or a specific connected app?"
                }
            
            # Silence thought on clarifications to avoid UI clutter
            thought = f"Routing to {next_agent}."
            if "[" in question and "]" in question:
                thought = None

            return {"next_agent": next_agent, "agent_thought": thought}
        except Exception as exc:
            logger.warning("Supervisor routing failed: %s. Defaulting to DATA_AGENT.", exc)
            return {"next_agent": "DATA_AGENT"}
