import logging
import json
import openai
from typing import Dict, Any

from axiom.agent.state import SQLAgentState
from axiom.config import settings

logger = logging.getLogger(__name__)

class ProblemDefinitionNode:
    """Converts user query into a precise problem statement."""
    def __init__(self) -> None:
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> Dict[str, Any]:
        question = state["question"]
        
        prompt = f"""You are a Senior Site Reliability Engineer (SRE) and Data Scientist.
Your task is to convert the user's issue into a precise problem formulation.
Do NOT attempt to solve it yet.

User Query: "{question}"

Output EXACTLY in this format:
WHAT IS WRONG: <description>
EXPECTED: <what should happen>
DEVIATION: <the difference between expected and actual>"""

        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        problem_statement = response.choices[0].message.content.strip()
        logger.info(f"Problem Formulation:\n{problem_statement}")
        return {"problem_statement": problem_statement}


class HypothesisGenerationNode:
    """Generates 3-5 distinct, testable hypotheses based on the problem statement and current evidence."""
    def __init__(self) -> None:
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> Dict[str, Any]:
        problem = state.get("problem_statement", state["question"])
        existing_log = "\n".join(state.get("investigation_log", []))
        
        prompt = f"""You are a Senior Investigator performing Root Cause Analysis.
Problem Statement:
{problem}

Past Investigation Steps / Evidence:
{existing_log if existing_log else "None so far."}

Generate 3-5 distinct, mutually exclusive hypotheses that could explain this issue.
Focus on system-level causes (e.g., config change, pipeline failure, traffic shift).
Do NOT output anything except a JSON list of strings representing the hypotheses.
Example: ["A recent deployment broke the auth service", "A data pipeline failed causing missing records"]
"""

        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            response_format={ "type": "json_object" }
        )
        
        try:
            content = response.choices[0].message.content
            # Handle potential dictionary wrapping from JSON object requirement
            parsed = json.loads(content)
            if isinstance(parsed, dict) and len(parsed.keys()) == 1:
                hypotheses = list(parsed.values())[0]
            elif isinstance(parsed, list):
                hypotheses = parsed
            else:
                hypotheses = [str(parsed)]
        except Exception as e:
            logger.error(f"Failed to parse hypotheses: {e}")
            hypotheses = ["Unknown error occurred during hypothesis generation."]

        logger.info(f"Generated Hypotheses: {hypotheses}")
        return {"hypotheses": hypotheses}


class InvestigationLoopNode:
    """Acts as a ReAct agent. Decides to formulate a SQL query to fetch data, or synthesize findings."""
    def __init__(self) -> None:
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    _MAX_INVESTIGATION_ATTEMPTS = 5

    async def __call__(self, state: SQLAgentState) -> Dict[str, Any]:
        problem = state.get("problem_statement", state["question"])
        hypotheses = state.get("hypotheses", [])
        log = state.get("investigation_log", [])
        schema_context = state.get("schema_context", "")
        sql_result = state.get("sql_result")
        sql_error = state.get("error")
        sql_query = state.get("sql_query")
        investigation_attempts = state.get("investigation_attempts", 0) + 1

        # Track the last real SQL result so the notebook builder can use it after
        # action_plan clears sql_result to None
        last_sql_result = state.get("last_sql_result")
        if sql_result and sql_result not in ("CONCLUDED",):
            last_sql_result = sql_result

        # If we just executed a query, log it
        if sql_query and (sql_result or sql_error):
            log_entry = f"Executed Query: {sql_query}\nResult: {sql_result if sql_result else sql_error}"
            if log_entry not in log:
                log.append(log_entry)
        elif sql_error and not sql_query:
            # SQL Generation completely failed (e.g. semantic impossibility)
            log_entry = f"Failed to generate SQL. Error: {sql_error}"
            if log_entry not in log:
                log.append(log_entry)

        # Hard cap: prevent infinite recursion when the schema can't answer the question
        if investigation_attempts > self._MAX_INVESTIGATION_ATTEMPTS:
            logger.warning(
                "Investigation loop exceeded max attempts (%d). Forcing conclude.",
                self._MAX_INVESTIGATION_ATTEMPTS,
            )
            return {
                "root_cause_confidence": "Low",
                "investigation_log": log,
                "investigation_attempts": investigation_attempts,
                "last_sql_result": last_sql_result,
                "error": None,
                "sql_result": "CONCLUDED",
            }

        # Detect repeated SQL-generation failures — schema likely can't answer the question
        generation_failures = sum(1 for e in log if e.startswith("Failed to generate SQL"))

        # Detect permission denials — unresolvable without DBA intervention, force conclude
        permission_errors = sum(1 for e in log if "permission denied" in e.lower())
        if permission_errors >= 2:
            logger.warning("Investigation loop encountered repeated permission denials — forcing conclude.")
            return {
                "root_cause_confidence": "Low",
                "investigation_log": log,
                "investigation_attempts": investigation_attempts,
                "last_sql_result": last_sql_result,
                "error": None,
                "sql_result": "CONCLUDED",
                "response_text": (
                    "I was unable to retrieve the data because the database user lacks SELECT "
                    "permission on the required tables. Please ask your DBA to grant access and re-run."
                ),
            }

        generation_failure_note = (
            "\nNOTE: SQL generation has failed. The schema may not contain data to answer "
            "this question analytically. If you cannot reformulate meaningfully, choose 'conclude'."
            if generation_failures > 0 else ""
        )

        no_prior_queries = not any(e.startswith("Executed Query") for e in log)
        must_investigate = no_prior_queries and schema_context
        must_investigate_note = (
            "\n\nIMPORTANT: You have NOT run any queries yet and schema is available. "
            "You MUST choose 'sql_query' to gather evidence before you can conclude."
            if must_investigate else ""
        )

        prompt = f"""You are investigating a critical issue.
Problem: {problem}
Current Hypotheses: {hypotheses}
Schema Context: {schema_context}

Past Actions & Evidence:
{chr(10).join(log) if log else 'None.'}{generation_failure_note}{must_investigate_note}

Determine your next action. You can either:
1. Formulate a SQL query to test a hypothesis (fetch time series, anomalies, segmentations). DO NOT REPEAT FAILED QUERIES.
2. Conclude the investigation if you have gathered enough evidence or the schema cannot support further queries.

Output a JSON object with this schema:
{{
    "action": "sql_query" or "conclude",
    "reasoning": "Why you are doing this",
    "sql": "The SQL query to run (only if action is sql_query)",
    "confidence": "High/Medium/Low (only if action is conclude)",
    "root_cause_summary": "Brief summary (only if action is conclude)"
}}
"""
        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={ "type": "json_object" }
        )

        try:
            decision = json.loads(response.choices[0].message.content)
            action = decision.get("action")

            # Hard-enforce: never conclude before running at least one query when schema exists
            if action != "sql_query" and must_investigate:
                logger.warning("InvestigationLoop tried to conclude on first attempt with schema — forcing sql_query")
                action = "sql_query"
                # Build a minimal sampling query from the first table in schema context
                first_table = next(
                    (line.split()[-1] for line in schema_context.splitlines() if "CREATE TABLE" in line.upper()),
                    None
                )
                if first_table and not decision.get("sql"):
                    decision["sql"] = f"SELECT * FROM {first_table} LIMIT 10"

            if action == "sql_query":
                # Clear previous error/result/attempts so the execution node runs cleanly
                return {
                    "sql_query": decision.get("sql"),
                    "investigation_log": log,
                    "investigation_attempts": investigation_attempts,
                    "error": None,
                    "sql_result": None,
                    "attempts": 0,
                    "root_cause_confidence": "Low",
                }
            else:
                return {
                    "root_cause_confidence": decision.get("confidence", "Medium"),
                    "investigation_log": log,
                    "investigation_attempts": investigation_attempts,
                    "last_sql_result": last_sql_result,
                    "error": None,  # Prevent correction loop
                    "sql_result": "CONCLUDED",
                }
        except Exception as e:
            logger.error(f"Investigation Loop Error: {e}")
            return {
                "root_cause_confidence": "Low",
                "investigation_log": log,
                "investigation_attempts": investigation_attempts,
            }


class ActionPlanNode:
    """Synthesizes the final 9-step structured RCA report."""
    def __init__(self) -> None:
        self._client = openai.AsyncOpenAI(
            base_url=f"{settings.litellm_url}/v1",
            api_key=settings.litellm_key,
        )

    async def __call__(self, state: SQLAgentState) -> Dict[str, Any]:
        problem = state.get("problem_statement", state["question"])
        log = state.get("investigation_log", [])
        confidence = state.get("root_cause_confidence", "Medium")
        
        prompt = f"""You are a Senior SRE writing a final Root Cause Analysis (RCA) report.

Problem: {problem}
Investigation Evidence:
{chr(10).join(log)}

Write the final report EXACTLY in this Markdown format. Do not deviate.

## 📌 Problem
<clear problem statement>

## 🔍 Key Findings
* <insight 1>
* <insight 2>

## 🧠 Root Cause
* Primary: <the root cause>
* Supporting factors: <factors>

## 📊 Evidence
* <Data-backed observations from the investigation log>

## 🎯 Confidence
* Root cause confidence: {confidence}

## 🚀 Recommended Actions

### Immediate
* <what to do now>

### Preventative
* <how to avoid recurrence>

### Optimization
* <system improvements>
"""
        response = await self._client.chat.completions.create(
            model=state.get("llm_model") or settings.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        
        report = response.choices[0].message.content.strip()
        
        # In the context of the Axiom Brain UI, we store this in response_text or build a markdown artifact
        return {
            "response_text": report,
            "rca_report": {"markdown": report},
            "sql_result": None,  # Clear sentinel so downstream nodes don't try to parse it
        }
