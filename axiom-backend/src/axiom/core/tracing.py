"""OpenTelemetry tracing.

Auto-instruments FastAPI, SQLAlchemy, and Redis. Spans are exported
via OTLP gRPC — point ``OTEL_EXPORTER_OTLP_ENDPOINT`` at any collector
(Jaeger, Tempo, Honeycomb, Datadog OTel agent).

To create a custom span inside business logic::

    from axiom.core.tracing import tracer
    with tracer.start_as_current_span("rag.retrieve") as span:
        span.set_attribute("rag.top_k", k)
        ...
"""

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from axiom.config import get_settings

tracer = trace.get_tracer("axiom")


def setup_tracing(app: FastAPI) -> None:
    settings = get_settings()
    if not settings.otel_exporter_otlp_endpoint:
        return

    resource = Resource.create({SERVICE_NAME: settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
    SQLAlchemyInstrumentor().instrument()
    RedisInstrumentor().instrument()
