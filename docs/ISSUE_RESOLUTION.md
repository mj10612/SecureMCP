# Issue review and resolution (0.2.0)

All 55 issues were reviewed against the code. Previously resolved #1–8 retain regression
coverage, adjusted where the safer 0.2 API deliberately changes representations or the MCP
trust boundary. The 47 remaining reports are covered by these changes and tests.

| Issues | Change | Evidence in tests |
| --- | --- | --- |
| #9, #11 | Sensitive code spans and URLs are atomic entities, including entities_only. | `test_sensitive_spans_mask_as_one_unit_and_restore` |
| #10, #15, #22, #45 | Delimited random pseudowords, consistent alphabetic allocation, no ordinary-word scanning or adjacency ambiguity. | `test_pseudowords_never_rewrite_ordinary_names_and_report_only_delimited_candidates`, `test_numeric_units_roundtrip` |
| #12 | Aggressive uses a smaller structural subset and masks auxiliaries/adverbs/pronouns. | `test_aggressive_masks_auxiliaries_and_function_adverbs` |
| #13, #14, #16, #48, #49 | Bounded validated TTL, duplicate creation errors, last-used mode, public locked counts, invalid policy errors. | `test_invalid_ttl_is_rejected`, `test_duplicate_creation_does_not_mutate_live_session`, `test_invalid_policy_fails_before_allocating_session` |
| #17, #26 | Unicode text words and code identifiers remain atomic and are masked. | `test_unicode_tokenizer_keeps_words_atomic`, `test_language_literals_and_identifiers_do_not_leak` |
| #18 | Only a terminal possessive suffix is detached; apostrophes inside names are private. | `test_sensitive_spans_mask_as_one_unit_and_restore` |
| #19, #34, #51 | UUID/IP/phone spans are recognized before number/word splitting; classifier patterns share their definitions. | `test_sensitive_spans_mask_as_one_unit_and_restore` |
| #20, #25, #31, #38 | Language-specific literals/comments: backticks, raw delimiters, SQL escaping, multiline/nested strings and complete numeric forms. Malformed boundaries fail closed. | `test_language_literals_and_identifiers_do_not_leak`, `test_unterminated_literals_fail_closed`, `test_raw_braces_and_rust_lifetimes_are_supported` |
| #21 | Conservative Korean non-function-word masking in entities_only plus explicit sensitive terms. | Korean cases in `test_sensitive_spans_mask_as_one_unit_and_restore`, `test_bilingual_matrix` |
| #23, #36, #50 | Periodic idle cleanup; shared operation lock; purge waits and invalidates retained objects. Custom store writes require the operation context. | `test_idle_expiration_releases_mapping_references_without_a_read`, `test_concurrent_masks_preserve_bijection_and_counts`, `test_clear_waits_for_inflight_mask_and_prevents_post_purge_writes` |
| #24 | Trusted LocalPrivacyClient before provider calls; no model-invoked restoration tools; CLI network transports refused; confidentiality claims corrected. | `test_model_cannot_call_restoration_oracle`, `test_trusted_client_only_sends_masked_data_and_clears_on_provider_failure` |
| #27 | Shared secret prefix/JWT and conservative long mixed-case token detection; explicit terms for unknown formats. | Credential cases in `test_sensitive_spans_mask_as_one_unit_and_restore` |
| #28 | Random 96-bit hash nonces independent of public salts and originals. | `test_hash_nonces_are_independent_of_original_and_public_salt` |
| #29, #33 | Single-syllable suffixes require stem evidence; bare/ambiguous nouns and names stay whole. | `test_ambiguous_korean_bare_nouns_are_not_truncated` |
| #30, #32 | Latin stems keep attached Korean particles; opt-in generated-response allomorph normalization. Exact roundtrips keep original particles. | `test_known_and_alphanumeric_korean_stems_keep_particles`, `test_generated_response_particle_normalization_is_explicit` |
| #35, #37, #41 | Language-specific keyword tables, missing core keywords, builtin/user-name masking, contextual Python keywords. | `test_core_keywords_preserved_per_language`, `test_language_literals_and_identifiers_do_not_leak`, `test_python_floor_division_and_contextual_keywords_preserve_syntax` |
| #39, #47 | Examples/demo use actual masked payloads; mocked echo outputs restore the correct clinical facts and Korean amounts. No fabricated response IDs. | `test_examples_restore_correct_facts`, `test_cli_demo_uses_actual_allocations` |
| #40 | ASCII code names and literal contents; native numeric nonce constants. Review syntax is preserved while execution/types remain opaque. | `test_repository_python_code_is_parseable_after_masking`, `test_masked_python_numeric_patterns_compile`, `test_all_code_strategies_keep_python_bytes_and_literals_parseable`, `test_javascript_masked_source_passes_native_syntax_check` |
| #42 | Unicode punctuation and symbols, hyphens and Markdown structure preserved. | `test_punctuation_and_markdown_are_preserved` |
| #43 | Broader candidate scanner reports altered spelling/case/separators; strict restoration rejects unknowns. No unsafe fuzzy reconstruction. | `test_altered_surrogates_are_reported_and_strict_mode_rejects`, `test_empty_reverse_store_still_reports_unknown_tokens` |
| #44, #46 | Type/context-aware mapping keys; sentence-initial proper-name candidates classified consistently. | `test_same_original_gets_distinct_type_and_context_allocations`, existing English entity regression tests |
| #52 | Versioned payloads, persisted allocations without regeneration, legacy read/update support, clear unsupported-version errors, exclusive CLI transaction lock. | `test_schema_version_and_generator_change_do_not_destroy_snapshots`, `test_legacy_pseudoword_snapshot_can_be_read_updated_and_read_again`, `test_cli_file_lock_covers_read_modify_write`, subprocess roundtrip tests |
| #53, #54 | Accurate metric aliases, unique vs occurrence counts, validated requested strategy, exposed preserve/sensitive options, removed unused fields/dead branch and duplicate suffix entries. | `test_metrics_distinguish_occurrences_and_unique_allocations`, `test_explicit_terms_and_preserve_are_available_in_cli_and_mcp`; lint/type checks |
| #55 | CI lint/type checks, warnings as errors, meaningful regression tests and an 85% coverage floor. | `.github/workflows/ci.yml`, `pyproject.toml` |

Tests are in [test_issue_resolution.py](../tests/test_issue_resolution.py) and the existing suite.
The bilingual matrix exercises English, Korean, mixed input, four modes and four strategies.
No live provider was called: callbacks are mocked to verify the trust boundary and facts.

## Limits that remain intentional

This is a heuristic local masker, not universal NER or a full morphological/compiler frontend.
Unrecognized sensitive terms require explicit specification or broader masking. Korean unknown
ambiguous stems are preserved inside their masked whole word. Generated-response particle
correction is opt-in and does not guess foreign pronunciation. Arbitrary model deletions and
inventions cannot be reconstructed. Code output is for review, not execution or type checking.
Legacy bare pseudowords remain readable but retain their historic ambiguity; new sessions use
the safe format. Clearing references is not byte-level zeroization. These limits and migration
changes are documented in the English/Korean README rather than hidden behind absolute claims.

Unrelated sponsorship/marketing proposal content was removed. Generated caches and coverage
artifacts are excluded from the repository; the development environment remains available.
