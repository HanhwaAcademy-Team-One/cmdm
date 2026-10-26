"""Minimal Turtle parser for the CMDM build (standard library only).

Covers the Turtle features used in this repository: @prefix/PREFIX,
IRIs, prefixed names, 'a', literals (short and long strings, language tags,
datatypes, numbers, booleans), blank-node property lists and collections.
It raises TurtleError with a line number on anything it does not understand,
so it also serves as the syntax check in CI.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XSD = "http://www.w3.org/2001/XMLSchema#"


class TurtleError(Exception):
    pass


@dataclass(frozen=True)
class BNode:
    id: str

    def __str__(self) -> str:
        return f"_:{self.id}"


@dataclass(frozen=True)
class Literal:
    value: str
    lang: str | None = None
    datatype: str | None = None

    def __str__(self) -> str:
        return self.value


_TOKEN = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<comment>\#[^\n]*)
    | (?P<long>\"\"\"(?:[^"\\]|\\.|"(?!""))*\"\"\")
    | (?P<string>"(?:[^"\\\n]|\\.)*")
    | (?P<iri><[^<>"{}|^`\\\s]*>)
    | (?P<lang>@[a-zA-Z]+(?:-[a-zA-Z0-9]+)*)
    | (?P<dt>\^\^)
    | (?P<number>[+-]?(?:\d+\.\d+|\.\d+|\d+)(?:[eE][+-]?\d+)?)
    | (?P<pname>(?:[A-Za-z][\w\-.]*)?:(?:[\w\-]+(?:[\w\-.]*[\w\-])?)?)
    | (?P<word>[A-Za-z]+)
    | (?P<punct>[.;,\[\]()])
    """,
    re.VERBOSE | re.DOTALL,
)

_ESCAPES = {"t": "\t", "b": "\b", "n": "\n", "r": "\r", "f": "\f", '"': '"', "'": "'", "\\": "\\"}


def _unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n in _ESCAPES:
                out.append(_ESCAPES[n]); i += 2; continue
            if n == "u":
                out.append(chr(int(s[i + 2:i + 6], 16))); i += 6; continue
            if n == "U":
                out.append(chr(int(s[i + 2:i + 10], 16))); i += 10; continue
        out.append(c); i += 1
    return "".join(out)


def _tokenize(text: str):
    pos, line = 0, 1
    toks = []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise TurtleError(f"line {line}: cannot read {text[pos:pos + 30]!r}")
        kind = m.lastgroup
        val = m.group(kind)
        if kind not in ("ws", "comment"):
            toks.append((kind, val, line))
        line += val.count("\n")
        pos = m.end()
    return toks


class Graph:
    def __init__(self):
        self.triples: list[tuple] = []
        self.prefixes: dict[str, str] = {}
        self._sp = defaultdict(lambda: defaultdict(list))
        self._po = defaultdict(lambda: defaultdict(list))

    def add(self, s, p, o):
        self.triples.append((s, p, o))
        self._sp[s][p].append(o)
        self._po[p][o].append(s)

    def objects(self, s, p):
        return list(self._sp.get(s, {}).get(p, []))

    def value(self, s, p, default=None):
        vals = self.objects(s, p)
        return vals[0] if vals else default

    def subjects(self, p, o):
        return list(self._po.get(p, {}).get(o, []))

    def predicates(self, s):
        return list(self._sp.get(s, {}).keys())

    def list_items(self, node):
        items, seen = [], set()
        while node != RDF + "nil":
            if node in seen:
                raise TurtleError("cyclic rdf:List")
            seen.add(node)
            items.append(self.value(node, RDF + "first"))
            node = self.value(node, RDF + "rest", RDF + "nil")
        return items

    def qname(self, iri: str) -> str:
        best = None
        for pfx, ns in self.prefixes.items():
            if iri.startswith(ns) and (best is None or len(ns) > len(self.prefixes[best])):
                best = pfx
        return f"{best}:{iri[len(self.prefixes[best]):]}" if best is not None else iri


class _Parser:
    def __init__(self, text: str):
        self.toks = _tokenize(text)
        self.i = 0
        self.g = Graph()
        self._bn = 0

    # -- helpers --------------------------------------------------------
    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else (None, None, self.toks[-1][2] if self.toks else 0)

    def take(self):
        t = self.peek(); self.i += 1; return t

    def expect(self, val):
        kind, v, line = self.take()
        if v != val:
            raise TurtleError(f"line {line}: expected {val!r}, got {v!r}")

    def fresh(self):
        self._bn += 1
        return BNode(f"b{self._bn}")

    def iri_of(self, kind, v, line):
        if kind == "iri":
            return _unescape(v[1:-1])
        if kind == "pname":
            pfx, local = v.split(":", 1)
            if pfx not in self.g.prefixes:
                raise TurtleError(f"line {line}: undeclared prefix {pfx!r}:")
            return self.g.prefixes[pfx] + local
        raise TurtleError(f"line {line}: expected IRI, got {v!r}")

    # -- grammar --------------------------------------------------------
    def parse(self):
        while self.i < len(self.toks):
            kind, v, line = self.peek()
            if kind == "lang" and v == "@prefix":
                self.take()
                pk, pv, pl = self.take()
                if pk != "pname" or not pv.endswith(":"):
                    raise TurtleError(f"line {pl}: bad prefix name {pv!r}")
                ik, iv, il = self.take()
                self.g.prefixes[pv[:-1]] = self.iri_of(ik, iv, il)
                self.expect(".")
            elif kind == "word" and v.upper() == "PREFIX":
                self.take()
                pk, pv, pl = self.take()
                ik, iv, il = self.take()
                self.g.prefixes[pv[:-1]] = self.iri_of(ik, iv, il)
            else:
                self.triples()
                self.expect(".")
        return self.g

    def triples(self):
        kind, v, line = self.peek()
        if v == "[":
            subj = self.blank_property_list()
            if self.peek()[1] != ".":
                self.predicate_object_list(subj)
        elif v == "(":
            subj = self.collection()
            self.predicate_object_list(subj)
        else:
            self.take()
            subj = self.iri_of(kind, v, line)
            self.predicate_object_list(subj)

    def predicate_object_list(self, subj):
        while True:
            kind, v, line = self.take()
            pred = RDF + "type" if (kind == "word" and v == "a") else self.iri_of(kind, v, line)
            self.object_list(subj, pred)
            if self.peek()[1] != ";":
                return
            while self.peek()[1] == ";":
                self.take()
            if self.peek()[1] in (".", "]"):
                return

    def object_list(self, subj, pred):
        while True:
            self.g.add(subj, pred, self.object())
            if self.peek()[1] != ",":
                return
            self.take()

    def blank_property_list(self):
        self.expect("[")
        node = self.fresh()
        if self.peek()[1] != "]":
            self.predicate_object_list(node)
        self.expect("]")
        return node

    def collection(self):
        self.expect("(")
        items = []
        while self.peek()[1] != ")":
            if self.peek()[0] is None:
                raise TurtleError("unterminated collection")
            items.append(self.object())
        self.expect(")")
        head = RDF + "nil"
        for item in reversed(items):
            node = self.fresh()
            self.g.add(node, RDF + "first", item)
            self.g.add(node, RDF + "rest", head)
            head = node
        return head

    def object(self):
        kind, v, line = self.peek()
        if v == "[":
            return self.blank_property_list()
        if v == "(":
            return self.collection()
        self.take()
        if kind in ("string", "long"):
            raw = v[3:-3] if kind == "long" else v[1:-1]
            value = _unescape(raw)
            nk, nv, nl = self.peek()
            if nk == "lang":
                self.take()
                return Literal(value, lang=nv[1:])
            if nk == "dt":
                self.take()
                dk, dv, dl = self.take()
                return Literal(value, datatype=self.iri_of(dk, dv, dl))
            return Literal(value, datatype=XSD + "string")
        if kind == "number":
            if re.search(r"[eE]", v):
                dt = "double"
            elif "." in v:
                dt = "decimal"
            else:
                dt = "integer"
            return Literal(v, datatype=XSD + dt)
        if kind == "word" and v in ("true", "false"):
            return Literal(v, datatype=XSD + "boolean")
        return self.iri_of(kind, v, line)


def parse(text: str) -> Graph:
    return _Parser(text).parse()


def parse_file(path) -> Graph:
    with open(path, encoding="utf-8") as f:
        return parse(f.read())
