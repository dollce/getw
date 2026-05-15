# mark2down 1.0.0

Released: 2026-05-16

mark2down 1.0.0 is the first stable release of the simplified automatic extraction workflow. The CLI now focuses on one user experience: provide an input, optionally choose where to save it, and let mark2down select the best available conversion path.

## Highlights

- Simplified CLI: `m2d SOURCE` now performs the full extraction workflow with only one user-facing output option, `-o/--output`.
- Automatic extraction defaults: browser rendering, dynamic-page waiting, metadata extraction, file-type routing, document parsing, and OCR are selected by default.
- Expanded input support: URLs, local files, `file:` URIs, `data:` URIs, and stdin are routed through a common Markdown output path.
- Document conversion: PDF, DOCX, PPTX, and XLSX inputs are converted into LLM-oriented Markdown, including document tables where possible.
- OCR support: rendered URL images and embedded document images are processed with macOS Vision through `ocrmac` when available.
- Better source detection: CSV, TSV, JSON, JSONL, HTML, PDF, DOCX, PPTX, and XLSX can be inferred from content when explicit hints are not available.
- Cleaner web output: common page chrome, comments, related-content blocks, and syntax-highlighter layout tables are stripped before Markdown generation.

## UX Changes

- Removed public tuning flags such as `--ocr`, `--wait`, `--timeout`, `--header`, `--stdout`, `--no-save`, `--extension`, `--mime-type`, and `--charset`.
- `-o/--output` now accepts either a directory or a target `.md` file path.
- Markdown output always includes source metadata frontmatter.
- OCR is opportunistic: unsupported OCR environments skip OCR instead of failing the whole conversion.

## Fixes

- Fixed Securelist syntax-highlighter layout tables being emitted as broken Markdown tables such as `| 1 2 3 | ... |`.
- Fixed URL OCR blocks around linked images so OCR content no longer corrupts surrounding Markdown links.
- Hardened OCR block formatting so parenthesized OCR text cannot be interpreted as a Markdown link target.

## Validation

- `.venv/bin/python -m compileall -q src tests`
- `.venv/bin/python -m unittest discover -s tests -v`
- `git diff --check`
- Installed CLI smoke tests with `m2d --help`, stdin CSV conversion, local Securelist HTML conversion, and live Securelist URL extraction.

