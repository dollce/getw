# getw

`getw` turns a web page into a source-grounded semantic document and derives
LLM-ready text from that document.

```powershell
getw https://example.com/article
```

The default output is compact Markdown on stdout. It keeps headings, lists,
quotes, code, tables, and essential provenance without carrying the token cost
of raw HTML or a full JSON AST.

## Design in one minute

`getw` separates four concerns:

1. Load the source with verified static HTTP first.
2. When static extraction looks like a JavaScript shell, optionally render the
   page with Playwright and wait for meaningful DOM stability.
3. Use Trafilatura to select the main content and normalize its structure into
   getw's versioned, provider-neutral document tree.
4. Deterministically lower that tree to compact Markdown, regular Markdown,
   plain grounding text, or lossless normalized JSON.

No LLM is called during normal extraction. Optional semantic enrichment is a
separate, explicit operation and never replaces the structural document.

See [the architecture notes](docs/architecture.md) for the complete rationale.

## Install

Install the core static extractor:

```powershell
uv tool install "getw @ git+https://github.com/dollce/getw.git"
```

Install browser rendering support for JavaScript-heavy pages:

```powershell
uv tool install "getw[browser] @ git+https://github.com/dollce/getw.git"
python -m playwright install chromium
```

`getw` also tries an installed Chrome or Edge channel when Playwright's pinned
Chromium is unavailable.

For local development:

```powershell
git clone https://github.com/dollce/getw.git
cd getw
uv sync --extra browser --extra test
```

Optional source-grounded LLM enrichment uses a separate extra:

```powershell
uv sync --extra semantic
```

## CLI

The default path writes compact LLM input to stdout:

```powershell
getw https://example.com/article
```

Choose a deterministic output projection without fetching the page again:

```powershell
getw https://example.com/article --format markdown
getw https://example.com/article --format plain
getw https://example.com/article --format json --pretty
```

Control acquisition when reproducibility or a specific page requires it:

```powershell
# Never launch a browser
getw https://example.com/article --render static

# Require a browser-rendered DOM
getw https://example.com/app --render browser

# Require a meaningful element before DOM stability is evaluated
getw https://example.com/app --wait-for "main article"

# Use an installed system browser channel
getw https://example.com/app --browser-channel chrome
```

Read supplied HTML explicitly. A plain Python/CLI string is never guessed to be
HTML:

```powershell
getw .\page.html --html --base-url https://example.com/ --format markdown
Get-Content .\page.html -Raw | getw - --html --base-url https://example.com/
```

Save any output projection:

```powershell
getw https://example.com/article --format json -o article.getw.json
```

Diagnostics and warnings go to stderr; extracted content goes to stdout. Add
`--verbose` to see every static/browser attempt, selection reason, duration,
character count, and quality score.

## Python API

The common path has one function and one stable return type:

```python
import getw

document = getw.extract("https://example.com/article")
llm_input = document.text
```

HTML is explicit, so it cannot be mistaken for a URL:

```python
document = getw.extract(
    getw.Html(
        html_source,
        base_url="https://example.com/article",
    )
)
```

One extraction can be lowered repeatedly:

```python
compact = document.render("compact")  # essential provenance + compact Markdown
markdown = document.render("markdown")
plain = document.render("plain")      # stable LangExtract grounding string
full_ir = document.to_json(indent=2)   # normalized structure + diagnostics

restored = getw.Document.from_json(full_ir)
assert restored == document
```

The async facade runs the same contract without blocking an asyncio caller:

```python
document = await getw.aextract(url, render="auto")
```

Advanced callers can retain a configured `Extractor` and inject alternative
loaders without changing the public `Document` model:

```python
config = getw.ExtractionConfig(
    load_mode="auto",
    wait_for="main article",
    timeout=20,
)
extractor = getw.Extractor(config)
document = extractor.extract(url)
```

## Optional semantic enrichment

Structural web extraction and domain-specific fact extraction are different
problems. `getw` therefore keeps LLM enrichment opt-in:

```python
import getw

document = getw.extract("https://example.com/news")

task = getw.SemanticTask(
    instruction="Extract organizations exactly as written in the source.",
    model_id="gemini-3.5-flash",
    examples=(
        getw.SemanticExample(
            text="Acme announced a new product.",
            extractions=(
                getw.SemanticExtraction("organization", "Acme"),
            ),
        ),
    ),
)

enriched = getw.enrich(document, task)
annotation = enriched.annotations[0]
print(annotation.text, annotation.targets)
```

The adapter passes one stable plain-text projection to LangExtract. Returned
character intervals are mapped back to getw node-local ranges. Ungrounded model
output is dropped by default. The original structural IR remains unchanged.

## Output contracts

### `compact`

The default LLM representation. It includes the final source URL and useful
metadata, preserves block semantics with minimal Markdown, and omits repeated
inline link targets. It never summarizes, rewrites, or silently truncates.

### `markdown`

The extracted body as interoperable Markdown. Link targets are retained.
Headerless source tables use fenced TSV rather than inventing a header row.

### `plain`

A stable text sequence used for source grounding. It intentionally has fewer
structural cues than Markdown.

### `json`

The versioned normalized document, metadata, semantic annotations, notices,
and load attempts. It is lossless with respect to getw's selected/normalized
content, not a byte-for-byte archive of the original HTTP response.

## What `auto` rendering means

`auto` starts with static HTTP. It escalates only when extraction is empty or a
versioned shell signal is present (for example an empty React/Next/Nuxt root,
bundled-script shell, or explicit `--wait-for` selector).

Browser loading waits for `DOMContentLoaded`, then either the requested visible
selector or a stable signature of visible text and DOM node count. It does not
use a fixed sleep and does not depend on `networkidle`. Every attempted path and
the selected path are recorded in the result.

If an obvious empty shell needs rendering but no browser capability exists,
getw fails with an installation hint instead of emitting `Loading...` as page
content. Interactive login and CAPTCHA challenges are not bypassed.

## Scope

Version 2 is intentionally a web text extractor. The earlier PDF, DOCX, PPTX,
XLSX, OCR, and general file-conversion code was removed rather than carried into
an unrelated core. Supplied HTML remains supported because it is the replay and
integration boundary for authenticated or externally automated pages.

## License

MIT
