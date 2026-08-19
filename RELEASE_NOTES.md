# getw 2.0.0

Released: 2026-08-19

Version 2 is a clean redesign around one job: extract web text into a stable
semantic document and lower that document into efficient LLM input.

## Breaking changes

- Removed the 1.x document/OCR conversion pipeline and its PDF, DOCX, PPTX,
  XLSX, spreadsheet, OCR, rich HTML, and file-routing dependencies.
- Removed the legacy save-by-default and `update` command behavior.
- The CLI now writes compact LLM text to stdout by default.
- Python extraction returns a versioned `Document` IR rather than an ad-hoc
  Markdown/HTML result object.
- Plain strings are URL inputs. Supplied HTML must use `Html(...)` in Python or
  `--html` in the CLI.

## Extraction architecture

- Added verified, redirect-aware, size-limited static HTTP loading.
- Added optional Playwright rendering with lazy imports and system
  Chrome/Edge fallback.
- Added deterministic `auto` escalation for empty or JavaScript-shell pages.
- Added content/DOM stability waiting and explicit selector waiting; fixed
  sleeps and `networkidle` are not used.
- Added fail-closed bot-challenge and empty-shell handling.
- Trafilatura 2.x performs main-content selection, but its result is normalized
  into getw's own provider-neutral semantic tree.
- Short semantic pages retain headings, links, emphasis, nested lists, code,
  tables, and quotes through instance-local extraction thresholds.

## Output and semantics

- Added deterministic `compact`, `markdown`, and `plain` lowering.
- Added versioned JSON round-tripping with metadata, annotations, notices, and
  static/browser attempt provenance.
- Headerless tables use TSV fences rather than fabricated headers.
- Added optional provider-neutral semantic tasks backed by LangExtract, with
  source intervals mapped to getw node-local evidence ranges.
- Ungrounded semantic output is excluded by default.

## Validation

- 19 deterministic unit/integration tests cover static extraction, IR
  round-tripping, metadata, CLI behavior, adaptive loading, failure policy, and
  semantic grounding.
- A real headless Chrome integration test serves a local JavaScript shell,
  inserts its article after page load, and verifies selection of the final DOM.
- A live static CLI smoke test against `https://example.com` verifies HTTP,
  extraction, compact lowering, and stderr diagnostics together.
