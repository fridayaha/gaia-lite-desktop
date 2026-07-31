"""Prometheus metrics definitions and endpoint.

Tracks per-layer operation latency, error rates, and status codes.
Uses prometheus_client for metric exposition.
"""

from prometheus_client import Counter, Histogram, generate_latest

# ── Layer Call Metrics ──

layer_call_duration = Histogram(
    name="ontology_layer_call_duration_seconds",
    documentation="Duration of layer method calls in seconds",
    labelnames=["layer", "method", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

layer_call_total = Counter(
    name="ontology_layer_call_total",
    documentation="Total number of layer method calls",
    labelnames=["layer", "method", "status"],
)

# ── Query Metrics ──

query_duration = Histogram(
    name="ontology_query_duration_seconds",
    documentation="Query execution duration in seconds",
    labelnames=["query_type", "storage_type"],
    buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
)

query_total = Counter(
    name="ontology_query_total",
    documentation="Total number of queries",
    labelnames=["query_type", "storage_type", "status"],
)

# ── Index Acceleration Metrics ──
# Tracks whether the Doris index path is actually used or bypassed.
# ``reason`` distinguishes the fallback cause so operators can tell a
# misconfigured ObjectType (not_built) from a Doris outage (doris_down).
object_query_index_hit_total = Counter(
    name="ontology_object_query_index_hit_total",
    documentation="Object queries that used the Doris index path",
    labelnames=["object_type"],
)

object_query_fallback_total = Counter(
    name="ontology_object_query_fallback_total",
    documentation="Object queries that fell back from the Doris index path to Trino",
    labelnames=["object_type", "reason"],  # reason: not_built | doris_down | no_filter | empty_result
)

# ── HTTP Metrics ──

http_request_duration = Histogram(
    name="ontology_http_request_duration_seconds",
    documentation="HTTP request duration in seconds",
    labelnames=["method", "path", "status"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

http_request_total = Counter(
    name="ontology_http_request_total",
    documentation="Total number of HTTP requests",
    labelnames=["method", "path", "status"],
)


def metrics_endpoint() -> bytes:
    """Generate Prometheus-formatted metrics output."""
    return generate_latest()
