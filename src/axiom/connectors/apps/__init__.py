from axiom.connectors.apps.factory import AppConnectorFactory
from axiom.connectors.apps.gmail import GMAIL_MANIFEST

AppConnectorFactory.register(GMAIL_MANIFEST)
