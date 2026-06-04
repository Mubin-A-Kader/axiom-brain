"""Conversation and message schemas."""

from axiom.schemas import APIModel


class MessageOut(APIModel):
    id: str
    role: str
    content: str


class ConversationOut(APIModel):
    id: str
    title: str | None
    principal_id: str


class CreateConversationRequest(APIModel):
    title: str | None = None


class AddMessageRequest(APIModel):
    role: str = "user"
    content: str
