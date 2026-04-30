"""Prometheus instrumentation. Names chosen so AutoFixOps' rule engine
recognizes the resulting alerts (HighErrorRate, SlowResponseTime, etc.)."""

from prometheus_client import Counter, Histogram, Gauge

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests by route, method, and status.",
    labelnames=("route", "method", "status"),
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "Latency in seconds by route.",
    labelnames=("route",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

shortener_links_total = Counter(
    "shortener_links_total",
    "Number of new short links created.",
)

shortener_clicks_total = Counter(
    "shortener_clicks_total",
    "Number of redirect lookups served.",
    labelnames=("hit",),  # cache_hit | cache_miss
)

cache_size_bytes = Gauge(
    "cache_size_bytes",
    "Approximate working-set size of the in-process cache (only used by chaos endpoints).",
)
