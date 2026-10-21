# Current Hashmarks packet capture

`current-producer.json` was captured without model calls from Hashmarks commit
`9fcc7f1c474a8de10ac739e8b5c4cbf2da0f0903`. It records the complete synthetic
repository source, full v5 ambiguous/resolved observations, the initial v2 wire
packet and the producer-decoded logical packet, and native MCP results produced
by `hashmarks.mcp_server._task_evidence_tool_result`.

The initial packet was produced by `CodeMap.tasks.evidence(...,
result_mode="initial")`, using `limit=20`, `token_budget=256`, and task
`change target_impl behavior`. The resolved observation uses `change a.py`.
The captured source identity binds the full ambiguous observation. The producer
selected `json+lzma-base85` for this capture. Compatibility regressions also
exercise its documented zlib and string-table encodings against the same fixed
logical data.

Default initial MCP text is readable presentation. `structuredContent` carries
the exact encoded packet; these channels are different observations. These
fixtures do not establish delivery by a native agent host or model attention.
No resource link was emitted by this producer call, and tests never fetch one.

The consumer implements only this versioned wire protocol using the standard
library; it does not depend on Hashmarks at runtime. It preserves unknown
fields, omission accounting, ambiguity, provenance and freshness, and imposes
explicit observation bounds (64 nesting levels, 100,000 JSON nodes, 8 MiB of
decoded strings). Compressed initial packets additionally retain the producer's
1 MiB output and 16 MiB LZMA memory bounds. A packet beyond a consumer bound is
unavailable for parsing, never evidence that repository facts are absent.
