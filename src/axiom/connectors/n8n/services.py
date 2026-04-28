"""
Service catalog — defines every third-party service Axiom can connect via n8n.
Adding a new service = add one entry here + build the n8n workflow in n8n UI.
No Python connector code needed per service.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional


AuthType = Literal["apikey", "oauth2", "basic", "bearer"]


@dataclass
class CredentialField:
    key: str
    label: str
    placeholder: str = ""
    secret: bool = False


@dataclass
class ProvisionField:
    """
    Describes a field the user must fill in at provision time (e.g. a sheet URL).
    Declared on NativeNodeConfig so the API and UI are driven entirely by data —
    no per-service if-branches needed anywhere outside this file.
    """
    key: str                    # form field key sent in provision_data, e.g. "sheet_url"
    label: str                  # UI label shown in the wizard
    placeholder: str = ""
    required: bool = True
    # Optional regex applied to the raw input to extract the real value
    # (e.g. pull the spreadsheet ID out of a full Google Sheets URL)
    extract_pattern: str = ""
    # Which key in user_params to set with the extracted value
    user_param_key: str = ""
    # Template dict merged with {"value": <extracted>} to form the user_param value
    user_param_template: dict = field(default_factory=dict)


@dataclass
class NativeNodeConfig:
    """
    Describes the native n8n node to use for a service instead of the
    generic httpRequest fallback.  The provisioning code will build a
    workflow with this node type wired between Webhook and Respond.
    """
    node_type: str          # e.g. "n8n-nodes-base.googleSheets"
    type_version: float     # e.g. 4
    credential_key: str     # key used in the credentials dict (often == n8n_credential_type)
    base_params: dict       # fixed node parameters (resource, operation, options, …)
    # Provision-time fields the user must fill in; drives both API validation and UI rendering
    provision_fields: List[ProvisionField] = field(default_factory=list)


@dataclass
class ServiceDefinition:
    id: str                          # e.g. "google_sheets"
    label: str                       # e.g. "Google Sheets"
    icon: str                        # emoji or icon name
    category: str                    # "spreadsheet", "crm", "database", etc.
    auth_type: AuthType
    n8n_credential_type: str         # n8n's internal credential type name
    credential_fields: List[CredentialField] = field(default_factory=list)
    description: str = ""
    # For oauth2 services with restricted scopes: Axiom builds the URL directly
    # instead of delegating to n8n, so n8n's hard-coded scopes are bypassed.
    oauth_scopes: List[str] = field(default_factory=list)
    auth_url: str = ""          # OAuth authorization endpoint
    token_url: str = ""         # OAuth token exchange endpoint
    # When set, the provisioning step builds a workflow using the native n8n node
    # instead of the generic httpRequest fallback.
    native_node: Optional[NativeNodeConfig] = None


SERVICES: Dict[str, ServiceDefinition] = {
    s.id: s for s in [
        ServiceDefinition(
            id="google_sheets",
            label="Google Sheets",
            icon="https://cdn.simpleicons.org/googlesheets/009688",
            category="spreadsheet",
            auth_type="oauth2",
            n8n_credential_type="googleSheetsOAuth2Api",
            description="Query any Google Sheet. Use the Google Drive API (https://www.googleapis.com/drive/v3/files?q=mimeType='application/vnd.google-apps.spreadsheet') to search for a sheet's ID. When searching by name, ALWAYS use the `contains` operator instead of `=` (e.g. `name contains 'settlement'`) to handle typos and partial matches. Then use the Google Sheets API (https://sheets.googleapis.com/v4/spreadsheets/{id} and /values/{range}) to read its data.",
            oauth_scopes=[
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.readonly",
            ],
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
        ),
        ServiceDefinition(
            id="notion",
            label="Notion",
            icon="https://cdn.simpleicons.org/notion/000000",
            category="productivity",
            auth_type="apikey",
            n8n_credential_type="notionApi",
            credential_fields=[
                CredentialField(key="apiKey", label="Integration Token", placeholder="secret_...", secret=True),
            ],
            description="Query Notion databases and pages.",
        ),
        ServiceDefinition(
            id="gmail",
            label="Gmail",
            icon="https://cdn.simpleicons.org/gmail/EA4335",
            category="productivity",
            auth_type="oauth2",
            n8n_credential_type="gmailOAuth2",
            description="Query and read Gmail messages and threads. Use the Gmail API (https://gmail.googleapis.com/gmail/v1/users/me/messages) to search. CRITICAL: When calling `messages.get` for specific emails, ALWAYS append `?format=metadata&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Subject&metadataHeaders=Date` to the URL. Never request the full `RAW` or `FULL` email body format because it will crash the system due to token limits.",
            oauth_scopes=[
                "https://www.googleapis.com/auth/gmail.readonly",
            ],
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
        ),
        ServiceDefinition(
            id="whatsapp",
            label="WhatsApp",
            icon="https://cdn.simpleicons.org/whatsapp/25D366",
            category="communication",
            auth_type="bearer",
            n8n_credential_type="whatsAppApi",
            credential_fields=[
                CredentialField(key="accessToken", label="Access Token", placeholder="EA...", secret=True),
            ],
            description="Send messages and query WhatsApp Business Cloud API.",
        ),
    ]
}


def get_service(service_id: str) -> ServiceDefinition:
    if service_id not in SERVICES:
        raise ValueError(f"Unknown service: {service_id}. Available: {list(SERVICES)}")
    return SERVICES[service_id]


def services_by_category() -> Dict[str, List[ServiceDefinition]]:
    result: Dict[str, List[ServiceDefinition]] = {}
    for svc in SERVICES.values():
        result.setdefault(svc.category, []).append(svc)
    return result


def get_platform_oauth_credentials(service_id: str) -> Dict[str, str]:
    """
    Returns the platform-level OAuth client credentials for a service.
    These are set once in .env by the platform operator — end users never see them.
    """
    from axiom.config import settings
    _map: Dict[str, Dict[str, str]] = {
        "google_sheets": {"clientId": settings.google_oauth_client_id, "clientSecret": settings.google_oauth_client_secret},
        "gmail":         {"clientId": settings.google_oauth_client_id, "clientSecret": settings.google_oauth_client_secret},
    }
    return _map.get(service_id, {})
