# mark2down

웹페이지를 **학습 데이터로 바로 쓸 수 있는 깨끗한 Markdown**으로 변환하는 CLI.

```bash
m2d https://example.com
# → ./example-domain.md  (YAML frontmatter + 정제된 본문)
```

## 특징

- **agent-browser** — 헤드리스 Chromium + DOM injection. SPA·지연 로딩·`<details>`·lazy-load 이미지 모두 처리
- **테이블 안전 변환** — `|`/개행/rowspan/colspan을 정확히 처리하는 커스텀 GFM 테이블 라이터. Wikipedia 같은 복잡한 비교표도 깨지지 않음
- **YAML Frontmatter** — `<meta>` + JSON-LD + OpenGraph + Twitter card를 병합해 `title`, `author`, `published_at`, `keywords`, `language`, `word_count` 등 17+ 필드 자동 수집
- **학습 데이터용 정제** — 유니코드 NFC, zero-width / NBSP 제거, UI 노이즈("Skip to content", "Accept cookies", …) 컷, 코드 블록 보존, 한글 슬러그 파일명

## 설치

[`uv`](https://docs.astral.sh/uv/) 필요.

```bash
# GitHub에서 바로 설치 (~/.local/bin에 mark2down, m2d 등록)
uv tool install git+https://github.com/dollce/mark2down.git

# 최초 1회 — Chromium 다운로드 (~90MB)
mark2down --install-browsers
```

`~/.local/bin`이 PATH에 없다면:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

업그레이드:
```bash
uv tool upgrade mark2down
```

## 빠른 시작

```bash
# 현재 디렉토리에 <페이지-제목-슬러그>.md 저장
m2d https://example.com

# 저장 위치 지정
m2d https://example.com -o ~/notes

# 저장 + stdout 동시 출력
m2d https://example.com -p

# 파일 저장 없이 파이프로
m2d https://example.com --no-save | glow -

# 동적 페이지 — 특정 셀렉터가 보일 때까지 대기
m2d https://app.example.com/post --wait-selector article --wait 3

# 인증이 필요한 페이지 — 쿠키 헤더 직접 전달
m2d https://private.example.com/doc --header 'Cookie: session=abc123'
```

## 옵션

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `-o, --output-dir DIR` | 저장 디렉토리 | 현재 디렉토리 |
| `-f, --filename NAME` | 파일명 직접 지정 | 페이지 제목 슬러그 |
| `-p, --stdout` | 저장과 함께 stdout으로도 출력 | off |
| `--no-save` | 파일 저장 없이 stdout만 | off |
| `--no-frontmatter` | YAML frontmatter 생략 | off |
| `--wait SECONDS` | networkidle 후 추가 대기 | 1.0 |
| `--wait-selector CSS` | 등장을 기다릴 CSS 셀렉터 | — |
| `--timeout SECONDS` | 네비게이션/네트워크 타임아웃 | 45 |
| `--no-headless` | 브라우저 창을 띄워 디버그 | off |
| `--header 'Name: value'` | 추가 HTTP 헤더 (여러 번 가능) | — |
| `--user-agent UA` | User-Agent 문자열 교체 | Chrome/131 macOS |
| `-q, --quiet` | 진행 로그 숨김 (stderr) | off |
| `--install-browsers` | Chromium 설치 후 종료 | — |
| `-V, --version` | 버전 출력 | — |

## 출력 예시

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

**Markdown** is a lightweight markup language for creating formatted text using a plain-text editor...

| Feature | Filename extension | DRM | Images | Tables |
| --- | --- | --- | --- | --- |
| Comic book archive | .cbr, .cbz, .cb7 | ? | Yes | No |
| DOC | .doc | ? | Yes | Yes |
```

## 동작 원리

1. **Fetch** (`agent.py`) — Playwright Chromium으로 페이지 로드 → `domcontentloaded` → `networkidle` 대기 → DOM injection 스크립트로 lazy-load·`<details>`·auto-scroll 처리
2. **Extract** (`converter.py`) — BS4로 노이즈 컨테이너(nav/aside/footer/ad/cookie/Wikipedia ambox 등) 제거 → `<article>`/`<main>`/콘텐츠 컨테이너 픽
3. **Convert** (`tables.py` + markdownify) — 테이블은 placeholder로 분리해 커스텀 라이터가 처리, 나머지는 markdownify(ATX heading, dash bullet)
4. **Enrich** (`metadata.py`) — `<meta>` + JSON-LD + canonical/lang을 병합해 frontmatter 빌드
5. **Clean** (`cleaner.py`) — NFC 정규화, invisible char 제거, 구조적 공백 정리, UI 노이즈 컷

## 알려진 제약

자세한 내용은 [Issues](https://github.com/dollce/mark2down/issues) 참고:

- Cloudflare Turnstile 등 **인터랙티브 봇 챌린지**는 헤드리스로 통과 불가
- **로그인이 필요한 페이지**는 `--header 'Cookie: ...'`로 우회 (영속 세션 기능 없음)
- **PDF/이미지 위주 페이지**는 본문 텍스트가 거의 추출되지 않음
- **Wikipedia 인포박스**는 좁은 2-열 테이블로 변환됨 (정확하지만 시각적으로 다름)

## License

MIT
