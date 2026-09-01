#!/usr/bin/env python3
"""Verify the public rules JSON and static catalogs against their sources.

Boundary: this is a structural/static gate. It does not execute a browser,
compute CSS, prove focus order, or observe screen-reader output.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
import check_applies  # noqa: E402
import gen_agents  # noqa: E402
import gen_rules  # noqa: E402

DOC_REL = "docs/rules.md"
HTML_REL = "site/rules.html"
JSON_REL = "site/downloads/ruleset.json"
JS_REL = "site/js/rules.js"
ENFORCEMENT_REL = ".aiqt/enforceability.json"
BEGIN = "<!-- RULESET:BEGIN (generated) -->"
END = "<!-- RULESET:END -->"
FACET_ORDER = (
    "apex", "ACCUR", "INTEG", "QUALI", "TRUST", "PROGR",
    "SPEED", "COST", "SECC", "SECI", "SECA", "SECP",
)
ENFORCEMENT = {"prose-only", "hook-linked", "gate-linked"}
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


def canonical_bytes(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_enforcement(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {
        "version", "boundary", "rules"
    }:
        raise ValueError(
            "enforceability.json has an unexpected top-level shape"
        )
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError(
            "enforceability.json version must be integer 1"
        )
    if not isinstance(data["boundary"], str) or not data["boundary"].strip():
        raise ValueError(
            "enforceability.json boundary must be a non-empty string"
        )
    if not isinstance(data["rules"], list) or not data["rules"]:
        raise ValueError(
            "enforceability.json rules must be a non-empty list"
        )

    rows = {}
    for index, row in enumerate(data["rules"], 1):
        if not isinstance(row, dict):
            raise ValueError(
                "enforceability rule #{} must be an object".format(
                    index
                )
            )
        cid = row.get("corpus-id")
        status = row.get("status")
        if (
            not isinstance(cid, str)
            or not gen_rules.CID_RE.fullmatch(cid)
            or cid in rows
        ):
            raise ValueError(
                "enforceability rule #{} has an invalid or "
                "duplicate corpus-id".format(index)
            )
        if status not in ENFORCEMENT:
            raise ValueError(
                "{}: invalid enforceability status".format(cid)
            )
        rows[cid] = status
    return rows


def source_model(root):
    applicability = check_applies.load_applicability_model(
        root / check_applies.APPLICABILITY_REL
    )
    assignments = check_applies.load_assignments(
        root / check_applies.APPLIES_REL
    )
    corpus = gen_rules.load_corpus(
        root / check_applies.RULES_REL
    )
    if not corpus:
        raise ValueError("rule corpus is empty")

    order = {
        row["slug"]: index
        for index, row in enumerate(applicability["condition"])
    }
    corpus_ids = [
        str(frontmatter["corpus-id"])
        for _path, frontmatter, _rel in corpus
    ]
    findings = check_applies.coverage_findings(
        corpus_ids, assignments, frozenset(order)
    )
    if findings:
        raise ValueError("; ".join(findings))

    enforcement = load_enforcement(root / ENFORCEMENT_REL)
    if set(enforcement) != set(corpus_ids):
        raise ValueError(
            "enforceability rows do not equal the corpus"
        )

    ordered = sorted(
        corpus, key=lambda item: gen_agents.sort_key(item[1])
    )
    public = []
    page = []
    for source, frontmatter, _derived in ordered:
        cid = str(frontmatter["corpus-id"])
        row = {
            "corpus-id": cid,
            "title": gen_rules.rule_title(source),
            "applies": sorted(
                assignments[cid], key=order.__getitem__
            ),
            "facet": frontmatter.get("facet"),
            "tier": frontmatter.get("tier"),
            "family": frontmatter["family"],
            "enforcement": enforcement[cid],
        }
        public.append(row)
        page.append({
            **row,
            "group": (
                "apex"
                if frontmatter.get("apex") is True
                else frontmatter["facet"]
            ),
        })

    without_digest = {
        "schema": 1,
        "conditions": applicability["condition"],
        "profiles": applicability["profile"],
        "rules": public,
    }
    digest = hashlib.sha256(
        canonical_bytes(without_digest)
    ).hexdigest()
    return {
        **without_digest,
        "model-sha256": digest,
    }, page


class CatalogParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.errors = []
        self.stack = []
        self.view = None
        self.group = None
        self.profile = None
        self.capture = None
        self.root_digests = []
        self.primary_ids = []
        self.group_members = defaultdict(list)
        self.group_counts = {}
        self.profile_counts = {}
        self.corpus_counts = []
        self.anchor_ids = []
        self.views = set()
        self.builder_hidden = []
        self.switcher_hidden = []
        self.url_notice_hidden = []
        self.always_controls = []
        self.status_visible = []
        self.scripts = []
        self.inline_json_scripts = 0

    @staticmethod
    def attrs(attrs):
        names = [name for name, _value in attrs]
        duplicates = sorted({
            name for name in names if names.count(name) > 1
        })
        return {name: ("" if value is None else value) for name, value in attrs}, duplicates

    def handle_starttag(self, tag, attrs):
        values, duplicates = self.attrs(attrs)
        if duplicates:
            self.errors.append(
                "{} carries duplicate attribute(s): {}".format(
                    tag, ", ".join(duplicates)
                )
            )

        marker = {
            "tag": tag,
            "old-view": self.view,
            "old-group": self.group,
            "old-profile": self.profile,
            "capture": self.capture,
        }
        self.stack.append(marker)
        classes = (values.get("class") or "").split()

        if (
            "rules-app" in classes
            and "data-rules-app" in values
        ):
            self.root_digests.append(
                values.get("data-model-sha256")
            )
        if "data-rules-load-status" in values:
            self.status_visible.append(
                "hidden" not in values
            )
        if "data-view" in values:
            self.view = values["data-view"]
            self.views.add(self.view)
        if "data-group" in values:
            self.group = values["data-group"]
            self.group_members[(self.view, self.group)]
        if "data-profile" in values:
            self.profile = values["data-profile"]
        if "data-builder" in values:
            self.builder_hidden.append("hidden" in values)
        if "data-view-switcher" in values:
            self.switcher_hidden.append("hidden" in values)
        if "data-url-notice" in values:
            self.url_notice_hidden.append("hidden" in values)

        if (
            tag == "input"
            and values.get("data-condition") == "always"
        ):
            self.always_controls.append(
                "checked" in values and "disabled" in values
            )

        if "data-rule-id" in values:
            cid = values["data-rule-id"]
            if self.view == "facet":
                self.primary_ids.append(cid)
            if self.view is not None and self.group is not None:
                self.group_members[
                    (self.view, self.group)
                ].append(cid)
            else:
                self.errors.append(
                    "rule {} is outside a data-view/data-group".format(
                        cid
                    )
                )

        if "id" in values:
            self.anchor_ids.append(values["id"])

        if "data-group-count" in values:
            self.capture = [
                "group", (self.view, self.group), []
            ]
        elif "data-profile-count" in values:
            self.capture = ["profile", self.profile, []]
        elif "data-corpus-count" in values:
            self.capture = ["corpus", None, []]

        if tag == "script":
            if values.get("src"):
                self.scripts.append((
                    values["src"],
                    "defer" in values,
                    values.get("type"),
                ))
            elif values.get("type") == "application/json":
                self.inline_json_scripts += 1

        if tag in VOID:
            self._finish(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self._finish(tag)

    def handle_data(self, data):
        if self.capture is not None:
            self.capture[2].append(data)

    def handle_endtag(self, tag):
        self._finish(tag)

    def _finish(self, tag):
        if not self.stack:
            self.errors.append(
                "unexpected closing tag {}".format(tag)
            )
            return
        marker = self.stack.pop()
        if marker["tag"] != tag:
            self.errors.append(
                "mismatched closing tag {}, expected {}".format(
                    tag, marker["tag"]
                )
            )

        if (
            self.capture is not marker["capture"]
            and self.capture is not None
        ):
            kind, key, chunks = self.capture
            raw = "".join(chunks).strip()
            try:
                number = int(raw)
            except ValueError:
                self.errors.append(
                    "{} count {!r} is not an integer".format(
                        kind, raw
                    )
                )
            else:
                if kind == "group":
                    if key in self.group_counts:
                        self.errors.append(
                            "duplicate displayed count for {}".format(
                                key
                            )
                        )
                    self.group_counts[key] = number
                elif kind == "profile":
                    if key in self.profile_counts:
                        self.errors.append(
                            "duplicate displayed profile count "
                            "for {}".format(key)
                        )
                    self.profile_counts[key] = number
                else:
                    self.corpus_counts.append(number)

        self.capture = marker["capture"]
        self.view = marker["old-view"]
        self.group = marker["old-group"]
        self.profile = marker["old-profile"]

    def close(self):
        super().close()
        if self.stack:
            self.errors.append("unclosed HTML element(s)")


def expected_groups(payload, rules):
    groups = {}
    for key in FACET_ORDER:
        ids = {
            rule["corpus-id"]
            for rule in rules
            if rule["group"] == key
        }
        if ids:
            groups[("facet", key)] = ids

    for condition in payload["conditions"]:
        slug = condition["slug"]
        groups[("set", slug)] = {
            rule["corpus-id"]
            for rule in rules
            if slug in rule["applies"]
        }

    for profile in payload["profiles"]:
        selected = set(profile["conditions"])
        groups[("profile", profile["slug"])] = {
            rule["corpus-id"]
            for rule in rules
            if selected.intersection(rule["applies"])
        }
    return groups


def inspect_catalog(catalog, where, payload, rules):
    findings = [
        "{}: {}".format(where, error)
        for error in catalog.errors
    ]
    expected_ids = [rule["corpus-id"] for rule in rules]

    if catalog.root_digests != [payload["model-sha256"]]:
        findings.append(
            "{}: expected exactly one matching "
            "data-model-sha256".format(where)
        )

    if (
        Counter(catalog.primary_ids) != Counter(expected_ids)
        or len(catalog.primary_ids) != len(expected_ids)
    ):
        findings.append(
            "{}: by-facet primary IDs do not equal the corpus "
            "exactly once".format(where)
        )

    expected = expected_groups(payload, rules)
    actual = {
        key: set(ids)
        for key, ids in catalog.group_members.items()
    }
    if actual != expected:
        findings.append(
            "{}: by-facet/by-set/by-profile membership does not "
            "equal source expansion".format(where)
        )

    for key, ids in catalog.group_members.items():
        repeated = sorted(
            cid for cid, count in Counter(ids).items()
            if count != 1
        )
        if repeated:
            findings.append(
                "{}: group {} repeats corpus-id(s): {}".format(
                    where, key, ", ".join(repeated)
                )
            )

    expected_counts = {
        key: len(ids) for key, ids in expected.items()
    }
    if catalog.group_counts != expected_counts:
        findings.append(
            "{}: displayed group counts do not equal their "
            "distinct memberships".format(where)
        )

    profile_counts = {
        profile["slug"]: len(
            expected[("profile", profile["slug"])]
        )
        for profile in payload["profiles"]
    }
    if catalog.profile_counts != profile_counts:
        findings.append(
            "{}: displayed profile counts do not equal their "
            "expansions".format(where)
        )

    if catalog.corpus_counts != [len(expected_ids)]:
        findings.append(
            "{}: displayed corpus count does not equal the live "
            "corpus".format(where)
        )

    if catalog.views != {"facet", "set", "profile"}:
        findings.append(
            "{}: the three static browse views are absent".format(
                where
            )
        )
    if catalog.builder_hidden != [True]:
        findings.append(
            "{}: builder must occur once and be initially "
            "hidden".format(where)
        )
    if catalog.switcher_hidden != [True]:
        findings.append(
            "{}: view switcher must occur once and be initially "
            "hidden".format(where)
        )
    if catalog.url_notice_hidden != [True]:
        findings.append(
            "{}: URL notice must occur once and be initially "
            "hidden".format(where)
        )
    if (
        not catalog.always_controls
        or not all(catalog.always_controls)
    ):
        findings.append(
            "{}: every always control must be checked and "
            "disabled".format(where)
        )
    if catalog.status_visible != [True]:
        findings.append(
            "{}: fallback status must occur once and be initially "
            "visible".format(where)
        )

    anchors = Counter(catalog.anchor_ids)
    duplicates = sorted(
        anchor for anchor, count in anchors.items()
        if count > 1
    )
    if duplicates:
        findings.append(
            "{}: duplicate anchor(s): {}".format(
                where, ", ".join(duplicates)
            )
        )
    if not {
        "rule-" + cid for cid in expected_ids
    }.issubset(anchors):
        findings.append(
            "{}: one or more primary rule anchors are "
            "absent".format(where)
        )

    scripts = [
        row for row in catalog.scripts
        if urlsplit(row[0]).path == "/js/rules.js"
    ]
    if (
        len(scripts) != 1
        or scripts[0][1] is not True
        or scripts[0][2] is not None
    ):
        findings.append(
            "{}: rules.js must occur once as a deferred "
            "executable external script".format(where)
        )
    if catalog.inline_json_scripts:
        findings.append(
            "{}: inline JSON is forbidden".format(where)
        )
    return findings


def parse_catalog(value):
    parser = CatalogParser()
    parser.feed(value)
    parser.close()
    return parser


def _strip_comments(code):
    """Remove // line and /* */ block comments that sit OUTSIDE string literals, via a small state
    machine tracking ', ", and ` strings (with backslash escapes). A // inside a URL string
    ("https://x") therefore cannot mask the rest of the line. Recognizable-subset lexer (no JS parser):
    residual (disclosed) is template-literal ${...} nesting - code inside a backtick ${...} is treated
    as string content, not re-scanned."""
    out = []
    i, n = 0, len(code)
    quote = None
    while i < n:
        c = code[i]
        if quote is not None:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and code[i + 1] == "/":
            while i < n and code[i] not in "\n\r\u2028\u2029":
                i += 1
            continue
        if c == "/" and i + 1 < n and code[i + 1] == "*":
            i += 2
            while i + 1 < n and not (code[i] == "*" and code[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def network_findings(js_text):
    """Findings for rules.js network egress: a BEST-EFFORT recognizable-subset LEXICAL tripwire for the
    ACCIDENTAL case (a maintainer adding a fetch/transport), NOT an adversarial-grade denylist. It strips
    comments (string-aware), then requires exactly one fetch call, a string literal to
    /downloads/ruleset.json, and rejects the named transport primitives.

    COVERAGE BOUNDARY (disclosed): a regex/state-machine scan cannot soundly tokenize JavaScript, so a
    DETERMINED author can evade it. It does NOT catch: an aliased fetch binding (var f = window.fetch;
    f(x)); a computed-member call (window["fetch"](x)); a dynamic import("..."); an Image()/img or
    script-element src assignment; a regex literal adjacent to a quote that desynchronizes string
    tracking; template-literal ${...} nesting; or any egress whose tokens it does not name. The transport
    check catches ONLY XMLHttpRequest, EventSource, WebSocket, and sendBeacon. The REAL egress controls
    are code review of this pack-immutable file and the tri-family QA; this scan is defence-in-depth for
    the common case. The gate's manifest residue and its
    class-c letter state the same boundary."""
    findings = []
    code = _strip_comments(js_text)
    all_fetches = re.findall(r"\bfetch\s*\(", code)
    literal_fetches = re.findall(r"\bfetch\s*\(\s*([\"\'])(.*?)\1", code)
    if (len(all_fetches) != 1
            or [url for _quote, url in literal_fetches] != ["/downloads/ruleset.json"]):
        findings.append(
            "rules.js must make exactly one fetch call, a string literal "
            "to /downloads/ruleset.json (a variable or computed fetch "
            "target is rejected)")
    if re.search(r"\b(?:XMLHttpRequest|EventSource|WebSocket|sendBeacon)\b", code):
        findings.append("rules.js carries an additional network data path")
    return findings


def run(root):
    root = Path(root).resolve()
    try:
        payload, rules = source_model(root)
        actual_json = json.loads(
            (root / JSON_REL).read_text(encoding="utf-8")
        )
        docs_text = (root / DOC_REL).read_text(
            encoding="utf-8"
        )
        site_text = (root / HTML_REL).read_text(
            encoding="utf-8"
        )
        js_text = (root / JS_REL).read_text(
            encoding="utf-8"
        )
    except (
        OSError, UnicodeError, ValueError, KeyError,
        json.JSONDecodeError,
    ) as exc:
        print(
            "error: cannot evaluate rules page ({}); "
            "fail-closed".format(exc),
            file=sys.stderr,
        )
        return 2

    findings = []
    if actual_json != payload:
        findings.append(
            "ruleset.json does not exactly match applicability, "
            "assignments, corpus, and enforcement"
        )

    if (
        docs_text.count(BEGIN) != 1
        or docs_text.count(END) != 1
        or docs_text.find(BEGIN) > docs_text.find(END)
    ):
        findings.append(
            "docs/rules.md does not carry exactly one ordered "
            "RULESET marker pair"
        )
    else:
        outside = (
            docs_text[:docs_text.find(BEGIN)]
            + docs_text[
                docs_text.find(END) + len(END):
            ]
        )
        if re.search(
            r"\b[0-9]+\s+rules?\b",
            outside,
            re.IGNORECASE,
        ):
            findings.append(
                "docs/rules.md carries a literal rule count "
                "outside the generated block"
            )

    findings.extend(inspect_catalog(
        parse_catalog(docs_text), DOC_REL, payload, rules
    ))
    findings.extend(inspect_catalog(
        parse_catalog(site_text), HTML_REL, payload, rules
    ))

    findings.extend(network_findings(js_text))

    if findings:
        print(
            "FAIL: {} rules-page consistency finding(s)".format(
                len(findings)
            )
        )
        for finding in findings:
            print("  " + finding)
        return 1

    print(
        "PASS: JSON and both static catalogs match the "
        "enumerated corpus and source expansions. Boundary: "
        "structural/static only; no browser, CSS, focus-order, "
        "or screen-reader execution."
    )
    return 0


def self_test_main():
    # The parser/model components are exercised over a closed synthetic
    # catalog without invoking the live repository.
    payload = {
        "schema": 1,
        "model-sha256": "0" * 64,
        "conditions": [
            {
                "slug": "always",
                "question": "Always?",
                "description": "Floor.",
            }
        ],
        "profiles": [
            {
                "name": "Full corpus",
                "slug": "full-corpus",
                "conditions": ["always"],
            }
        ],
        "rules": [{
            "corpus-id": "selfr1",
            "title": "Fixture",
            "applies": ["always"],
            "facet": "ACCUR",
            "tier": 10,
            "family": "aiqt",
            "enforcement": "prose-only",
            "group": "ACCUR",
        }],
    }
    fixture = (
        '<p data-rules-load-status>Fallback.</p>'
        '<div class="rules-app" data-rules-app '
        'data-model-sha256="{digest}">'
        '<strong data-corpus-count>1</strong>'
        '<section data-builder hidden>'
        '<p data-url-notice hidden></p>'
        '<input data-condition="always" checked disabled>'
        '<button data-profile="full-corpus">'
        '<span data-profile-count>1</span></button></section>'
        '<div data-view-switcher hidden></div>'
        '<section data-view="facet">'
        '<details data-group="ACCUR"><summary>'
        '<span data-group-count>1</span></summary>'
        '<article id="rule-selfr1" data-rule-id="selfr1">'
        '<span data-rule-state></span></article></details></section>'
        '<section data-view="set">'
        '<details data-group="always"><summary>'
        '<span data-group-count>1</span></summary>'
        '<li data-rule-id="selfr1">'
        '<span data-rule-state></span></li></details></section>'
        '<section data-view="profile">'
        '<details data-group="full-corpus"><summary>'
        '<span data-group-count>1</span></summary>'
        '<li data-rule-id="selfr1">'
        '<span data-rule-state></span></li></details></section>'
        '</div><script src="/js/rules.js?v=test" defer></script>'
    ).format(digest=payload["model-sha256"])

    clean = inspect_catalog(
        parse_catalog(fixture),
        "fixture",
        payload,
        payload["rules"],
    )
    if clean:
        print("SELF-TEST FAIL: " + "; ".join(clean))
        return 1

    bad = inspect_catalog(
        parse_catalog(
            fixture.replace(
                'data-model-sha256="',
                'data-model-sha256="1',
                1,
            )
        ),
        "fixture",
        payload,
        payload["rules"],
    )
    if not any("data-model-sha256" in finding for finding in bad):
        print(
            "SELF-TEST FAIL: fingerprint corruption was not caught"
        )
        return 1

    duplicate = inspect_catalog(
        parse_catalog(
            fixture.replace(
                '<section data-view="set">',
                '<div id="rule-selfr1"></div>'
                '<section data-view="set">',
            )
        ),
        "fixture",
        payload,
        payload["rules"],
    )
    if not any("duplicate anchor" in finding for finding in duplicate):
        print(
            "SELF-TEST FAIL: duplicate anchor was not caught"
        )
        return 1

    if network_findings('fetch("/downloads/ruleset.json", {cache: "no-cache"});'):
        print("SELF-TEST FAIL: a clean single-literal fetch was flagged")
        return 1
    for label, sample in (
        ("variable fetch target", 'var e = "/x"; fetch(e); fetch("/downloads/ruleset.json");'),
        ("second literal fetch", 'fetch("/downloads/ruleset.json"); fetch("/x");'),
        ("no sanctioned fetch", 'fetch("/other.json");'),
        ("transport primitive", 'fetch("/downloads/ruleset.json"); new WebSocket("wss://x");'),
        ("slash-in-string masking a transport",
         'fetch("/downloads/ruleset.json"); var u = "http://x"; new WebSocket(u);'),
        ("unicode line terminator ends a comment",
         'fetch("/downloads/ruleset.json"); // note\u2028new WebSocket("wss://x");'),
        ("carriage-return ends a comment",
         'fetch("/downloads/ruleset.json"); // note\rnew WebSocket("wss://x");'),
        ("U+2029 ends a comment",
         'fetch("/downloads/ruleset.json"); // note\u2029new WebSocket("wss://x");'),
    ):
        if not network_findings(sample):
            print("SELF-TEST FAIL: network scan missed the " + label)
            return 1
    if network_findings('var w = window; w["fetch"]("/x"); fetch("/downloads/ruleset.json");'):
        print("SELF-TEST FAIL: computed-member residual unexpectedly flagged")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="aiqt-rulespage-selftest-"))
    try:
        rows = [{"corpus-id": "selfr1", "status": "prose-only"}]
        ok = tmp / "ok.json"
        ok.write_text(json.dumps({"version": 1, "boundary": "b", "rules": rows}), encoding="utf-8")
        if load_enforcement(ok) != {"selfr1": "prose-only"}:
            print("SELF-TEST FAIL: a valid enforcement ledger did not load")
            return 1
        for label, boundary in (("null", None), ("empty", ""), ("whitespace", "  ")):
            bad = tmp / "bad.json"
            bad.write_text(json.dumps({"version": 1, "boundary": boundary, "rules": rows}), encoding="utf-8")
            try:
                load_enforcement(bad)
                print("SELF-TEST FAIL: a " + label + " boundary was accepted")
                return 1
            except ValueError:
                pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(
        "SELF-TEST PASS: a conformant synthetic catalog passes; fingerprint and "
        "duplicate-anchor corruptions fail; the network scan catches variable/second/"
        "transport egress while the disclosed computed-member residual slips; and a "
        "malformed enforcement boundary fails closed."
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test_main()
    return run(args.root or repo_root())


if __name__ == "__main__":
    raise SystemExit(main())
