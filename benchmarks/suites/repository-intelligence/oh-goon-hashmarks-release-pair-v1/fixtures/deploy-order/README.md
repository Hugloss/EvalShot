# Deployment ordering

Repair this standard-library deployment manifest tool.

`deploy_order.graph.order_services(dependencies)` accepts a dictionary mapping
nonempty service names to lists of dependency names. Validate the entire manifest:
keys and dependency names must be nonempty strings, values must be lists, every
dependency must be declared, and cycles (including self cycles) are invalid.
Invalid manifests raise ValueError. Duplicate dependency edges are allowed and
mean one prerequisite. Empty manifests produce []. Do not mutate any input.

Return every service exactly once, after its dependencies. At each selection,
choose the alphabetically smallest currently ready service, including services
that just became ready. Results must be independent of dictionary insertion and
dependency-list order. Use an iterative algorithm so long chains do not exhaust
the Python recursion limit.

`python -m deploy_order MANIFEST` reads a UTF-8 JSON manifest file and prints
one JSON list plus newline. Success exits 0 with no stderr. Invalid JSON,
invalid manifests and file/encoding errors exit 2 with a useful diagnostic on
stderr and nothing on stdout. Preserve the public function and CLI interface.
