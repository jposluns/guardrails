#!/usr/bin/env python3
"""Generate the public ruleset JSON and the no-JavaScript catalog. Stdlib only."""
import argparse
import hashlib
import html
import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import reconcile, replace_block, repo_root  # noqa: E402
import check_applies  # noqa: E402
import gen_agents  # noqa: E402
import gen_rules  # noqa: E402

GENSRC_OUTPUTS = (
    {"target": "docs/rules.md", "kind": "block",
     "sources": (".aiqt/core/applicability.toml", ".aiqt/core/applies.toml",
                 ".aiqt/core/rules/", ".aiqt/enforceability.json"),
     "regenerate": "python3 tools/gen_ruleset.py"},
    {"target": "site/downloads/ruleset.json", "kind": "file",
     "sources": (".aiqt/core/applicability.toml", ".aiqt/core/applies.toml",
                 ".aiqt/core/rules/", ".aiqt/enforceability.json"),
     "regenerate": "python3 tools/gen_ruleset.py"},
)

FACET_ORDER = (
    "apex", "ACCUR", "INTEG", "QUALI", "TRUST", "PROGR",
    "SPEED", "COST", "SECC", "SECI", "SECA", "SECP",
)
FACET_LABEL = {
    "apex": "The apex",
    "ACCUR": "Accuracy",
    "INTEG": "Integrity",
    "QUALI": "Quality",
    "TRUST": "Trust",
    "PROGR": "Progress",
    "SPEED": "Speed",
    "COST": "Cost",
    "SECC": "Security confidentiality",
    "SECI": "Security integrity",
    "SECA": "Security availability",
    "SECP": "Privacy",
}
CONTEXT_FLAGS = ("personal-data", "multi-agent")
ENFORCEMENT_LABEL = {
    "prose-only": "Prose",
    "hook-linked": "Hook",
    "gate-linked": "Gate",
}
ENFORCEMENT_REL = ".aiqt/enforceability.json"
RULESET_REL = "site/downloads/ruleset.json"
DOC_REL = "docs/rules.md"


def text(value):
    return html.escape(str(value), quote=False)


def attr(value):
    return html.escape(str(value), quote=True)


def canonical_bytes(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_enforcement(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"version", "boundary", "rules"}:
        raise ValueError("enforceability.json has an unexpected top-level shape")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("enforceability.json version must be integer 1")
    if not isinstance(data["boundary"], str) or not data["boundary"].strip():
        raise ValueError("enforceability.json boundary must be a non-empty string")
    if not isinstance(data["rules"], list) or not data["rules"]:
        raise ValueError("enforceability.json rules must be a non-empty list")

    rows = {}
    for index, row in enumerate(data["rules"], 1):
        if not isinstance(row, dict):
            raise ValueError(
                "enforceability.json rule #{} must be an object".format(index)
            )
        cid = row.get("corpus-id")
        status = row.get("status")
        if not isinstance(cid, str) or not gen_rules.CID_RE.fullmatch(cid):
            raise ValueError(
                "enforceability.json rule #{} has an invalid corpus-id".format(index)
            )
        if cid in rows:
            raise ValueError("duplicate enforceability row: {}".format(cid))
        if status not in ENFORCEMENT_LABEL:
            raise ValueError(
                "{}: unknown enforceability status".format(cid)
            )
        rows[cid] = status
    return rows


def effective_ids(rules, selected):
    selected = set(selected)
    return {
        rule["corpus-id"]
        for rule in rules
        if selected.intersection(rule["applies"])
    }


def build(root):
    applicability = check_applies.load_applicability_model(
        root / check_applies.APPLICABILITY_REL
    )
    assignments = check_applies.load_assignments(
        root / check_applies.APPLIES_REL
    )
    corpus = gen_rules.load_corpus(root / check_applies.RULES_REL)
    if not corpus:
        raise ValueError("rule corpus is empty")

    condition_order = {
        row["slug"]: index
        for index, row in enumerate(applicability["condition"])
    }
    known = frozenset(condition_order)
    for slug in CONTEXT_FLAGS:
        if slug not in known:
            raise ValueError(
                "page context flag {!r} is absent from applicability.toml".format(
                    slug
                )
            )

    corpus_ids = [
        str(frontmatter["corpus-id"])
        for _path, frontmatter, _rel in corpus
    ]
    findings = check_applies.coverage_findings(
        corpus_ids, assignments, known
    )
    if findings:
        raise ValueError("; ".join(findings))

    enforcement = load_enforcement(root / ENFORCEMENT_REL)
    if set(enforcement) != set(corpus_ids):
        missing = sorted(set(corpus_ids) - set(enforcement))
        orphan = sorted(set(enforcement) - set(corpus_ids))
        raise ValueError(
            "enforceability rows do not equal the corpus "
            "(missing: {}; orphan: {})".format(
                ", ".join(missing) or "none",
                ", ".join(orphan) or "none",
            )
        )

    ordered = sorted(
        corpus, key=lambda item: gen_agents.sort_key(item[1])
    )
    public_rules = []
    page_rules = []

    for source, frontmatter, _derived in ordered:
        cid = str(frontmatter["corpus-id"])
        applies = sorted(
            assignments[cid], key=condition_order.__getitem__
        )
        public = {
            "corpus-id": cid,
            "title": gen_rules.rule_title(source),
            "applies": applies,
            "facet": frontmatter.get("facet"),
            "tier": frontmatter.get("tier"),
            "family": frontmatter["family"],
            "enforcement": enforcement[cid],
        }
        public_rules.append(public)
        page_rules.append({
            **public,
            "source": source.relative_to(root).as_posix(),
            "group": (
                "apex"
                if frontmatter.get("apex") is True
                else frontmatter["facet"]
            ),
        })

    payload_without_digest = {
        "schema": 1,
        "conditions": applicability["condition"],
        "profiles": applicability["profile"],
        "rules": public_rules,
    }
    digest = hashlib.sha256(
        canonical_bytes(payload_without_digest)
    ).hexdigest()
    return {
        **payload_without_digest,
        "model-sha256": digest,
    }, page_rules


def primary_rule_row(rule):
    badges = "".join(
        '<span class="rules-badge">{}</span>'.format(text(condition))
        for condition in rule["applies"]
    )
    source_url = (
        "https://github.com/jposluns/guardrails/blob/main/"
        + rule["source"]
    )
    return (
        '<article class="rules-row" id="rule-{cid}" '
        'data-rule-id="{cid}">'
        '<h3>{title}</h3>'
        '<p class="rules-row-state" data-rule-state></p>'
        '<p class="rules-badges">{badges}'
        '<span class="rules-overlap" data-overlap hidden>'
        'overlap</span></p>'
        '<p class="rules-meta">{family} · {enforcement}</p>'
        '<a href="{source}" target="_blank" '
        'rel="noopener noreferrer">Read the source rule</a>'
        '</article>'
    ).format(
        cid=attr(rule["corpus-id"]),
        title=text(rule["title"]),
        badges=badges,
        family=text(rule["family"]),
        enforcement=text(ENFORCEMENT_LABEL[rule["enforcement"]]),
        source=attr(source_url),
    )


def compact_rule_row(rule):
    return (
        '<li class="rules-list-row" data-rule-id="{cid}">'
        '<a href="#rule-{cid}">{title}</a> '
        '<span data-rule-state></span>'
        '<span class="rules-overlap" data-overlap hidden>'
        'overlap</span></li>'
    ).format(
        cid=attr(rule["corpus-id"]),
        title=text(rule["title"]),
    )


def group(key, label, rules, primary=False):
    rows = "".join(
        primary_rule_row(rule) if primary else compact_rule_row(rule)
        for rule in rules
    )
    if primary:
        container = '<div class="inner">{}</div>'.format(rows)
    else:
        container = '<ul class="inner">{}</ul>'.format(rows)
    return (
        '<details class="more rules-group" data-group="{key}" open>'
        '<summary>{label} '
        '<span class="rules-count" data-group-count>{count}</span>'
        '</summary>{rows}</details>'
    ).format(
        key=attr(key),
        label=text(label),
        count=len({rule["corpus-id"] for rule in rules}),
        rows=container,
    )


def render_block(payload, rules):
    by_group = {
        key: [rule for rule in rules if rule["group"] == key]
        for key in FACET_ORDER
    }
    conditions = payload["conditions"]
    profiles = payload["profiles"]

    profile_buttons = "".join(
        '<button type="button" class="rules-profile" '
        'data-profile="{slug}" aria-pressed="false">'
        '<strong>{name}</strong> '
        '<span data-profile-count>{count}</span> '
        '<span class="rules-profile-state" '
        'data-profile-state>inactive</span></button>'.format(
            slug=attr(profile["slug"]),
            name=text(profile["name"]),
            count=len(effective_ids(rules, profile["conditions"])),
        )
        for profile in profiles
    )

    touch_controls = "".join(
        '<label><input type="checkbox" data-condition="{slug}"> '
        '{question}</label>'.format(
            slug=attr(slug),
            question=text(next(
                row["question"]
                for row in conditions
                if row["slug"] == slug
            )),
        )
        for slug in CONTEXT_FLAGS
    )

    fine_controls = "".join(
        '<label><input type="checkbox" data-condition="{slug}"'
        '{checked}{disabled}> {question}</label>'.format(
            slug=attr(row["slug"]),
            checked=" checked" if row["slug"] == "always" else "",
            disabled=" disabled" if row["slug"] == "always" else "",
            question=text(row["question"]),
        )
        for row in conditions
    )

    facet_view = "".join(
        group(key, FACET_LABEL[key], by_group[key], primary=True)
        for key in FACET_ORDER
        if by_group[key]
    )
    set_view = "".join(
        group(
            row["slug"],
            row["question"],
            [
                rule for rule in rules
                if row["slug"] in rule["applies"]
            ],
        )
        for row in conditions
    )
    profile_view = "".join(
        group(
            profile["slug"],
            profile["name"],
            [
                rule for rule in rules
                if set(rule["applies"]).intersection(
                    profile["conditions"]
                )
            ],
        )
        for profile in profiles
    )

    # No blank lines inside this block: gen_site's bounded renderer treats
    # a non-type-1 raw HTML block as ending at a blank line.
    lines = [
        '<div class="rules-app" data-rules-app '
        'data-model-sha256="{}">'.format(
            attr(payload["model-sha256"])
        ),
        '<div class="wrap pagehead"><p class="eyebrow">'
        'The corpus</p><h1>Build your rule set.</h1>',
        '<p class="lead">AIQT is one governed corpus of '
        '<strong data-corpus-count>{}</strong> rules. '
        'Describe your assistant and watch the effective union '
        'take shape.</p></div>'.format(len(rules)),
        '<section class="wrap rules-builder" data-builder hidden>'
        '<h2>Describe your assistant</h2>',
        '<p class="rules-url-notice" data-url-notice hidden></p>',
        '<label class="rules-floor"><input type="checkbox" '
        'data-condition="always" checked disabled> '
        '<strong>Always on</strong>: the baseline floor cannot '
        'be removed.</label>',
        '<fieldset><legend>Use a profile preset</legend>'
        '<div class="rules-profile-grid">{}</div></fieldset>'.format(
            profile_buttons
        ),
        '<fieldset><legend>What else does it touch?</legend>'
        '<div class="rules-check-grid">{}</div></fieldset>'.format(
            touch_controls
        ),
        '<details class="more"><summary>Fine control</summary>'
        '<div class="inner rules-check-grid">{}</div></details>'.format(
            fine_controls
        ),
        '<p class="rules-summary" data-summary '
        'aria-live="polite"></p>',
        '<p><label><input type="checkbox" data-only-selected> '
        'Show only my effective set</label></p>',
        '<section class="rules-export">'
        '<h2>Your rule set, to go.</h2>',
        '<div class="rules-actions">'
        '<button type="button" data-copy-link>Copy permalink</button>'
        '<button type="button" data-download-ids>'
        'Download rule IDs</button></div>',
        '<pre data-export-text></pre>'
        '<a class="btn primary" href="/install">'
        'Add AIQT to your assistant</a></section>',
        '</section>',
        '<div class="wrap rules-view-switcher" '
        'data-view-switcher hidden role="group" '
        'aria-label="Catalog view">'
        '<label><input type="radio" name="rules-view" '
        'value="facet" checked> By facet</label>'
        '<label><input type="radio" name="rules-view" '
        'value="set"> By set</label>'
        '<label><input type="radio" name="rules-view" '
        'value="profile"> By profile</label></div>',
        '<section class="wrap rules-view" data-view="facet">'
        '<h2>By facet</h2>{}</section>'.format(facet_view),
        '<section class="wrap rules-view" data-view="set">'
        '<h2>By applicability set</h2>{}</section>'.format(set_view),
        '<section class="wrap rules-view" data-view="profile">'
        '<h2>By adoption profile</h2>{}</section>'.format(
            profile_view
        ),
        '</div>',
    ]
    return "\n".join(lines)


def run(root, check):
    root = Path(root).resolve()
    try:
        payload, rules = build(root)
        json_text = json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        source_path = root / DOC_REL
        source_text = source_path.read_text(encoding="utf-8")
        markdown_text = replace_block(
            source_text, "RULESET", render_block(payload, rules)
        )
        drift = [
            reconcile(root / RULESET_REL, json_text, check),
            reconcile(source_path, markdown_text, check),
        ]
    except (
        OSError, UnicodeError, ValueError, KeyError,
        json.JSONDecodeError,
    ) as exc:
        print(
            "error: cannot generate rules page ({}); "
            "fail-closed".format(exc),
            file=sys.stderr,
        )
        return 2

    if check and any(drift):
        print(
            "drift: ruleset.json or docs/rules.md is out of date; "
            "run tools/gen_ruleset.py",
            file=sys.stderr,
        )
        return 1
    return 0


def _fixture(root):
    (root / ".aiqt/core/rules").mkdir(parents=True)
    (root / "site/downloads").mkdir(parents=True)
    (root / "docs").mkdir()

    conditions = (
        "always", "writes-code", "tools-retrieval",
        "personal-data", "agent-harness", "multi-agent",
    )
    applicability = ["version = 1", ""]
    for slug in conditions:
        applicability += [
            "[[condition]]",
            'slug = "{}"'.format(slug),
            'question = "Question {}?"'.format(slug),
            'description = "Description {}."'.format(slug),
            "",
        ]
    applicability += [
        "[[profile]]",
        'name = "Full corpus"',
        'slug = "full-corpus"',
        "conditions = [{}]".format(", ".join(
            '"{}"'.format(value) for value in conditions
        )),
        "",
    ]
    (root / check_applies.APPLICABILITY_REL).write_text(
        "\n".join(applicability), encoding="utf-8"
    )
    (root / check_applies.APPLIES_REL).write_text(
        'version = 1\n[assignments]\n'
        'selfa1 = ["always"]\n'
        'selfb1 = ["writes-code"]\n',
        encoding="utf-8",
    )

    template = """---
corpus-id: {cid}
origin: pack
family: aiqt
tier: 10
facet: {facet}
slug: {slug}
---
# {title}

Fixture rule text.
"""
    (root / ".aiqt/core/rules/a.md").write_text(
        template.format(
            cid="selfa1", facet="ACCUR",
            slug="fixture-a", title="Fixture A",
        ),
        encoding="utf-8",
    )
    (root / ".aiqt/core/rules/b.md").write_text(
        template.format(
            cid="selfb1", facet="QUALI",
            slug="fixture-b", title="Fixture B",
        ),
        encoding="utf-8",
    )

    ledger = {
        "version": 1,
        "boundary": "Fixture boundary.",
        "rules": [
            {"corpus-id": "selfa1", "status": "prose-only"},
            {"corpus-id": "selfb1", "status": "gate-linked"},
        ],
    }
    (root / ENFORCEMENT_REL).write_text(
        json.dumps(ledger), encoding="utf-8"
    )
    (root / DOC_REL).write_text(
        '+++\ntitle = "t"\ndescription = "d"\n'
        'canonical = "https://aiqt.ai/rules"\n'
        'og-title = "t"\nog-description = "d"\n'
        'og-url = "https://aiqt.ai/rules"\n'
        'sidebar-active = "rules"\n+++\n\n'
        '<!-- RULESET:BEGIN (generated) -->\n'
        '<!-- RULESET:END -->\n',
        encoding="utf-8",
    )


def self_test_main():
    failures = []
    try:
        tmp = Path(tempfile.mkdtemp(
            prefix="aiqt-ruleset-selftest-"
        ))
    except OSError as exc:
        print(
            "SELF-TEST ERROR: no writable temporary directory: "
            "{}".format(exc),
            file=sys.stderr,
        )
        return 2

    def quiet(root, check):
        with redirect_stdout(io.StringIO()), redirect_stderr(
            io.StringIO()
        ):
            try:
                return run(root, check)
            except BaseException as exc:  # noqa: BLE001
                return "raised {}".format(type(exc).__name__)

    try:
        _fixture(tmp)
        first = build(tmp)
        second = build(tmp)
        if first != second or render_block(*first) != render_block(*second):
            failures.append("deterministic build/render")

        overlap_rules = (
            [
                {
                    "corpus-id": "o{:02d}".format(index),
                    "applies": ["a", "b"],
                }
                for index in range(5)
            ]
            + [
                {
                    "corpus-id": "a{:02d}".format(index),
                    "applies": ["a"],
                }
                for index in range(10)
            ]
            + [
                {
                    "corpus-id": "b{:02d}".format(index),
                    "applies": ["b"],
                }
                for index in range(5)
            ]
        )
        measured = (
            len(effective_ids(overlap_rules, {"a"})),
            len(effective_ids(overlap_rules, {"b"})),
            len(effective_ids(overlap_rules, {"a", "b"})),
        )
        if measured != (15, 10, 20):
            failures.append(
                "15 plus 10 with five shared must yield 20 unique"
            )

        if quiet(tmp, False) != 0 or quiet(tmp, True) != 0:
            failures.append("conformant fixture generate/check")

        json_path = tmp / RULESET_REL
        json_good = json_path.read_text(encoding="utf-8")
        json_path.write_text(json_good + " ", encoding="utf-8")
        if quiet(tmp, True) != 1:
            failures.append("ruleset.json drift expected exit 1")
        json_path.write_text(json_good, encoding="utf-8")

        docs_path = tmp / DOC_REL
        docs_good = docs_path.read_text(encoding="utf-8")
        # Corrupt INSIDE the generated block: a change outside the markers is
        # preserved by replace_block and would not register as drift.
        docs_path.write_text(
            docs_good.replace(
                "<!-- RULESET:BEGIN (generated) -->",
                "<!-- RULESET:BEGIN (generated) -->\n<!-- drift -->",
                1,
            ),
            encoding="utf-8",
        )
        if quiet(tmp, True) != 1:
            failures.append("docs/rules.md drift expected exit 1")
        docs_path.write_text(docs_good, encoding="utf-8")

        applies_path = tmp / check_applies.APPLIES_REL
        applies_good = applies_path.read_text(encoding="utf-8")
        applies_path.write_text(
            'version = 1\n[assignments]\n'
            'selfa1 = ["always"]\n',
            encoding="utf-8",
        )
        try:
            build(tmp)
            failures.append("untagged rule was accepted")
        except ValueError:
            pass

        applies_path.write_text(
            applies_good + 'orphan1 = ["always"]\n',
            encoding="utf-8",
        )
        try:
            build(tmp)
            failures.append("orphan assignment was accepted")
        except ValueError:
            pass
        applies_path.write_text(applies_good, encoding="utf-8")

        ledger_path = tmp / ENFORCEMENT_REL
        ledger_good = ledger_path.read_text(encoding="utf-8")
        ledger = json.loads(ledger_good)
        ledger["rules"].pop()
        ledger_path.write_text(
            json.dumps(ledger), encoding="utf-8"
        )
        try:
            build(tmp)
            failures.append("missing enforcement row was accepted")
        except ValueError:
            pass

        ledger_path.write_text("{", encoding="utf-8")
        if quiet(tmp, True) != 2:
            failures.append(
                "malformed enforcement JSON expected exit 2"
            )
        ledger_path.write_text(ledger_good, encoding="utf-8")

        duplicate = tmp / ".aiqt/core/rules/duplicate.md"
        duplicate.write_text(
            (tmp / ".aiqt/core/rules/a.md")
            .read_text(encoding="utf-8")
            .replace("slug: fixture-a", "slug: duplicate-a"),
            encoding="utf-8",
        )
        try:
            build(tmp)
            failures.append("duplicate corpus-id was accepted")
        except ValueError:
            pass
        duplicate.unlink()

        empty = tmp / "empty"
        _fixture(empty)
        for path in (empty / ".aiqt/core/rules").iterdir():
            path.unlink()
        if quiet(empty, True) != 2:
            failures.append("empty corpus expected exit 2")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1

    print(
        "SELF-TEST PASS: deterministic generation, overlap "
        "deduplication, independent target drift, malformed JSON, "
        "empty/duplicate corpus, applicability coverage, and "
        "enforcement coverage."
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test_main()
    return run((args.root or repo_root()).resolve(), args.check)


if __name__ == "__main__":
    raise SystemExit(main())
