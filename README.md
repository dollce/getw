# mark2down

`mark2down`은 웹페이지를 읽기 좋은 Markdown 파일로 저장하는 CLI입니다. 브라우저로 페이지를 실제로 열어 본 뒤 본문, 표, 메타데이터를 추출하므로 정적 HTML만 긁는 도구보다 동적 페이지에 강합니다.

```bash
m2d https://example.com
# 현재 폴더에 example-com-index.md 같은 Markdown 파일 생성
```

## 언제 쓰나요?

- 웹 문서를 Markdown으로 보관하고 싶을 때
- 블로그, 문서 페이지, 위키 문서를 노트앱이나 Git 저장소에 넣고 싶을 때
- 표가 많은 페이지를 깨지지 않는 GFM Markdown 표로 바꾸고 싶을 때
- 제목, 원본 URL, 언어, 단어 수 같은 메타데이터를 frontmatter로 함께 남기고 싶을 때

## 설치

`mark2down`은 [`uv`](https://docs.astral.sh/uv/)의 tool 설치 방식을 권장합니다. 이렇게 설치하면 `m2d`와 `mark2down` 명령이 `~/.local/bin` 아래에 생기고, PATH만 잡혀 있으면 어느 경로에서든 실행할 수 있습니다.

```bash
uv tool install git+https://github.com/dollce/mark2down.git
```

설치 후 경로를 확인합니다.

```bash
which m2d
# $HOME/.local/bin/m2d

m2d --version
```

`which m2d`가 아무것도 출력하지 않으면 `~/.local/bin`이 PATH에 없는 상태입니다. zsh를 사용한다면 아래 한 줄을 추가한 뒤 터미널을 다시 열거나 `source ~/.zshrc`를 실행하세요.

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

bash를 사용한다면 `~/.bashrc` 또는 `~/.bash_profile`에 같은 내용을 추가하면 됩니다.

### 브라우저 설치

처음 한 번은 Playwright Chromium 브라우저를 설치해야 합니다.

```bash
m2d --install-browsers
```

### 로컬 소스에서 설치

저장소를 직접 받아 수정하거나 로컬 버전을 설치하려면:

```bash
git clone https://github.com/dollce/mark2down.git
cd mark2down
uv tool install --reinstall .
```

설치 위치는 동일하게 `~/.local/bin/m2d`입니다.

### 업그레이드와 제거

```bash
uv tool upgrade mark2down
uv tool uninstall mark2down
```

## 빠른 시작

현재 폴더에 Markdown 파일을 저장합니다.

```bash
m2d https://example.com
```

저장 폴더를 지정합니다.

```bash
m2d https://example.com -o ~/notes/web
```

파일명을 직접 지정합니다.

```bash
m2d https://example.com -f example.md
```

파일로 저장하면서 터미널에도 출력합니다.

```bash
m2d https://example.com --stdout
```

파일 저장 없이 다른 명령으로 넘깁니다.

```bash
m2d https://example.com --no-save | glow -
```

동적 페이지에서 특정 요소가 나타날 때까지 기다립니다.

```bash
m2d https://app.example.com/post --wait-selector article --wait 3
```

쿠키가 필요한 페이지는 직접 헤더를 전달할 수 있습니다.

```bash
m2d https://private.example.com/doc --header 'Cookie: session=...'
```

## 주요 옵션

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `-o, --output-dir DIR` | Markdown 파일을 저장할 디렉토리 | 현재 디렉토리 |
| `-f, --filename NAME` | 저장 파일명 직접 지정 | 페이지 제목 기반 자동 생성 |
| `-p, --stdout` | 저장과 함께 stdout으로도 출력 | 꺼짐 |
| `--no-save` | 파일 저장 없이 stdout으로만 출력 | 꺼짐 |
| `--no-frontmatter` | YAML frontmatter 생략 | 꺼짐 |
| `--wait SECONDS` | 페이지 로드 후 추가 대기 시간 | `1.0` |
| `--wait-selector CSS` | 지정한 CSS 셀렉터가 나타날 때까지 대기 | 없음 |
| `--timeout SECONDS` | 네비게이션/네트워크 타임아웃 | `45` |
| `--no-headless` | 브라우저 창을 띄워 디버깅 | 꺼짐 |
| `--header 'Name: value'` | 추가 HTTP 헤더. 여러 번 사용 가능 | 없음 |
| `--user-agent UA` | User-Agent 문자열 교체 | 기본 Chromium UA |
| `-q, --quiet` | 진행 로그 숨김 | 꺼짐 |
| `--install-browsers` | Chromium 설치 후 종료 | 없음 |
| `-V, --version` | 버전 출력 | 없음 |

## 출력 파일

기본 출력은 YAML frontmatter와 본문 Markdown으로 구성됩니다.

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

frontmatter가 필요 없으면 `--no-frontmatter`를 사용하세요.

## 동작 방식

1. Playwright Chromium으로 페이지를 열고 네트워크가 안정될 때까지 기다립니다.
2. lazy-load 이미지, `<details>`, 스크롤 기반 콘텐츠를 최대한 펼칩니다.
3. `article`, `main`, 본문 후보 영역을 우선 선택하고 nav/footer/cookie banner 같은 노이즈를 제거합니다.
4. 표는 별도 변환기로 처리해 `|`, 줄바꿈, `rowspan`, `colspan` 때문에 Markdown 표가 깨지는 문제를 줄입니다.
5. 메타 태그, OpenGraph, Twitter card, JSON-LD, canonical URL을 모아 frontmatter를 만듭니다.
6. 유니코드 정규화와 공백 정리를 거쳐 Markdown 파일을 저장합니다.

## 자주 겪는 문제

### `m2d: command not found`

대부분 `~/.local/bin`이 PATH에 없어서 발생합니다.

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
which m2d
```

### 브라우저 실행 오류

처음 설치 후 Chromium이 없으면 브라우저 설치 명령을 실행하세요.

```bash
m2d --install-browsers
```

### 봇 챌린지 페이지가 저장됨

Cloudflare Turnstile, Akamai 같은 인터랙티브 봇 챌린지는 헤드리스 브라우저로 통과할 수 없습니다. 일반 브라우저에서 한 번 통과한 뒤 세션 쿠키를 전달해야 합니다.

```bash
m2d https://example.com --header 'Cookie: cf_clearance=...'
```

### 로그인 페이지나 비공개 문서

현재는 영속 세션 저장 기능이 없습니다. 필요한 쿠키나 인증 헤더를 `--header`로 넘겨야 합니다.

## 알려진 제약

- 인터랙티브 봇 챌린지는 자동으로 풀 수 없습니다.
- PDF나 이미지 중심 페이지는 추출할 텍스트가 적을 수 있습니다.
- 사이트별 UI 구조에 따라 본문 외 영역이 일부 남을 수 있습니다.
- 복잡한 시각적 레이아웃은 보존하지 않고 Markdown 문서 구조로 변환합니다.

## License

MIT
