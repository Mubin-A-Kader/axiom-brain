import json

_WORKFLOW_TEMPLATE = {
    "name": "",
    "active": True,
    "nodes": [
        {
            "id": "webhook-node",
            "name": "Webhook",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 1.1,
            "position": [0, 0],
            "parameters": {
                "httpMethod": "POST",
                "path": "",
                "responseMode": "responseNode",
                "options": {},
            },
            "webhookId": "",
        },
        {
            "id": "fetch-node",
            "name": "Fetch Data",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [240, 0],
            "parameters": {
                "method": "={{ $json.body.method || 'GET' }}",
                "url": "={{ $json.body.url }}",
                "sendQuery": True,
                "specifyQuery": "json",
                "jsonQuery": "={{ JSON.stringify($json.body.query || {}) }}",
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($json.body.payload || {}) }}",
                "authentication": "predefinedCredentialType",
                "nodeCredentialType": "",
                "options": {},
            },
            "credentials": {},
        },
        {
            "id": "respond-node",
            "name": "Respond",
            "type": "n8n-nodes-base.respondToWebhook",
            "typeVersion": 1,
            "position": [480, 0],
            "parameters": {
                "respondWith": "json",
                "responseBody": "={{ JSON.stringify($input.all().map(i => i.json)) }}",
                "options": {},
            },
        },
    ],
    "connections": {
        "Webhook": {"main": [[{"node": "Fetch Data", "type": "main", "index": 0}]]},
        "Fetch Data": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
    },
    "settings": {"executionOrder": "v1"},
}

with open('src/axiom/connectors/n8n/client.py', 'r') as f:
    content = f.read()

# Replace the old _WORKFLOW_TEMPLATE block. We can do this safely via string manipulation or just write a replace script.
