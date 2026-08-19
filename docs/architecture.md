# getw 2 architecture

## Objective and boundary

getw's core contract is:

```text
URL or supplied HTML
  -> acquired HTML snapshot
  -> selected semantic document IR
  -> deterministic LLM projection or normalized JSON
  -> optional source-grounded semantic overlay
```

The normalized IR is authoritative. Compact text is a projection, not the
source of truth. “Lossless” means lossless relative to selected and normalized
content; preserving the original response bytes, scripts, styles, pixels, and
server state would require a separate archival product.

## Why the public API is small

The common call is deliberately one function:

```python
document = getw.extract(url)
```

It always returns `Document`. The result is not conditionally a string, a
Trafilatura object, a browser page, or a model-provider object. `Document.text`,
`Document.render()`, and `Document.to_json()` lower the same immutable IR.

HTML uses `Html(content, base_url=...)`. This prevents fragile heuristics that
guess whether an arbitrary string is a URL, HTML fragment, or path.

An `Extractor` object exposes dependency injection for advanced applications
and tests, while loaders remain behind a small protocol. A global plugin
registry is intentionally absent from the first release: per-instance
configuration is easier to reproduce and isolate.

## Acquiring pages that require a DOM

Static HTTP is the default first pass because it is faster, cheaper, safer, and
more reproducible than executing a site's scripts.

The `auto` policy escalates to a browser only when one of these versioned signals
is present:

- structural extraction is empty;
- a short document contains a known application root or bundle marker;
- visible HTML is a script-heavy empty shell;
- the caller explicitly supplied a selector to wait for.

The browser adapter uses Playwright as an optional dependency. Navigation waits
for `DOMContentLoaded`. A supplied selector must become visible. Otherwise getw
polls a signature containing visible-text length, text hash, DOM node count, and
document readiness until it is stable for the configured interval.

This is intentional: Playwright documents `networkidle` as discouraged, and a
fixed sleep is both slower on fast pages and premature on slow pages. See the
[Playwright Page API](https://playwright.dev/python/docs/api/class-page) and
[browser installation documentation](https://playwright.dev/python/docs/browsers).

The selected static or browser candidate is the one with materially better
content length/quality. Both attempts and the escalation reason remain in the
document. An obvious shell without a renderer fails closed; a usable static
candidate can survive a failed optional rendering attempt with a warning.

## Why Trafilatura is necessary but not the public data model

Trafilatura 2.x provides strong main-text selection, metadata extraction, and
support for links, images, tables, formatting, precision/recall profiles, and a
Python `Document` whose body is a structured LXML tree. Its balanced mode also
has recall fallback behavior. See its [Python usage guide](https://trafilatura.readthedocs.io/en/latest/usage-python.html)
and [`bare_extraction` API](https://trafilatura.readthedocs.io/en/latest/corefunctions.html).

It is not sufficient as the whole product contract:

- client-side DOM content must exist before Trafilatura can inspect it;
- extraction is a precision/recall trade-off, not a semantic truth oracle;
- short pages can cross internal fallback thresholds and become flattened;
- its internal tags and serializer behavior may evolve independently of getw;
- domain entities and relationships require a task definition, not generic
  boilerplate removal.

getw therefore configures Trafilatura per extraction (never by mutating global
settings), lowers short-document thresholds, and converts its body tree into a
versioned AST. The AST includes headings, paragraphs, lists/items, quotes, code,
tables/rows/cells, images, links, emphasis, line breaks, and text leaves.

If Trafilatura produces no document, its baseline extractor is used only as a
last structural fallback. Empty output remains an error.

## IR and provenance

Every node has a deterministic pre-order ID, open string `kind`, attributes,
and ordered children. An open kind avoids coupling future extensions to a
closed enum, while schema version `1` fixes the meaning of built-in kinds.

`SourceInfo` records requested/final URL, acquisition mode, status, and content
type. `LoadAttempt` records each candidate's duration, HTML size, extracted
character count, quality score, selection state, and reason. Request header
values and page snapshots are not serialized, avoiding accidental credential or
cookie retention.

Metadata merges Trafilatura, HTML metadata, canonical links, and JSON-LD while
preserving raw HTML meta/JSON-LD under `Metadata.extra`.

## LLM representation

There is no universally token-minimal representation: tokenization is
model-specific, and reducing syntax eventually removes semantics. getw uses
four explicit points on that curve:

| Projection | Purpose | Preserved structure | Expected cost |
|---|---|---|---|
| `plain` | stable grounding offsets | reading order only | lowest |
| `markdown` | portable semantic body | headings, lists, quotes, code, tables, links | low |
| `compact` | direct LLM context | Markdown structure plus essential provenance; repeated link URLs omitted | low-to-moderate |
| `json` | archive/reprocessing | full normalized AST, metadata, evidence, diagnostics | highest |

One repository fixture measured as follows with tiktoken 0.14's `o200k_base`:

| Representation | Characters | Tokens |
|---|---:|---:|
| Plain | 173 | 36 |
| Markdown | 264 | 70 |
| Compact | 328 | 93 |
| Raw source HTML | 1,229 | 320 |
| Normalized IR JSON | 2,928 | 837 |

These are comparative measurements for one fixture and one tokenizer, not
universal ratios. The key invariant is qualitative: `compact` never summarizes,
rewrites, or silently truncates. A future token-budget layer must name a concrete
tokenizer and make every omission explicit.

Headerless tables illustrate why semantic fidelity matters. GFM requires a
header row, so promoting the first data row silently changes meaning. getw emits
such tables as fenced TSV; tables with real source headers use GFM.

## LangExtract's role

[Google LangExtract](https://github.com/google/langextract) is a good optional
backend for user-defined structured extraction. It accepts instructions,
examples or output schemas, handles long-document chunking, and aligns extracted
text to source character intervals. It is not a DOM loader, boilerplate remover,
or universal document ontology.

getw feeds LangExtract the stable `plain` grounding projection after structural
extraction. Its result becomes provider-neutral `Annotation` objects. Global
character intervals are translated into node-local `TextSpan` evidence.
Ungrounded values are dropped by default. Structural nodes are never rewritten
by probabilistic model output.

This separation keeps normal extraction deterministic and free of API keys,
model cost, latency, and model-version drift.

## Failure and security policy

- TLS verification is enabled; getw does not create an unverified SSL context.
- Responses are streamed with a configured maximum byte count.
- Unsupported binary content types fail instead of being decoded as web text.
- Explicit browser mode fails when browser support is unavailable.
- Auto mode does not serialize an obvious JavaScript loading shell as success.
- Known anti-bot challenge pages fail instead of becoming extracted content.
- getw does not bypass CAPTCHA, interactive login, or access controls.
- Supplied final HTML is the integration boundary for authenticated or externally
  automated sessions.

## Deliberately deferred

The first v2 core does not include crawling, browser interaction scripts,
session/login management, source snapshot archives, site-specific plugins,
automatic token-budget truncation, or non-web document conversion. Each can be
added around the IR without changing the common extraction contract.
