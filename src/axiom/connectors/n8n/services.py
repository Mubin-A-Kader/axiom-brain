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
        # ── Spreadsheets ──────────────────────────────────────────────────
        ServiceDefinition(
            id="google_sheets",
            label="Google Sheets",
            icon="📊",
            category="spreadsheet",
            auth_type="oauth2",
            n8n_credential_type="googleSheetsOAuth2Api",
            description="Query any Google Sheet as a data source",
            oauth_scopes=[
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.readonly",
            ],
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            native_node=NativeNodeConfig(
                node_type="n8n-nodes-base.googleSheets",
                type_version=4,
                credential_key="googleSheetsOAuth2Api",
                base_params={
                    "resource": "sheet",
                    "operation": "read",
                    "options": {},
                },
                provision_fields=[
                    ProvisionField(
                        key="sheet_url",
                        label="Google Sheet URL",
                        placeholder="https://docs.google.com/spreadsheets/d/...",
                        extract_pattern=r"/spreadsheets/d/([a-zA-Z0-9_-]+)",
                        user_param_key="documentId",
                        user_param_template={"__rl": True, "mode": "id"}
                    ),
                    ProvisionField(
                        key="sheet_name",
                        label="Sheet Name (Tab)",
                        placeholder="Sheet1",
                        required=True,
                        user_param_key="sheetName",
                        user_param_template={"__rl": True, "mode": "name"}
                    )
                ]
            ),
        ),
        ServiceDefinition(
            id="airtable",
            label="Airtable",
            icon="🗂️",
            category="spreadsheet",
            auth_type="apikey",
            n8n_credential_type="airtableTokenApi",
            credential_fields=[
                CredentialField(key="accessToken", label="API Token", placeholder="pat...", secret=True),
            ],
            description="Query Airtable bases and tables",
            native_node=NativeNodeConfig(
                node_type="n8n-nodes-base.airtable",
                type_version=2,
                credential_key="airtableTokenApi",
                base_params={
                    "resource": "table",
                    "operation": "list",
                },
                provision_fields=[
                    ProvisionField(
                        key="base_id",
                        label="Base ID",
                        placeholder="app...",
                        required=True,
                        user_param_key="base",
                        user_param_template={"__rl": True, "mode": "id"}
                    ),
                    ProvisionField(
                        key="table_name",
                        label="Table Name",
                        placeholder="My Table",
                        required=True,
                        user_param_key="table",
                        user_param_template={"__rl": True, "mode": "id"}
                    )
                ]
            ),
        ),
        # ── CRM ───────────────────────────────────────────────────────────
        ServiceDefinition(
            id="salesforce",
            label="Salesforce",
            icon="☁️",
            category="crm",
            auth_type="oauth2",
            n8n_credential_type="salesforceOAuth2Api",
            description="Query Salesforce objects (Leads, Opportunities, Accounts...)",
            native_node=NativeNodeConfig(
                node_type="n8n-nodes-base.salesforce",
                type_version=4,
                credential_key="salesforceOAuth2Api",
                base_params={
                    "resource": "sobject",
                    "operation": "getAll",
                },
                provision_fields=[
                    ProvisionField(
                        key="object_name",
                        label="Salesforce Object",
                        placeholder="Lead",
                        required=True,
                        user_param_key="sobject",
                        user_param_template={"__rl": True, "mode": "id"}
                    )
                ]
            ),
        ),
        ServiceDefinition(
            id="hubspot",
            label="HubSpot",
            icon="🔶",
            category="crm",
            auth_type="apikey",
            n8n_credential_type="hubspotApi",
            credential_fields=[
                CredentialField(key="accessToken", label="Private App Token", placeholder="pat-na1-...", secret=True),
            ],
            description="Query HubSpot contacts, deals, and companies",
        ),
        # ── Productivity ──────────────────────────────────────────────────
        ServiceDefinition(
            id="notion",
            label="Notion",
            icon="📝",
            category="productivity",
            auth_type="apikey",
            n8n_credential_type="notionApi",
            credential_fields=[
                CredentialField(key="apiKey", label="Integration Token", placeholder="secret_...", secret=True),
            ],
            description="Query Notion databases",
        ),
        ServiceDefinition(
            id="slack",
            label="Slack",
            icon="💬",
            category="productivity",
            auth_type="oauth2",
            n8n_credential_type="slackOAuth2Api",
            description="Query Slack channels and messages",
        ),
        # ── Finance ───────────────────────────────────────────────────────
        ServiceDefinition(
            id="stripe",
            label="Stripe",
            icon="💳",
            category="finance",
            auth_type="apikey",
            n8n_credential_type="stripeApi",
            credential_fields=[
                CredentialField(key="secretKey", label="Secret Key", placeholder="sk_live_...", secret=True),
            ],
            description="Query Stripe payments, customers, and subscriptions",
        ),
        # ── Dev tools ─────────────────────────────────────────────────────
        ServiceDefinition(
            id="github",
            label="GitHub",
            icon="🐙",
            category="dev",
            auth_type="apikey",
            n8n_credential_type="githubApi",
            credential_fields=[
                CredentialField(key="accessToken", label="Personal Access Token", placeholder="ghp_...", secret=True),
            ],
            description="Query GitHub repos, issues, and pull requests",
        ),
        # ── Generic ───────────────────────────────────────────────────────
        ServiceDefinition(
            id="http_api",
            label="Custom REST API",
            icon="🔌",
            category="generic",
            auth_type="bearer",
            n8n_credential_type="httpHeaderAuth",
            credential_fields=[
                CredentialField(key="name", label="Header Name", placeholder="Authorization"),
                CredentialField(key="value", label="Header Value", placeholder="Bearer <token>", secret=True),
            ],
            description="Connect any REST API that returns JSON",
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
        "salesforce":    {"clientId": settings.salesforce_oauth_client_id, "clientSecret": settings.salesforce_oauth_client_secret},
        "slack":         {"clientId": settings.slack_oauth_client_id, "clientSecret": settings.slack_oauth_client_secret},
    }
    return _map.get(service_id, {})
