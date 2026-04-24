import hashlib
import os

class LLMTagging:
    """
    Implements cryptographically robust tagging for untrusted data.
    Allows downstream agents to differentiate between system instructions and external data.
    """
    
    @staticmethod
    def wrap_untrusted(data: str, source: str = "external") -> str:
        """
        Wraps untrusted data in semantic XML-like delimiters with a unique hash.
        """
        if not data:
            return ""
            
        # Generate a unique salt for this tag session to prevent predictable bypassing
        nonce = hashlib.sha256(os.urandom(16)).hexdigest()[:8]
        
        return f"""
<untrusted_data source="{source}" nonce="{nonce}">
{data}
</untrusted_data>
""".strip()

    @staticmethod
    def wrap_schema(ddl: str) -> str:
        return LLMTagging.wrap_untrusted(ddl, source="database_schema")

    @staticmethod
    def wrap_query_result(result: str) -> str:
        return LLMTagging.wrap_untrusted(result, source="database_query_result")
