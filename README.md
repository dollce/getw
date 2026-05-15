# mark2down

`mark2down` turns web pages, local files, `file:`/`data:` URIs, and piped input into clean Markdown for LLM ingestion.

The CLI is intentionally small: give it input, and it chooses the best available extraction path automatically. Browser rendering, document parsing, table preservation, metadata frontmatter, file-type detection, and OCR are handled by default.

```bash
m2d https://example.com
# Creates a Markdown file in the current directory.
```

## Why Use It?

- Archive web articles, docs, wiki pages, and blog posts as Markdown.
- Convert PDF, DOCX, PPTX, XLSX, HTML, Markdown, plain text, CSV, JSON, and JSONL.
- Accept `http:`, `https:`, `file:`, and `data:` sources plus stdin.
- Preserve source metadata in YAML frontmatter.
- Convert complex tables into GitHub Flavored Markdown where possible.
- Run OCR automatically when image text can improve the output.

## Install

Install the command globally with [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/dollce/mark2down.git
```

Check the installed command:

```bash
which m2d
m2d --version
```

If `which m2d` prints nothing, add `~/.local/bin` to your shell `PATH`.

For zsh:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

For bash, add the same export line to `~/.bashrc` or `~/.bash_profile`.

### Install From a Local Checkout

Use this when developing the project locally or testing unpublished changes:

```bash
git clone https://github.com/dollce/mark2down.git
cd mark2down
uv tool install --reinstall .
```

### Upgrade or Remove

```bash
uv tool upgrade mark2down
uv tool uninstall mark2down
```

## Usage

Save a web page to the current directory:

```bash
m2d https://example.com
```

Save to a specific directory:

```bash
m2d https://example.com -o ~/notes/web
```

Save to a specific Markdown file:

```bash
m2d https://example.com -o ~/notes/example.md
```

Convert local documents:

```bash
m2d ./report.pdf
m2d ./brief.docx
m2d ./deck.pptx
m2d ./workbook.xlsx
```

Convert structured text:

```bash
m2d ./report.html
m2d ./payload.json
cat data.csv | m2d -o ./data.md
cat events.jsonl | m2d -o ./events.md
```

Convert URI-style sources:

```bash
m2d file:///tmp/report.html
m2d 'data:text/csv,name%2Cscore%0Aalpha%2C10'
```

## Options

`m2d` has one user-facing output option:

| Option | Description | Default |
|---|---|---|
| `-o, --output PATH` | Save path. Use a directory or a `.md` file path. | Current directory |

The standard `--help` and `--version` flags are also available.

## Output Format

The output contains YAML frontmatter followed by the extracted Markdown body.

```markdown
---
title: Markdown - Wikipedia
source_url: https://en.wikipedia.org/wiki/Markdown
canonical_url: https://en.wikipedia.org/wiki/Markdown
domain: en.wikipedia.org
language: en
word_count: 3658
char_count: 35309
reading_time_min: 17
generator: mark2down
---

# Markdown

Markdown is a lightweight markup language...
```

## How It Works

1. Detects whether the input is a URL, local file, `file:` URI, `data:` URI, or stdin.
2. Infers the content type from URL/file metadata, MIME hints, magic bytes, or text structure.
3. For URLs, loads the page with Playwright Chromium and waits for dynamic content to settle.
4. Selects the most likely main content container and removes navigation, footer, cookie banners, comments, and related-content chrome.
5. Converts PDF, DOCX, PPTX, XLSX, HTML, CSV, JSON, and JSONL into Markdown-oriented structure.
6. Preserves tables as GitHub Flavored Markdown where that format is appropriate.
7. Runs OCR opportunistically on rendered URL images and embedded document images.
8. Writes a Markdown file with source metadata and normalized text.

## Known Limitations

- Interactive bot challenges such as Cloudflare Turnstile or Akamai challenges cannot be solved automatically.
- Private pages that require login may not be extractable from a fresh headless browser session.
- OCR currently uses macOS Vision through `ocrmac`; on unsupported systems OCR is skipped rather than failing the whole conversion.
- XLS formulas are not evaluated by mark2down; XLSX output uses stored workbook values.
- Highly visual layouts are converted into document structure, not preserved as visual layouts.
- Site-specific page chrome may sometimes remain in the extracted Markdown.

## License

MIT
