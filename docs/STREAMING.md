# SSE lookahead design review (#86)

Decision: retain whole-response buffering for the supported protocols. A prefix-only
lookahead restorer cannot preserve the current fail-closed, alias and signed-reasoning
contracts. This review completes the proposed design consideration; incremental
streaming is not implemented or advertised.

A text-only restorer could hold the longest suffix that is a prefix of a known alias,
then release safe text as the next fragment arrives. For one known finite alias table,
that reduces text delay to an alias-length lookahead. However, the gateway also rejects
unknown alias-looking tokens, recognizes numerically glued aliases, folds JSON tool
arguments before parsing, restores tool names, and authenticates opaque reasoning replay.
A stream may contain a malformed final JSON fragment or an unknown alias after otherwise
valid text. Once HTTP 200 and partial output have been emitted, the gateway cannot replace
that response with the existing sanitized failure. A tool host might execute a partial
argument or consume an unverified opaque block. Prefix matching alone does not address
those protocol boundaries. Signed thinking/signature fragments must remain exact; they
cannot be treated as generic text. Interleaved choices/tool IDs need independent state.

A future implementation would need a protocol-aware incremental event parser, per-choice
and per-tool state, whole tool-argument validation before release, complete signed-block
provenance before replay, explicit terminal error events supported by each native host,
and bounded buffers/deadlines. Text streaming would have to be an explicit changed error
contract rather than an invisible replacement. Differential tests must partition every
alias and numeric token at every boundary, interleave streams, test malformed final JSON,
and preserve native host file-read/unknown-alias/signed-reasoning checks. The shared host
contracts remain authoritative (`tests/test_host_contract.py`).

## Baseline measurements

Measured locally on Windows, Python 3.11.15, 2026-10-08, using `GatewaySession.response`,
`time.perf_counter` and `tracemalloc`. Synthetic Responses text events alternate the two
halves of the same real allocated alias. No network, credentials or model calls are used.
One run; these numbers are descriptive, not a benchmark SLA.

| Events | Input bytes | Restore time (ms) | Peak traced bytes |
| --- | ---: | ---: | ---: |
| 100 | 12,800 | 8.75 | 145,107 |
| 1,000 | 128,000 | 74.74 | 1,489,384 |
| 5,000 | 640,000 | 358.67 | 7,565,744 |

Buffer memory includes parsed events, concatenated fragments and encoded output; input
size alone understates peak memory. Whole buffering adds the provider's full generation
time to time-to-first-output. Its 16 MiB byte bound is not a 16 MiB total memory bound.
The 180-second socket read timeout is not a total deadline. A safe future prototype must
measure end-to-end first output and peak memory against this baseline, including signed
reasoning and large tool JSON, before claiming an improvement. Current behavior and limits
remain documented in GATEWAY.md.
