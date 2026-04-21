import logging
from typing import Dict, Any, Optional
from axiom.config import settings

logger = logging.getLogger(__name__)

class AdaptiveInferenceManager:
    """
    Manages Dynamic Temperature Scaling (DTS) and entropy control 
    based on agent state and task complexity.
    """
    
    # State-to-Temperature Mapping
    STATE_MAP = {
        "routing": 0.4,       # Wide-sampling to find all possible table candidates
        "planning": 0.0,      # Deterministic intent decomposition
        "generation": 0.0,    # High precision SQL production
        "critic": 0.2,        # Focused correction with slight variability
        "discovery_l1": 0.4,  # Fuzzy matching & synonym search
        "discovery_l2": 0.7,  # Semantic grep & EAV pattern recognition
        "discovery_l3": 0.9,  # Speculative schema mapping (last resort)
        "synthesis": 0.7      # Conversational and creative summary
    }

    @classmethod
    def get_parameters(cls, node_name: str, attempt: int = 0, error_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Returns the optimal LLM parameters (temperature, max_tokens, stop_sequences)
        based on the current node and failure context.
        """
        # Default base temperature
        temp = cls.STATE_MAP.get(node_name, settings.llm_temperature)
        
        # Scaling Logic: Increase entropy if we are stuck in a retry loop
        if node_name == "discovery" or node_name == "critic":
            # Temperature Stepping: 0.4 -> 0.6 -> 0.8
            scaling_factor = min(attempt * 0.2, 0.5)
            temp = min(temp + scaling_factor, 1.0)
            
        # Specific overrides for empty result sets
        if error_type == "ZERO_RESULTS" and node_name == "discovery":
            temp = max(temp, 0.7) # Force high-entropy for "hunting" data

        logger.debug(f"DTS Scaling: Node={node_name}, Attempt={attempt}, Temp={temp}")
        
        return {
            "temperature": temp,
            "max_tokens": 1000 if node_name == "discovery" else 500,
            "response_format": {"type": "json_object"} if node_name in ["planning", "discovery"] else None
        }

    @classmethod
    def get_system_override(cls, node_name: str) -> Optional[str]:
        """
        Provides specialized system instructions to control model behavior 
        during high-temperature discovery phases.
        """
        if node_name == "discovery":
            return """You are in DETECTIVE MODE. Your goal is to speculate and explore.
            The standard schema lookup failed. You must:
            1. Think laterally about where data might be hiding (JSONB columns, Meta tables).
            2. Propose synonyms for user terms.
            3. BE SPECULATIVE but ground your findings in the INFORMATION_SCHEMA provided."""
        
        if node_name == "generation":
            return "You are a PRECISE SQL ENGINEER. Accuracy is everything. Do not hallucinate columns."
            
        return None
