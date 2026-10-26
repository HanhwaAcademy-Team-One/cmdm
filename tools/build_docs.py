#!/usr/bin/env python3
"""Build the CMDM GitHub Pages site from src/*.ttl.

    python3 tools/build_docs.py          # check + build docs/
    python3 tools/build_docs.py --check  # check only, write nothing (for CI)
    python3 tools/build_docs.py --force  # overwrite an already published version

Standard library only. Fails (exit 1) on Turtle syntax errors, undefined cmdm:
terms, a versionIRI that does not match versionInfo, or a change to a version
that has already been published (D-05: published versions are frozen).
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ttl  # noqa: E402
from ttl import RDF, XSD, BNode, Literal  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC_ONT = ROOT / "src" / "cmdm.ttl"
SRC_SHAPES = ROOT / "src" / "cmdm-shapes.ttl"
DOCS = ROOT / "docs"

SITE = "https://hanhwaacademy-team-one.github.io/cmdm/"
REPO = "https://github.com/HanhwaAcademy-Team-One/cmdm"
ONT_IRI = SITE + "ontology"
NS = ONT_IRI + "#"
SH_NS = SITE + "shapes#"

OWL = "http://www.w3.org/2002/07/owl#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
DCT = "http://purl.org/dc/terms/"
SH = "http://www.w3.org/ns/shacl#"

MODULES = {
    "M1": ("제품·모델", "제품군, 로봇·컨트롤러 모델, 공급 구성품, 시리얼 단위 실물 자산과 외부 장비"),
    "M2": ("설계 BOM·CAD", "STEP AP214와 정렬된 아이템 정의, BOM 라인, CAD 파일·도면 메타데이터"),
    "M3": ("재질·물성", "부품 재질 등급과 재질 분류, 밀도"),
    "M4": ("기구·성능 사양", "관절, 기술 사양, 정지 성능, 고정 사양, 툴 플랜지, 관절 자세"),
    "M5": ("전기 I/O·통신", "포트·핀, I/O 채널과 할당 기능, Modbus, 컨베이어 엔코더"),
    "M6": ("안전·규격", "안전 기능·설정, 동작·관절 제한, 안전 경계, 위험성 평가, 규격·인증"),
    "M7": ("SW 설정·프로그램", "TCP, 마운팅, 사용자 좌표, 변수, 프로그램과 명령, 사용자 계정"),
    "M8": ("운영·모니터링·보전", "운영 상태, 관측 기록, 이벤트·에러 코드, 점검 계획과 이력"),
    "M9": ("조직·데이터 품질", "조직·사람, 원천 데이터 품질 이슈"),
    "M0": ("공통 식별", "여러 모듈에서 함께 쓰는 코드·이름·설명·시리얼 번호"),
    "ANN": ("주석 속성", "단위, STEP 대응 엔티티, 매뉴얼 근거 절"),
}
MODULE_ORDER = ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M0", "ANN"]

KIND_LABEL = {
    OWL + "Class": "클래스",
    OWL + "ObjectProperty": "객체 속성",
    OWL + "DatatypeProperty": "데이터 속성",
    OWL + "AnnotationProperty": "주석 속성",
}
CHARACTERISTICS = {
    OWL + "FunctionalProperty": "함수적",
    OWL + "InverseFunctionalProperty": "역함수적",
    OWL + "SymmetricProperty": "대칭",
    OWL + "TransitiveProperty": "전이적",
}

e = html.escape


class BuildError(Exception):
    pass


# --------------------------------------------------------------------------
# Reading the sources
# --------------------------------------------------------------------------

def module_map(path: Path) -> dict[str, str]:
    """Assign each cmdm: term to a module using the section comments in the file."""
    current, out = None, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"#\s+M(\d)\.", line):
            current = "M" + re.match(r"#\s+M(\d)\.", line).group(1)
        elif re.match(r"#\s+Annotation properties", line):
            current = "ANN"
        elif re.match(r"#\s+(Object|Datatype) properties", line):
            current = None
        elif line.startswith("# ---"):
            m = re.search(r"\bM(\d)\b", line)
            if m:
                current = "M" + m.group(1)
            elif "공통" in line:
                current = "M0"
            elif "문서" in line:
                current = "M2"
            elif "관절" in line:
                current = "M4"
        m = re.match(r"cmdm:(\w+)\s+a\s+owl:", line)
        if m and m.group(1) not in out:
            out[m.group(1)] = current or "M0"
    return out


def shape_sections(path: Path) -> dict[str, str]:
    current, out = "기타", {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"#\s*-{4,}\s*(.+?)\s*-{4,}\s*$", line)
        if m:
            current = m.group(1)
        m = re.match(r"cmdmsh:(\w+)\s+a\s+sh:NodeShape", line)
        if m:
            out[m.group(1)] = current
    return out


def literals(g, s, p, lang=None):
    vals = [o for o in g.objects(s, p) if isinstance(o, Literal)]
    if lang is not None:
        vals = [v for v in vals if v.lang == lang]
    return [v.value for v in vals]


def first(vals, default=""):
    return vals[0] if vals else default


def local(iri: str) -> str:
    return iri[len(NS):] if isinstance(iri, str) and iri.startswith(NS) else ""


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def run_checks(ont, shapes) -> tuple[str, list[str]]:
    errors, warnings = [], []
    defined = {s for s, p, o in ont.triples if p == RDF + "type" and isinstance(s, str) and s.startswith(NS)}

    def used_in(g, label):
        for s, p, o in g.triples:
            for t in (s, p, o):
                if isinstance(t, str) and t.startswith(NS) and t not in defined:
                    errors.append(f"{label}: 정의되지 않은 용어 cmdm:{local(t)}")
            if p == SH + "select" and isinstance(o, Literal):
                for name in re.findall(r"cmdm:(\w+)", o.value):
                    if NS + name not in defined:
                        errors.append(f"{label}: SPARQL 안의 정의되지 않은 용어 cmdm:{name}")

    used_in(ont, "cmdm.ttl")
    used_in(shapes, "cmdm-shapes.ttl")

    version = first(literals(ont, ONT_IRI, OWL + "versionInfo"))
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        errors.append(f"owl:versionInfo 가 x.y.z 형식이 아닙니다: {version!r}")
    viri = ont.value(ONT_IRI, OWL + "versionIRI")
    if viri != f"{ONT_IRI}/{version}":
        errors.append(f"owl:versionIRI 가 {ONT_IRI}/{version} 이어야 합니다 (현재 {viri})")

    for s, p, o in ont.triples:
        if p == RDF + "type" and o == OWL + "Class" and not literals(ont, s, RDFS + "label", "ko"):
            warnings.append(f"한국어 레이블 없음: {ont.qname(s)}")

    # de-duplicate while keeping order
    errors = list(dict.fromkeys(errors))
    if errors:
        raise BuildError("\n".join(errors))
    return version, warnings


def check_frozen(version: str, force: bool):
    for src, rel in ((SRC_ONT, f"ontology/{version}/cmdm.ttl"), (SRC_SHAPES, f"shapes/{version}/cmdm-shapes.ttl")):
        published = DOCS / rel
        if published.exists() and published.read_bytes() != src.read_bytes() and not force:
            raise BuildError(
                f"{version} 버전은 이미 공개되었고 {src.relative_to(ROOT)} 와 내용이 다릅니다.\n"
                f"스키마를 바꿨다면 owl:versionInfo 와 owl:versionIRI 를 올리세요 (D-05). "
                f"공개본을 의도적으로 덮어쓰려면 --force 를 붙이세요."
            )


# --------------------------------------------------------------------------
# HTML helpers
# --------------------------------------------------------------------------

def ref(iri, g, onto_href: str) -> str:
    """Render a term reference. onto_href is the path to the ontology page ('' on that page)."""
    if isinstance(iri, BNode):
        return "<code>[ ]</code>"
    if isinstance(iri, Literal):
        return f"<code>{e(iri.value)}</code>"
    if iri.startswith(NS):
        name = local(iri)
        return f'<a class="ref" href="{onto_href}#{e(name)}"><code>cmdm:{e(name)}</code></a>'
    q = g.qname(iri)
    if iri.startswith("http://qudt.org/") or iri.startswith("http://www.w3.org/ns/sosa") or iri.startswith("http://www.w3.org/ns/prov"):
        return f'<a class="ref ext" href="{e(iri)}" rel="noopener"><code>{e(q)}</code></a>'
    return f'<code title="{e(iri)}">{e(q)}</code>'


def page(*, title, description, root, active, body, head_extra="") -> str:
    nav = [("개요", f"{root}", "home"), ("스키마", f"{root}ontology/", "ontology"), ("검증 규칙", f"{root}shapes/", "shapes")]
    links = "".join(
        f'<a href="{href}"{" aria-current=\"page\"" if key == active else ""}>{label}</a>' for label, href, key in nav
    )
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<link rel="icon" href="{root}assets/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="{root}assets/style.css">
{head_extra}
</head>
<body>
<header class="topbar">
  <div class="topbar-inner">
    <a class="brand" href="{root}"><span class="brand-mark" aria-hidden="true"></span>CMDM</a>
    <nav class="topnav" aria-label="사이트">{links}<a href="{REPO}" rel="noopener">GitHub</a></nav>
  </div>
</header>
{body}
<footer class="footer">
  <div class="wrap">
    <p>CMDM · 협동로봇 제조 데이터 관리 온톨로지 — OPMS 프로젝트 팀</p>
    <p class="muted">이 사이트는 <code>src/*.ttl</code> 에서 <code>tools/build_docs.py</code> 로 생성됩니다.</p>
  </div>
</footer>
<script src="{root}assets/site.js"></script>
</body>
</html>
"""


def search_box(placeholder):
    return f"""<div class="search">
  <label class="sr-only" for="q">검색</label>
  <input id="q" type="search" placeholder="{e(placeholder)}" autocomplete="off" spellcheck="false">
  <p class="search-count" aria-live="polite"></p>
</div>"""


# --------------------------------------------------------------------------
# Ontology page
# --------------------------------------------------------------------------

def collect_terms(ont, modules):
    terms = {}
    for s, p, o in ont.triples:
        if p == RDF + "type" and isinstance(s, str) and s.startswith(NS) and o in KIND_LABEL:
            terms.setdefault(s, {"iri": s, "name": local(s), "kind": o, "module": modules.get(local(s), "M0")})
    sub_of, inverse, sub_prop = defaultdict(list), defaultdict(list), defaultdict(list)
    disjoint = defaultdict(set)
    for s, p, o in ont.triples:
        if p == RDFS + "subClassOf" and isinstance(o, str):
            sub_of[o].append(s)
        elif p == OWL + "inverseOf":
            inverse[o].append(s)
        elif p == RDFS + "subPropertyOf":
            sub_prop[o].append(s)
        elif p == OWL + "disjointWith":
            disjoint[s].add(o); disjoint[o].add(s)
    by_domain, by_range = defaultdict(list), defaultdict(list)
    for iri, t in terms.items():
        for d in ont.objects(iri, RDFS + "domain"):
            by_domain[d].append(iri)
        for r in ont.objects(iri, RDFS + "range"):
            by_range[r].append(iri)
    return terms, sub_of, inverse, sub_prop, disjoint, by_domain, by_range


def render_ontology(ont, shapes, version, versions, root, pinned: bool) -> str:
    modules = module_map(SRC_ONT)
    terms, sub_of, inverse, sub_prop, disjoint, by_domain, by_range = collect_terms(ont, modules)
    shapes_by_target = defaultdict(list)
    for s, p, o in shapes.triples:
        if p == SH + "targetClass":
            shapes_by_target[o].append(s)

    def r(x):
        return ref(x, ont, "")

    def refs(xs):
        return ", ".join(r(x) for x in xs) if xs else ""

    def term_html(iri):
        t = terms[iri]
        name, kind = t["name"], t["kind"]
        ko = first(literals(ont, iri, RDFS + "label", "ko"))
        en = first(literals(ont, iri, RDFS + "label", "en"))
        comments = literals(ont, iri, RDFS + "comment")
        facts = []

        def fact(label, value):
            if value:
                facts.append(f"<dt>{label}</dt><dd>{value}</dd>")

        chars = [CHARACTERISTICS[x] for x in ont.objects(iri, RDF + "type") if x in CHARACTERISTICS]
        if kind == OWL + "Class":
            fact("상위 클래스", refs(ont.objects(iri, RDFS + "subClassOf")))
            fact("하위 클래스", refs(sub_of.get(iri, [])))
            fact("서로소", refs(sorted(disjoint.get(iri, []))))
            fact("정의역인 속성", refs(by_domain.get(iri, [])))
            fact("치역인 속성", refs(by_range.get(iri, [])))
            fact("유사 개념", refs(ont.objects(iri, SKOS + "closeMatch")))
            shp = shapes_by_target.get(iri, [])
            if shp:
                fact("검증 규칙", ", ".join(
                    f'<a class="ref" href="{root}shapes/#{e(s[len(SH_NS):])}"><code>cmdmsh:{e(s[len(SH_NS):])}</code></a>' for s in shp))
        else:
            dom, rng = ont.objects(iri, RDFS + "domain"), ont.objects(iri, RDFS + "range")
            if dom or rng:
                fact("정의역 → 치역", f'{refs(dom) or "<span class=muted>(제한 없음)</span>"} <span class="arrow">→</span> {refs(rng) or "<span class=muted>(제한 없음)</span>"}')
            fact("특성", " · ".join(chars))
            invs = list(ont.objects(iri, OWL + "inverseOf")) + inverse.get(iri, [])
            fact("역속성", refs(list(dict.fromkeys(invs))))
            fact("상위 속성", refs(ont.objects(iri, RDFS + "subPropertyOf")))
            fact("하위 속성", refs(sub_prop.get(iri, [])))
            for chain in ont.objects(iri, OWL + "propertyChainAxiom"):
                fact("속성 체인", ' <span class="arrow">∘</span> '.join(r(x) for x in ont.list_items(chain)))
            fact("단위", refs(ont.objects(iri, NS + "unit")))
        fact("STEP 대응", " ".join(f"<code>{e(v)}</code>" for v in literals(ont, iri, NS + "stepEntity")))
        fact("매뉴얼 근거", e(", ".join(literals(ont, iri, NS + "manualSection"))))

        search = " ".join([name, ko, en, *comments]).lower()
        badge_cls = {OWL + "Class": "b-class", OWL + "ObjectProperty": "b-obj",
                     OWL + "DatatypeProperty": "b-data", OWL + "AnnotationProperty": "b-ann"}[kind]
        labels = ""
        if ko or en:
            labels = '<p class="labels">' + (f'<span lang="ko">{e(ko)}</span>' if ko else "") + (
                f'<span lang="en">{e(en)}</span>' if en else "") + "</p>"
        comment = "".join(f'<p class="comment">{e(c)}</p>' for c in comments)
        dl = f'<dl class="facts">{"".join(facts)}</dl>' if facts else ""
        return f"""<article class="term" id="{e(name)}" data-search="{e(search)}">
  <header class="term-head">
    <h4><a class="anchor" href="#{e(name)}">cmdm:{e(name)}</a></h4>
    <span class="badge {badge_cls}">{KIND_LABEL[kind]}</span>
    <button class="copy" type="button" data-copy="{e(iri)}" title="IRI 복사">IRI 복사</button>
  </header>
  {labels}{comment}{dl}
</article>"""

    grouped = defaultdict(lambda: defaultdict(list))
    for iri, t in terms.items():
        grouped[t["module"]][t["kind"]].append(iri)

    nav_items, sections = [], []
    for mod in MODULE_ORDER:
        if mod not in grouped:
            continue
        title, desc = MODULES[mod]
        count = sum(len(v) for v in grouped[mod].values())
        code = "" if mod in ("M0", "ANN") else f'<span class="mod-code">{mod}</span>'
        nav_items.append(f'<li><a href="#mod-{mod}">{code}{e(title)} <span class="n">{count}</span></a></li>')
        parts = []
        for kind in (OWL + "Class", OWL + "ObjectProperty", OWL + "DatatypeProperty", OWL + "AnnotationProperty"):
            items = grouped[mod].get(kind)
            if items:
                parts.append(f'<div class="kind-group"><h3>{KIND_LABEL[kind]} <span class="n">{len(items)}</span></h3>'
                             + "\n".join(term_html(i) for i in items) + "</div>")
        sections.append(f"""<section class="module" id="mod-{mod}">
  <h2>{code}{e(title)}</h2>
  <p class="module-desc">{e(desc)}</p>
  {''.join(parts)}
</section>""")

    n_cls = sum(1 for t in terms.values() if t["kind"] == OWL + "Class")
    n_obj = sum(1 for t in terms.values() if t["kind"] == OWL + "ObjectProperty")
    n_dat = sum(1 for t in terms.values() if t["kind"] == OWL + "DatatypeProperty")
    title_ko = first(literals(ont, ONT_IRI, DCT + "title", "ko"))
    title_en = first(literals(ont, ONT_IRI, DCT + "title", "en"))
    created = first(literals(ont, ONT_IRI, DCT + "created"))
    desc = first(literals(ont, ONT_IRI, RDFS + "comment"))
    ttl_name = "cmdm.ttl"
    banner = ""
    if pinned:
        banner = (f'<p class="banner">이 페이지는 <strong>{e(version)}</strong> 고정본입니다. '
                  f'최신 문서는 <a href="{root}ontology/">스키마</a>에서 볼 수 있습니다.</p>')
    other_versions = " ".join(f'<a href="{root}ontology/{v}/">{v}</a>' for v in versions)

    body = f"""<main class="wrap layout">
<aside class="sidebar" aria-label="모듈">
  {search_box("클래스·속성 검색  ( / )")}
  <ul class="toc">{''.join(nav_items)}</ul>
</aside>
<div class="content">
  {banner}
  <section class="hero hero-doc">
    <p class="eyebrow">OWL 2 스키마 · v{e(version)}</p>
    <h1>{e(title_ko)}</h1>
    <p class="subtitle" lang="en">{e(title_en)}</p>
    <p class="lead">{e(desc)}</p>
    <dl class="meta">
      <dt>온톨로지 IRI</dt><dd><code>{e(ONT_IRI)}</code></dd>
      <dt>버전 IRI</dt><dd><code>{e(ONT_IRI)}/{e(version)}</code></dd>
      <dt>접두어</dt><dd><code>@prefix cmdm: &lt;{e(NS)}&gt; .</code></dd>
      <dt>작성일</dt><dd>{e(created)}</dd>
      <dt>버전</dt><dd>{other_versions}</dd>
    </dl>
    <div class="actions">
      <a class="btn primary" href="{ttl_name}" download>Turtle 내려받기</a>
      <a class="btn" href="{root}shapes/">SHACL 검증 규칙</a>
    </div>
    <ul class="stats">
      <li><strong>{n_cls}</strong>클래스</li>
      <li><strong>{n_obj}</strong>객체 속성</li>
      <li><strong>{n_dat}</strong>데이터 속성</li>
      <li><strong>{len(MODULES) - 2}</strong>모듈</li>
    </ul>
  </section>
  {''.join(sections)}
</div>
</main>"""
    return page(
        title=f"CMDM 스키마 {version}" if pinned else "CMDM 스키마",
        description=desc[:150],
        root=root,
        active="ontology",
        body=body,
        head_extra=f'<link rel="alternate" type="text/turtle" href="{ttl_name}" title="CMDM (Turtle)">',
    )


# --------------------------------------------------------------------------
# Shapes page
# --------------------------------------------------------------------------

SEVERITY = {SH + "Violation": ("위반", "sev-violation"), SH + "Warning": ("경고", "sev-warning"), SH + "Info": ("참고", "sev-info")}


def describe(g, node, onto_href) -> list[str]:
    out = []

    def num(p):
        return ", ".join(o.value for o in g.objects(node, SH + p) if isinstance(o, Literal))

    for p, label in (("minCount", "최소 {}개"), ("maxCount", "최대 {}개"), ("minInclusive", "값 ≥ {}"),
                     ("minExclusive", "값 > {}"), ("maxInclusive", "값 ≤ {}"), ("maxExclusive", "값 < {}"),
                     ("maxLength", "길이 ≤ {}"), ("minLength", "길이 ≥ {}")):
        v = num(p)
        if v:
            out.append(label.format(e(v)))
    for dt in g.objects(node, SH + "datatype"):
        out.append(f"형식 {ref(dt, g, onto_href)}")
    for c in g.objects(node, SH + "class"):
        out.append(f"클래스 {ref(c, g, onto_href)}")
    for pat in g.objects(node, SH + "pattern"):
        out.append(f"패턴 <code>{e(pat.value)}</code>")
    for lt in g.objects(node, SH + "lessThan"):
        out.append(f"&lt; {ref(lt, g, onto_href)}")
    for hv in g.objects(node, SH + "hasValue"):
        out.append(f"값 = <code>{e(str(hv))}</code>")
    for lst in g.objects(node, SH + "in"):
        vals = ", ".join(f"<code>{e(str(x))}</code>" for x in g.list_items(lst))
        out.append(f"허용값 {{ {vals} }}")
    for combo, word in (("or", "다음 중 하나"), ("and", "모두")):
        for lst in g.objects(node, SH + combo):
            alts = []
            for item in g.list_items(lst):
                inner = describe(g, item, onto_href)
                path = g.value(item, SH + "path")
                head = path_html(g, path, onto_href) + " " if path is not None else ""
                alts.append(f"({head}{' · '.join(inner)})")
            out.append(f"{word}: " + (" 또는 " if combo == "or" else " 그리고 ").join(alts))
    return out


def path_html(g, path, onto_href):
    if isinstance(path, BNode):
        items = g.list_items(path)
        return ' <span class="arrow">/</span> '.join(ref(x, g, onto_href) for x in items)
    return ref(path, g, onto_href)


def render_shapes(shapes, version, versions, root, pinned: bool) -> str:
    sections = shape_sections(SRC_SHAPES)
    onto_href = f"{root}ontology/"
    order = [s for s, p, o in shapes.triples if p == RDF + "type" and o == SH + "NodeShape"]
    grouped = defaultdict(list)
    for s in order:
        grouped[sections.get(s[len(SH_NS):], "기타")].append(s)

    sev_count = defaultdict(int)
    blocks, nav = [], []
    for sec, shape_list in grouped.items():
        sec_id = "sec-" + re.sub(r"[^\w]+", "-", sec).strip("-")
        nav.append(f'<li><a href="#{e(sec_id)}">{e(sec)} <span class="n">{len(shape_list)}</span></a></li>')
        cards = []
        for s in shape_list:
            name = s[len(SH_NS):]
            node_sev = shapes.value(s, SH + "severity", SH + "Violation")
            targets = ", ".join(ref(t, shapes, onto_href) for t in shapes.objects(s, SH + "targetClass"))
            rows = []
            for prop in shapes.objects(s, SH + "property"):
                sev = shapes.value(prop, SH + "severity", node_sev)
                sev_count[sev] += 1
                label, cls = SEVERITY.get(sev, ("?", ""))
                msg = first(literals(shapes, prop, SH + "message"))
                rows.append(f"""<tr>
  <td data-label="경로">{path_html(shapes, shapes.value(prop, SH + "path"), onto_href)}</td>
  <td data-label="조건">{' · '.join(describe(shapes, prop, onto_href))}</td>
  <td data-label="수준"><span class="sev {cls}">{label}</span></td>
  <td data-label="메시지">{e(msg)}</td>
</tr>""")
            node_rules = describe(shapes, s, onto_href)
            if node_rules:
                sev_count[node_sev] += 1
                label, cls = SEVERITY.get(node_sev, ("?", ""))
                rows.append(f"""<tr>
  <td data-label="경로"><span class="muted">(노드)</span></td>
  <td data-label="조건">{' · '.join(node_rules)}</td>
  <td data-label="수준"><span class="sev {cls}">{label}</span></td>
  <td data-label="메시지"></td>
</tr>""")
            sparqls = []
            for sq in shapes.objects(s, SH + "sparql"):
                sev = shapes.value(sq, SH + "severity", node_sev)
                sev_count[sev] += 1
                label, cls = SEVERITY.get(sev, ("?", ""))
                msg = first(literals(shapes, sq, SH + "message"))
                query = re.sub(r"^\n", "", first(literals(shapes, sq, SH + "select")))
                query = "\n".join(line[12:] if line.startswith(" " * 12) else line for line in query.splitlines())
                sparqls.append(f"""<div class="sparql">
  <p><span class="sev {cls}">{label}</span> {e(msg)}</p>
  <details><summary>SPARQL 보기</summary><pre><code>{e(query.strip())}</code></pre></details>
</div>""")
            table = ""
            if rows:
                table = f"""<table class="rules">
<thead><tr><th>경로</th><th>조건</th><th>수준</th><th>메시지</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>"""
            search = " ".join([name, *[t for t in (shapes.qname(x) for x in shapes.objects(s, SH + "targetClass"))]]).lower()
            search += " " + " ".join(literals(shapes, b, SH + "message")[0] for b in
                                     shapes.objects(s, SH + "property") + shapes.objects(s, SH + "sparql")
                                     if literals(shapes, b, SH + "message")).lower()
            cards.append(f"""<article class="term shape" id="{e(name)}" data-search="{e(search)}">
  <header class="term-head">
    <h4><a class="anchor" href="#{e(name)}">cmdmsh:{e(name)}</a></h4>
    <span class="badge b-shape">NodeShape</span>
  </header>
  <p class="target">대상 클래스 {targets}</p>
  {table}{''.join(sparqls)}
</article>""")
        blocks.append(f'<section class="module" id="{e(sec_id)}"><h2>{e(sec)}</h2>{"".join(cards)}</section>')

    banner = ""
    if pinned:
        banner = (f'<p class="banner">이 페이지는 <strong>{e(version)}</strong> 고정본입니다. '
                  f'최신 규칙은 <a href="{root}shapes/">검증 규칙</a>에서 볼 수 있습니다.</p>')
    body = f"""<main class="wrap layout">
<aside class="sidebar" aria-label="규칙 묶음">
  {search_box("규칙·메시지 검색  ( / )")}
  <ul class="toc">{''.join(nav)}</ul>
</aside>
<div class="content">
  {banner}
  <section class="hero hero-doc">
    <p class="eyebrow">SHACL · v{e(version)}</p>
    <h1>검증 규칙</h1>
    <p class="lead">사양서를 그래프에 적재하기 전에 통과해야 하는 입력 게이트입니다. <strong>위반</strong>은 저장을 막고, <strong>경고</strong>는 정제 대상으로 기록합니다. 기준값은 가상 제품군 VORLA 시리즈의 사양을 따릅니다.</p>
    <dl class="meta">
      <dt>규칙 IRI</dt><dd><code>{e(SH_NS)}</code></dd>
      <dt>접두어</dt><dd><code>@prefix cmdmsh: &lt;{e(SH_NS)}&gt; .</code></dd>
      <dt>버전</dt><dd>{" ".join(f'<a href="{root}shapes/{v}/">{v}</a>' for v in versions)}</dd>
    </dl>
    <div class="actions">
      <a class="btn primary" href="cmdm-shapes.ttl" download>Turtle 내려받기</a>
      <a class="btn" href="{root}ontology/">스키마 문서</a>
    </div>
    <ul class="stats">
      <li><strong>{len(order)}</strong>노드 셰이프</li>
      <li><strong>{sev_count[SH + 'Violation']}</strong>위반 규칙</li>
      <li><strong>{sev_count[SH + 'Warning']}</strong>경고 규칙</li>
    </ul>
  </section>
  {''.join(blocks)}
</div>
</main>"""
    return page(title=f"CMDM 검증 규칙 {version}" if pinned else "CMDM 검증 규칙",
                description="CMDM 사양 데이터 입력 게이트용 SHACL 규칙", root=root, active="shapes", body=body,
                head_extra='<link rel="alternate" type="text/turtle" href="cmdm-shapes.ttl" title="CMDM SHACL (Turtle)">')


# --------------------------------------------------------------------------
# Home page
# --------------------------------------------------------------------------

def render_home(ont, shapes, version, versions) -> str:
    modules = module_map(SRC_ONT)
    terms = collect_terms(ont, modules)[0]
    counts = defaultdict(lambda: defaultdict(int))
    for t in terms.values():
        counts[t["module"]][t["kind"]] += 1
    cards = []
    for mod in MODULE_ORDER[:9]:
        title, desc = MODULES[mod]
        c = counts[mod]
        cards.append(f"""<a class="mod-card" href="ontology/#mod-{mod}">
  <span class="mod-code">{mod}</span>
  <h3>{e(title)}</h3>
  <p>{e(desc)}</p>
  <p class="mod-n">클래스 {c[OWL + 'Class']} · 속성 {c[OWL + 'ObjectProperty'] + c[OWL + 'DatatypeProperty']}</p>
</a>""")
    n_cls = sum(1 for t in terms.values() if t["kind"] == OWL + "Class")
    n_prop = sum(1 for t in terms.values() if t["kind"] in (OWL + "ObjectProperty", OWL + "DatatypeProperty"))
    n_shapes = sum(1 for s, p, o in shapes.triples if p == RDF + "type" and o == SH + "NodeShape")
    vers = "".join(
        f'<li><strong>{v}</strong> <a href="ontology/{v}/">스키마</a> · <a href="ontology/{v}/cmdm.ttl">cmdm.ttl</a> · '
        f'<a href="shapes/{v}/">규칙</a> · <a href="shapes/{v}/cmdm-shapes.ttl">cmdm-shapes.ttl</a></li>'
        for v in reversed(versions))
    body = f"""<main>
<section class="hero hero-home">
  <div class="wrap">
    <p class="eyebrow">OPMS 프로젝트 · 온톨로지 v{e(version)}</p>
    <h1>협동로봇 제조 데이터를<br>하나의 언어로</h1>
    <p class="lead">CMDM은 협동로봇의 모델 사양, 설계 BOM, 전기 인터페이스, 안전 설정, 운영·보전 데이터를 연결하는 OWL 2 온톨로지입니다. 사양서를 적재할 때는 SHACL 규칙이 입력 게이트 역할을 합니다.</p>
    <div class="actions">
      <a class="btn primary" href="ontology/">스키마 문서</a>
      <a class="btn" href="shapes/">검증 규칙</a>
      <a class="btn" href="ontology/cmdm.ttl" download>cmdm.ttl</a>
    </div>
    <ul class="stats">
      <li><strong>{n_cls}</strong>클래스</li>
      <li><strong>{n_prop}</strong>속성</li>
      <li><strong>{n_shapes}</strong>검증 셰이프</li>
      <li><strong>v{e(version)}</strong>현재 버전</li>
    </ul>
  </div>
</section>

<section class="wrap block">
  <h2>모듈</h2>
  <div class="mod-grid">{''.join(cards)}</div>
</section>

<section class="wrap block two-col">
  <div>
    <h2>불러오기</h2>
    <p>RDF 도구(GraphDB, Protégé, rdflib 등)에서 아래 주소를 바로 불러올 수 있습니다.</p>
    <pre><code>@prefix cmdm:   &lt;{e(NS)}&gt; .
@prefix cmdmsh: &lt;{e(SH_NS)}&gt; .

# 최신본
{e(SITE)}ontology/cmdm.ttl
{e(SITE)}shapes/cmdm-shapes.ttl

# 고정본 (v{e(version)})
{e(SITE)}ontology/{e(version)}/cmdm.ttl</code></pre>
  </div>
  <div>
    <h2>IRI 규칙</h2>
    <table class="iri-table">
      <thead><tr><th>대상</th><th>형태</th></tr></thead>
      <tbody>
        <tr><td>스키마</td><td><code>…/cmdm/ontology#{{용어}}</code></td></tr>
        <tr><td>버전</td><td><code>…/cmdm/ontology/{{x.y.z}}</code></td></tr>
        <tr><td>검증 규칙</td><td><code>…/cmdm/shapes#{{셰이프}}</code></td></tr>
        <tr><td>데이터</td><td><code>…/cmdm/id/{{종류}}/{{식별자}}</code></td></tr>
      </tbody>
    </table>
    <p class="muted small">기반 주소는 <code>{e(SITE)}</code> 입니다. 데이터 IRI는 식별자로 쓰며 지금은 웹 페이지로 열리지 않습니다.</p>
  </div>
</section>

<section class="wrap block">
  <h2>버전</h2>
  <p>공개된 버전은 고정되어 바뀌지 않습니다. 스키마를 바꾸면 새 버전 번호로 공개합니다.</p>
  <ul class="versions">{vers}</ul>
</section>

<section class="wrap block note">
  <h2>가상 데이터 고지</h2>
  <p>예시로 쓰인 제품군(VORLA 시리즈, 모델 V4·V7·V15), 컨트롤러, 운영 SW, 에러 코드, 도면번호, 아이템 코드, 수치 제한값, 매뉴얼 절 번호는 모두 이 프로젝트에서 만든 가상 값이며 실제 제품·회사와 관련이 없습니다. 이 사이트는 교육 목적의 비상업 프로젝트입니다.</p>
</section>
</main>"""
    return page(title="CMDM — 협동로봇 제조 데이터 관리 온톨로지",
                description="협동로봇 제조 데이터 관리 온톨로지(CMDM) 스키마와 SHACL 검증 규칙",
                root="", active="home", body=body)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def published_versions(extra: str) -> list[str]:
    found = {p.parent.name for p in DOCS.glob("ontology/*/cmdm.ttl") if re.fullmatch(r"\d+\.\d+\.\d+", p.parent.name)}
    found.add(extra)
    return sorted(found, key=lambda v: tuple(int(x) for x in v.split(".")))


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="검사만 하고 파일은 쓰지 않음")
    ap.add_argument("--force", action="store_true", help="이미 공개된 버전을 덮어씀")
    args = ap.parse_args(argv)

    try:
        ont = ttl.parse_file(SRC_ONT)
        shapes = ttl.parse_file(SRC_SHAPES)
        version, warnings = run_checks(ont, shapes)
        check_frozen(version, args.force)
    except (ttl.TurtleError, BuildError) as exc:
        print(f"빌드 실패:\n{exc}", file=sys.stderr)
        return 1

    for w in warnings:
        print(f"경고: {w}")
    print(f"검사 통과: cmdm.ttl {len(ont.triples)} 트리플, cmdm-shapes.ttl {len(shapes.triples)} 트리플, v{version}")
    if args.check:
        return 0

    versions = published_versions(version)
    for rel_dir in (f"ontology/{version}", "ontology"):
        shutil.copyfile(SRC_ONT, (DOCS / rel_dir).mkdir(parents=True, exist_ok=True) or DOCS / rel_dir / "cmdm.ttl")
    for rel_dir in (f"shapes/{version}", "shapes"):
        shutil.copyfile(SRC_SHAPES, (DOCS / rel_dir).mkdir(parents=True, exist_ok=True) or DOCS / rel_dir / "cmdm-shapes.ttl")

    write(DOCS / "ontology" / "index.html", render_ontology(ont, shapes, version, versions, "../", False))
    write(DOCS / "ontology" / version / "index.html", render_ontology(ont, shapes, version, versions, "../../", True))
    write(DOCS / "shapes" / "index.html", render_shapes(shapes, version, versions, "../", False))
    write(DOCS / "shapes" / version / "index.html", render_shapes(shapes, version, versions, "../../", True))
    write(DOCS / "index.html", render_home(ont, shapes, version, versions))
    (DOCS / ".nojekyll").touch()
    print(f"docs/ 생성 완료 (버전: {', '.join(versions)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
