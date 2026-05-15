# mark2down

`mark2down` is a command-line tool that saves web pages as clean, readable Markdown. It opens pages with a real browser, extracts the main content, preserves useful metadata, and writes a Markdown file you can keep in notes, documentation, or a Git repository.

```bash
m2d https://example.com
# Creates a Markdown file in the current directory.
```

## Why Use It?

- Archive web articles, docs, wiki pages, and blog posts as Markdown.
- Keep source URL, title, language, word count, and other metadata in YAML frontmatter.
- Convert complex HTML tables into GitHub Flavored Markdown tables.
- Handle many browser-rendered pages better than static HTML scrapers.

## Install

`mark2down` is designed to be installed with [`uv`](https://docs.astral.sh/uv/) as a global tool. This installs both `m2d` and `mark2down` into `~/.local/bin`, so the command works from any directory once that folder is on your `PATH`.

```bash
uv tool install git+https://github.com/dollce/mark2down.git
```

Check the installed command:

```bash
which m2d
# $HOME/.local/bin/m2d

m2d --version
```

If `which m2d` prints nothing, add `~/.local/bin` to your shell `PATH`.

For zsh:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

For bash, add the same export line to `~/.bashrc` or `~/.bash_profile`.

### Install Browser Runtime

`mark2down` uses Playwright Chromium. Run this once after installation:

```bash
m2d --install-browsers
```

### Install From a Local Checkout

Use this when developing the project locally or testing unpublished changes:

```bash
git clone https://github.com/dollce/mark2down.git
cd mark2down
uv tool install --reinstall .
```

The installed executable is still available at:

```bash
which m2d
# $HOME/.local/bin/m2d
```

### Upgrade or Remove

```bash
uv tool upgrade mark2down
uv tool uninstall mark2down
```

## Quick Start

Save a page to the current directory:

```bash
m2d https://example.com
```

Save to a specific directory:

```bash
m2d https://example.com -o ~/notes/web
```

Choose the output filename:

```bash
m2d https://example.com -f example.md
```

Save the file and also print Markdown to stdout:

```bash
m2d https://example.com --stdout
```

Print Markdown only, without writing a file:

```bash
m2d https://example.com --no-save
```

Pipe the output into another tool:

```bash
m2d https://example.com --no-save | glow -
```

Wait for dynamic content to appear:

```bash
m2d https://app.example.com/post --wait-selector article --wait 3
```

Pass a cookie or another HTTP header:

```bash
m2d https://private.example.com/doc --header 'Cookie: session=...'
```

## Options

| Option | Description | Default |
|---|---|---|
| `-o, --output-dir DIR` | Directory where the Markdown file is saved | Current directory |
| `-f, --filename NAME` | Output filename | Generated from the page title |
| `-p, --stdout` | Also print Markdown to stdout | Off |
| `--no-save` | Print Markdown without writing a file | Off |
| `--no-frontmatter` | Skip YAML frontmatter | Off |
| `--wait SECONDS` | Extra wait after page load | `1.0` |
| `--wait-selector CSS` | Wait until a CSS selector appears | None |
| `--timeout SECONDS` | Navigation and network timeout | `45` |
| `--no-headless` | Show the browser window for debugging | Off |
| `--header 'Name: value'` | Extra HTTP header. Can be repeated | None |
| `--user-agent UA` | Override the browser User-Agent | Default Chromium UA |
| `-q, --quiet` | Hide progress logs | Off |
| `--install-browsers` | Install Chromium and exit | None |
| `-V, --version` | Print version | None |

## Output Format

By default, the output contains YAML frontmatter followed by the extracted Markdown body.

```markdown
---
title: Markdown - Wikipedia
source_url: https://en.wikipedia.org/wiki/Markdown
canonical_url: https://en.wikipedia.org/wiki/Markdown
domain: en.wikipedia.org
language: en
fetched_at: '2026-04-22T05:27:13.246069+00:00'
word_count: 3658
char_count: 35309
reading_time_min: 17
generator: mark2down
---

# Markdown

Markdown is a lightweight markup language...

| Feature | Filename extension | Images | Tables |
| --- | --- | --- | --- |
| DOC | .doc | Yes | Yes |
```

Use `--no-frontmatter` if you only want the Markdown body.

## How It Works

1. Loads the page with Playwright Chromium.
2. Waits for the page to settle, then expands lazy-loaded and scroll-based content where possible.
3. Selects the most likely main content container and removes navigation, footer, cookie banner, and other layout noise.
4. Converts tables with a dedicated GitHub Flavored Markdown table writer.
5. Builds frontmatter from meta tags, OpenGraph, Twitter card metadata, JSON-LD, canonical URL, and language hints.
6. Normalizes Unicode and whitespace before writing the final Markdown file.

## Known Limitations

- Interactive bot challenges such as Cloudflare Turnstile or Akamai challenges cannot be solved automatically.
- Private pages require you to pass the needed cookie or authorization header manually.
- PDF-heavy or image-heavy pages may contain little extractable text.
- Highly visual layouts are converted into document structure, not preserved as visual layouts.
- Site-specific page chrome may sometimes remain in the extracted Markdown.

## License

MIT
