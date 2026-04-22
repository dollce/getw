# mark2down — 작업 이력

세션 일자: 2026-04-21 ~ 2026-04-22
환경: macOS (darwin 25.3.0, arm64), Python 3.12, uv 0.11.7

## 1. 요구사항

원본 프롬프트 요지:
- 입력: web URL (CLI 인자)
- 출력: 웹페이지 콘텐츠를 Markdown으로 변환 + 저장
- 개발환경: `uv`
- 배포: 설치 방식, `~/.local/bin`에 설치되어 어디서나 실행 가능
- 저장위치: 기본값 = 실행한 현재 디렉토리, 인자로 지정 가능
- 기술적 요구:
  - DOM Injection 처리를 위한 agent-browser 활용
  - Markdown 표 깨짐 현상 수정
  - YAML Frontmatter로 페이지 메타정보 삽입 (데이터 정제)
  - 학습 데이터로 활용 가능한 수준의 데이터 정제 / 정규화

## 2. 최종 아키텍처

```
src/mark2down/
├── __init__.py        # 패키지 메타
├── __main__.py        # python -m mark2down 진입점
├── cli.py             # Click 기반 CLI (mark2down, m2d)
├── agent.py           # Playwright + DOM injection (agent-browser)
├── converter.py       # HTML → Markdown 파이프라인
├── tables.py          # 커스텀 테이블 변환기
├── metadata.py        # YAML frontmatter 빌더
└── cleaner.py         # 학습 데이터용 후처리 (정규화/정제)
```

진입점: `pyproject.toml`의 `[project.scripts]`에 `mark2down`, `m2d` 두 alias 등록
빌드 백엔드: `hatchling` + `src/` 레이아웃

## 3. 주요 기술 결정

### 3.1 agent-browser → Playwright (sync_api)
- 헤드리스 Chromium으로 SPA·지연 로딩 페이지까지 처리
- DOM injection 스크립트(`DOM_INJECTION` in `agent.py`):
  - script/style/noscript/template 제거
  - `<details>` open, `aria-expanded="false"` → true
  - lazy-load 이미지 승격 (`data-src`, `data-original`, `data-lazy-src`)
  - bounded auto-scroll (최대 40회)로 infinite-scroll 트리거
- 메타 추출(`META_EXTRACTOR`): `<meta>`, JSON-LD, canonical, lang, document.title
- 최초 실행 시 chromium 자동 설치 (`playwright install chromium`)

### 3.2 콘텐츠 추출 — trafilatura에서 직접 추출로 전환
**초기 시도**: trafilatura `extract(output_format='html')` 사용
**문제 발견**: trafilatura가 `<tr>/<td>`를 자체 `<row>/<cell>` 스키마로 변환 → 커스텀 테이블 핸들러가 우회됨
**해결**: 직접 추출 파이프라인으로 재작성
- BS4(lxml) 파싱 → 노이즈 제거 (nav/aside/footer/ad/cookie/Wikipedia ambox·navbox 등)
- 콘텐츠 컨테이너 선택: `article` > `main` > `[role='main']` > `#mw-content-text` 등
- 상대 URL → 절대 URL 변환

### 3.3 테이블 변환기 (`tables.py`)
markdownify 기본 테이블 핸들러는 `|`/개행/span에 취약 → 처음부터 새로 작성:
- 2D 그리드 추출 (`_extract_grid`): rowspan/colspan을 별도 셀로 확장
- 셀 텍스트(`_inline_text`): `<br>`/code/strong/em/링크 보존, `|` 이스케이프, 개행 → ` <br> `
- 다중 헤더 행 → `" / "` 구분자로 단일 행으로 병합
- thead 자동 감지 (`<th>`-only 행 또는 `<thead>` 부모)
- 레이아웃 테이블 자동 스킵: `role="presentation"`, ambox/navbox/sidebar/metadata 클래스
- 중첩 테이블은 inner-first로 평탄화
- 통합 흐름: `replace_tables_with_placeholders` → markdownify 변환 → `inject_tables`로 재삽입

**버그 픽스**:
1. 중첩 테이블 처리 후 detached element에 `replace_with` 호출 → 최상위 테이블만 처리하도록 필터링
2. 셀 트레일링 ` <br> ` 잔존 → 정규식으로 leading/trailing/연속 `<br>` 제거

### 3.4 YAML Frontmatter (`metadata.py`)
다중 소스 병합:
- HTML `<meta>` 태그 (description, og:*, twitter:*, article:*, dc.*)
- JSON-LD (Article/NewsArticle/BlogPosting/TechArticle/ScholarlyArticle/WebPage)
- `<html lang>`, `<link rel=canonical>`, `<title>`

생성 필드: `title, source_url, final_url, canonical_url, domain, description, author, publisher, language, keywords, image, published_at, modified_at, fetched_at, word_count, char_count, reading_time_min, generator`

날짜는 `dateutil.parser`로 파싱 → ISO 8601 UTC로 정규화
빈 값은 자동 드롭 → frontmatter 간결 유지

### 3.5 데이터 정제 (`cleaner.py`)
- 유니코드 NFC 정규화
- Zero-width / NBSP / 특수 공백 제거
- 코드 펜스 내부 공백은 보존 (in_fence 플래그)
- UI 노이즈 라인 제거: "Skip to content", "Back to top", "Accept cookies" 등 (정규식)
- 연속 동일 라인 dedup
- 구조 정리:
  - 3+ 빈 줄 → 2개로 축소
  - heading/code-fence/table 블록 주위 공백 보장
  - 테이블 행 사이에는 빈 줄 금지 (행 사이 빈 줄 삽입 버그 수정)
  - 깨진 인라인 마크다운 복원 (`] (` → `](`, soft hyphen 제거)

### 3.6 CLI (`cli.py`)
주요 옵션:
- `URL` (필수)
- `-o, --output-dir DIR` (기본: cwd)
- `-f, --filename NAME` (기본: 슬러그(title) 또는 host-path)
- `-p, --stdout` (저장 + stdout 동시 출력)
- `--no-save` (stdout만)
- `--no-frontmatter`
- `--wait`, `--wait-selector`, `--timeout`, `--no-headless`
- `--user-agent`, `--header` (반복 가능)
- `--install-browsers`
- `-q, --quiet`

파일명: `python-slugify` 의 `allow_unicode=True`로 한글 슬러그 보존
중복 시 `-N` 접미사 자동 추가
진행 로그는 stderr (`rich.Console(stderr=True)`), 본문 출력은 stdout

## 4. 검증 (스모크 테스트)

| 케이스 | 결과 |
|---|---|
| https://example.com | 17 단어, frontmatter 정상 |
| Wikipedia EN — Comparison_of_e-book_formats | 12-열 비교표 GFM 형식으로 정확 렌더 |
| Wikipedia KO — 마크다운 | 한글 슬러그 파일명, 인포박스 2-열 테이블 정상 |
| GitHub — astral-sh/uv | OpenGraph description/publisher 추출 |

설치 확인:
- `~/.local/bin/mark2down`, `~/.local/bin/m2d` 심링크 → uv tools venv
- `/tmp/...`에서 호출 시 현재 cwd에 파일 저장 ✅

## 5. Cloudflare 봇 차단 대응 — 시도 후 롤백

**상황**: 사용자가 `https://manuals.plus/...` 페이지에서 status=403 + "Just a moment..." 챌린지 페이지가 그대로 마크다운화되는 것을 보고함

**진단**: 헤드리스 Chromium의 자동화 시그너처(`navigator.webdriver` 등)가 Cloudflare에 감지됨

**시도한 변경 사항** (이후 롤백):
- `patchright` (stealth-patched Playwright fork) 의존성 추가
- agent.py: 챌린지 페이지 감지 + 1회 재시도, `ChallengePageError` 예외, stealth init script
- agent.py: 영속 브라우저 컨텍스트 (`--profile NAME`) — `~/.cache/mark2down/profiles/<name>`
- agent.py: `--interactive` 모드 — visible window + 최대 120s 챌린지 해결 대기
- cli.py: `--interactive`, `--interactive-wait`, `--profile`, `--challenge-wait` 옵션 노출

**결과**:
- patchright passive stealth만으로는 manuals.plus의 Cloudflare Turnstile (interactive challenge)을 통과하지 못함
- `--wait 20`까지 늘려도 동일

**롤백**: 사용자 요청으로 위 변경 사항 모두 되돌림
- `pyproject.toml`에서 patchright 제거, `uv sync` 후 패키지 언인스톨
- `agent.py`, `cli.py` 원본 복구
- 글로벌 도구 재설치, example.com 동작 재확인

## 6. 배포

```bash
uv tool install /Users/jeonghyun.a/Library/CloudStorage/SynologyDrive-workspace/projects/26/mark2down
# 최초 1회: mark2down --install-browsers (auto-install on first run도 가능)
```

생성물:
- `~/.local/share/uv/tools/mark2down/` (격리된 venv)
- `~/.local/bin/mark2down` → tools venv의 `bin/mark2down`
- `~/.local/bin/m2d` → tools venv의 `bin/m2d`

## 7. GitHub Push

원격: https://github.com/dollce/mark2down (PRIVATE, master 브랜치)
초기 커밋: `82fb792` — 12 파일 (`.gitignore`, `.python-version`, `pyproject.toml`, `uv.lock`, `src/mark2down/*` 7개)

`.gitignore`: `.venv/`, `__pycache__/`, `*.pyc`, `dist/`, `build/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.DS_Store`, IDE 설정 등 표준 Python 제외 목록
`.python-version`은 글로벌 gitignore에 의해 무시되고 있어 `git add -f`로 강제 추가
로컬 `master`는 `origin/master` 추적 중

## 8. 알려진 제약

- **Cloudflare/Akamai 등의 interactive bot challenge**: 헤드리스 모드로 통과 불가능. 향후 필요 시 `--interactive` + `--profile` 패턴 재도입 고려 (롤백된 5장 참조)
- **로그인이 필요한 페이지**: 현재 `--header 'Cookie: ...'`로 우회 가능하나 영속 세션 기능은 없음
- **PDF, 이미지 위주 페이지**: 본문 텍스트가 적어 word_count가 0에 가까울 수 있음
- **Wikipedia infobox**: 좁은 2-열 테이블로 변환됨 (정확하지만 시각적으로 다름)
