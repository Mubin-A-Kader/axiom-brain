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
        prompt = f"""You are the Master Orchestrator for Axiom Brain.
Route the user query to the most appropriate agent.

### AVAILABLE AGENTS:
{agent_list}

### CONVERSATION HISTORY (Context):
{history_context if history_context else "No prior history."}

### USER QUERY:
"{question}"

### INSTRUCTIONS:
Respond strictly with valid JSON:
{{"next_agent": "<AGENT_NAME>"}}

1. ANALYZE intent: Look for keywords (sheets, email, slack, database, metrics).
2. REFINEMENT DETECTION (CRITICAL): If the user asks for a "chart", "plot", "graph", "visualization", "summary", "breakdown", or "detailed analysis" based on results they *just saw*, you MUST stay with the agent that provided those results. 
   - Keywords to stay: "this", "it", "those", "that summary", "above", "result".
   - Example: If history shows GMAIL_AGENT and user says "can u a graph from this", you MUST stay on GMAIL_AGENT.
   - Example: If history shows DATA_AGENT and user says "break it down", you MUST stay on DATA_AGENT.
3. TOPIC SWITCHING: If the user explicitly mentions a NEW topic (e.g. "What is our revenue?"), switch to the corresponding agent (e.g. DATA_AGENT).
4. AMBIGUITY: Only output "AMBIGUOUS_AGENT" if there is NO recent history context or if the user explicitly asks a question that spans multiple unconnected domains without referring to the previous turn. 
5. Do NOT arbitrarily default to DATA_AGENT for visualization requests if the data originated from an App Agent (Gmail, Slack, etc.). App agents can build their own notebooks now.
6. Use exactly the agent name shown above. 
7. Do NOT stay on the GMAIL_AGENT if the user explicitly asks about a database or spreadsheet.
"""

        try:
            response = await self._client.chat.completions.create(
                model=state.get("llm_model") or settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
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
                
            return {"next_agent": next_agent, "agent_thought": f"Routing to {next_agent}."}
        except Exception as exc:
            logger.warning("Supervisor routing failed: %s. Defaulting to DATA_AGENT.", exc)
            return {"next_agent": "DATA_AGENT"}
