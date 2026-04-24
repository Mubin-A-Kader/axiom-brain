import uuid
import hashlib
import time
from typing import Dict, Any, Optional

class AgentNamingService:
    """
    Generates and manages Decentralized Identifiers (DIDs) for agents and sessions.
    Follows a basic did:axiom format.
    """
    
    @staticmethod
    def generate_session_did(tenant_id: str, session_id: str) -> str:
        """did:axiom:tenant:{tenant_id}:session:{session_id}"""
        return f"did:axiom:tenant:{tenant_id}:session:{session_id}"

    @staticmethod
    def generate_agent_did(agent_name: str, session_did: str) -> str:
        """did:axiom:agent:{agent_name}:parent:{hash(session_did)}"""
        parent_hash = hashlib.sha256(session_did.encode()).hexdigest()[:12]
        return f"did:axiom:agent:{agent_name}:parent:{parent_hash}"

    @staticmethod
    def generate_verifiable_credential(did: str, capabilities: list[str]) -> Dict[str, Any]:
        """
        Creates a basic verifiable credential (VC) for an agent.
        In a production system, this would be signed with a private key.
        """
        return {
            "id": f"vc:axiom:{uuid.uuid4()}",
            "issuer": "did:axiom:orchestrator",
            "subject": did,
            "issuance_date": time.time(),
            "capabilities": capabilities,
            "proof": {
                "type": "Ed25519Signature2018",
                "signature": "..." # Mock signature for this phase
            }
        }
