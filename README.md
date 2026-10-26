# CMDM — 협동로봇 제조 데이터 관리 온톨로지

OPMS 프로젝트의 온톨로지 스키마와 SHACL 검증 규칙을 공개하는 저장소입니다.

- 사이트: https://hanhwaacademy-team-one.github.io/cmdm/
- 스키마 IRI: `https://hanhwaacademy-team-one.github.io/cmdm/ontology#` (접두어 `cmdm:`)
- 검증 규칙 IRI: `https://hanhwaacademy-team-one.github.io/cmdm/shapes#` (접두어 `cmdmsh:`)

## 구조

```
src/
  cmdm.ttl            스키마 원본 (편집은 여기서만)
  cmdm-shapes.ttl     SHACL 검증 규칙 원본
tools/
  build_docs.py       검사 + 사이트 생성 (표준 라이브러리만 사용)
  ttl.py              Turtle 파서
docs/                 GitHub Pages로 공개되는 폴더 (생성물, 직접 고치지 않음)
  index.html            개요
  ontology/             스키마 문서 · cmdm.ttl · {버전}/ 고정본
  shapes/               검증 규칙 문서 · cmdm-shapes.ttl · {버전}/ 고정본
  assets/               스타일·스크립트 (직접 편집)
  404.html
.github/workflows/check.yml   PR마다 검사와 docs/ 최신 여부 확인
```

## 사이트 만들기

```bash
python3 tools/build_docs.py          # 검사 후 docs/ 생성
python3 tools/build_docs.py --check  # 검사만
python3 -m http.server 8000 -d docs  # 로컬 미리보기 → http://localhost:8000
```

`src/`를 고쳤으면 `docs/`도 다시 생성해서 같이 커밋합니다. 그렇지 않으면 CI가 실패합니다.

빌드가 멈추는 경우는 다음과 같습니다.

- Turtle 문법 오류 (줄 번호 표시)
- 정의되지 않은 `cmdm:` 용어 사용 (SHACL의 SPARQL 안 포함)
- `owl:versionIRI`가 `…/ontology/{owl:versionInfo}`와 다름
- 이미 공개된 버전의 내용을 바꿈 (결정 기록 D-05: 공개 버전은 고정)

## 새 버전 공개 절차

1. `src/cmdm.ttl`의 `owl:versionInfo`와 `owl:versionIRI`를 함께 올립니다 (예: `1.1.0`).
2. `python3 tools/build_docs.py`를 실행합니다. `docs/ontology/1.1.0/`이 새로 생기고, 이전 버전은 그대로 남습니다.
3. PR을 올려 리뷰를 받고 병합합니다.
4. `git tag v1.1.0 && git push origin v1.1.0`

## GitHub Pages 설정

Settings → Pages → Build and deployment에서 Source를 `Deploy from a branch`, Branch를 `main` / `/docs`로 지정합니다.

## 가상 데이터 고지

스키마 주석과 검증 규칙에 나오는 제품군(VORLA 시리즈, 모델 V4·V7·V15), 컨트롤러(VC-1), 운영 SW(Tessel OS), 에러 코드, 도면번호, 아이템 코드, 수치 제한값, 매뉴얼 절 번호는 모두 이 프로젝트에서 만든 **가상 값**입니다. 실제 제품·회사와 관련이 없습니다.

이 저장소에는 스키마, 검증 규칙, 문서 생성 도구만 있습니다. 실제 제조사 자료는 넣지 않습니다. 교육 목적의 비상업 프로젝트입니다.
