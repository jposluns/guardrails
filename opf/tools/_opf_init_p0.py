#!/usr/bin/env python3
"""OPF init P0: ratified contracts, without observation or effects.

VALID here means that supplied data satisfies this contract, never that a target is
bound, an inventory is complete, or publication is authorized. The v1 plan remains
unstaged. F19 is a separate hypothetical-membership predicate, not a v1 activation.
It covers literal source membership only; index modes, conflict stages, intent-to-add,
expansion and installation require the later staging implementation.

Compatibility checks cover supplied manifest/provenance bytes and snapshot deltas,
not physical-store health or snapshot completeness. No CLI exit is promoted to an
InitResult. Existing pure validators retain the detailed payload/schema authority.
"""
import base64
import binascii
import copy
import functools
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_init_contract as contract  # noqa: E402
import _opf_init_operation as operation  # noqa: E402
import _opf_init_substrate as substrate  # noqa: E402
import _opf_store as store  # noqa: E402

PLAN_FORMATS = frozenset(("opf.init.plan/v1",))
OUTCOME_FORMATS = frozenset(("opf.init.outcome/v1",))
RESULT_STATUSES = frozenset((
    "VIEWS-READY", "ALREADY-INITIALIZED", "REFUSED", "FAILED", "CANNOT-EVALUATE"))
MANIFEST_PATH = operation._MANIFEST_RELPATH


class ContractValidation:
    """A validation verdict, distinct from both InitResult and a CLI exit."""
    __slots__ = ("status", "findings", "model")

    def __init__(self, status, findings=(), model=None):
        self.status, self.findings, self.model = status, findings, model


class _Refusal(Exception):
    def __init__(self, code, location, detail, status="REFUSED"):
        self.status = status
        self.finding = (code, location, detail)


def _require(condition, code, location, detail):
    if not condition:
        raise _Refusal(code, location, detail)


def _validation(fn):
    @functools.wraps(fn)
    def validate(*args, **kwargs):
        try:
            return ContractValidation("VALID", model=fn(*args, **kwargs))
        except _Refusal as exc:
            return ContractValidation(exc.status, (exc.finding,))
        except (substrate.InitSubstrateError, operation.InitOperationError,
                ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            return ContractValidation("REFUSED", (("MALFORMED", fn.__name__, str(exc)),))
    return validate


def _record(raw, location):
    if raw is None:
        raise _Refusal("INPUT", location, "required bytes unavailable", "CANNOT-EVALUATE")
    _require(type(raw) is bytes and 0 < len(raw) <= contract.MAX_RAW_BYTES,
             "INPUT", location, "expected bounded, non-empty bytes")
    doc = substrate._strict_json_loads(raw, location)
    _require(substrate._canonical_or_refuse(doc, location) == raw,
             "SCHEMA", location, "exact canonical JSON required")
    return doc


def _version(doc, formats, location):
    _require(type(doc.get("format")) is str and doc["format"] in formats
             and type(doc.get("schema")) is int and doc["schema"] == 1,
             "VERSION", location, "unsupported format or schema")


def _versions(plan, completed):
    _require(type(completed) is bool, "INPUT", "completed", "expected a boolean")
    expected = operation._pinned_versions()
    if completed:
        versions = plan["versions"]
        recorded = versions.get("views_generator") if type(versions) is dict else None
        _require(type(recorded) is dict and set(recorded) == set(expected["views_generator"])
                 and all(type(recorded[k]) is str and recorded[k]
                         for k in ("name", "version", "transform_vocab"))
                 and type(recorded["projection_schema"]) is int
                 and recorded["projection_schema"] >= 1,
                 "VERSION", "versions.views_generator", "malformed recorded generator")
        expected["views_generator"] = recorded
    _require(plan["versions"] == expected, "VERSION", "versions",
             "not this generator's pinned versions")


def _membership(plan):
    sets = plan["sets"]
    _require(type(sets) is dict and set(sets) == operation.PLAN_SET_KEYS,
             "SETS", "sets", "expected exactly S, V, K, E, C")
    for key, entries in sets.items():
        _require(type(entries) is list, "SETS", key, "expected a list")
        for entry in entries:
            _require(type(entry) is dict and type(entry.get("path")) is str
                     and contract._bad_relpath(entry["path"]) is None,
                     "SETS", key, "entry requires a canonical relative path")
    paths = [e["path"] for key in ("S", "V", "E", "C") for e in sets[key]]
    _require(len(paths) == len(set(paths)), "OVERLAP", "sets", "S/V/E/C overlap")
    _require(sets["K"] == [], "SETS", "K", "Keep is not active")
    _require(sets["C"] == [{"path": operation.LEASE_RELPATH, "kind": "lease"}],
             "UNCLASSIFIED", "C", "expected exactly the lease")
    _require(len(sets["E"]) <= 1 and all(e["path"] == operation.CHANGELOG_RELPATH
                                      for e in sets["E"]),
             "UNCLASSIFIED", "E", "only a preserved CHANGELOG.md is permitted")
    roster = set(operation.BOOTSTRAP_SOURCE_ROSTER) | {operation.PROVENANCE_RELPATH}
    if not sets["E"]:
        roster.add(operation.CHANGELOG_RELPATH)
    sources = [e["path"] for e in sets["S"]]
    _require(set(sources) == roster and len(sources) == len(roster),
             "UNCLASSIFIED", "S", "not the fixed bootstrap source roster")
    _require(sources == operation._effect_order(roster),
             "SETS", "S", "not in canonical creation order")
    manifest = next(e for e in sets["S"] if e["path"] == MANIFEST_PATH)
    try:
        payload = base64.b64decode(manifest["payload"].encode("ascii"), validate=True)
        views = operation.build_view_roster(plan["operation_id"], payload, roster)
    except (operation.InitOperationError, binascii.Error, UnicodeError,
            AttributeError, KeyError, ValueError) as exc:
        raise _Refusal("SETS", "V", "cannot derive the manifest view roster: {}".format(exc))
    _require(sets["V"] == views, "UNCLASSIFIED", "V", "not the manifest-declared view roster")


def _unstaged(plan):
    _require(plan["staging_set"] == [], "STAGING", "staging_set",
             "v1 is unstaged; a staged contract requires a new version")
    _require(plan["publication_boundaries"] == operation.PUBLICATION_BOUNDARIES,
             "STAGING", "publication_boundaries", "not the VIEWS-READY boundary")


def _plan(raw, completed=False):
    doc = _record(raw, "plan")
    _version(doc, PLAN_FORMATS, "plan")
    substrate._validate_plan(raw, doc.get("operation_id"))
    _versions(doc, completed)
    _membership(doc)
    _unstaged(doc)
    # Pure validation only: do not supply live bindings or call run_init_operation.
    return operation.validate_init_plan(raw, completed=completed)


@_validation
def validate_plan(raw, *, completed=False):
    return _plan(raw, completed)


@_validation
def validate_outcome(raw, *, operation_id):
    doc = _record(raw, "outcome")
    _version(doc, OUTCOME_FORMATS, "outcome")
    _require(type(operation_id) is str and substrate._OP_ID_RE.fullmatch(operation_id),
             "SCHEMA", "operation_id", "expected a well-formed operation id")
    # P0 validates the persisted envelope. Producer fields are not a new result schema.
    return substrate._validate_outcome(raw, operation_id)


@_validation
def validate_result(value, *, contract_kind):
    """Retain D2a exit semantics; no implicit conversion in either direction."""
    if contract_kind == "source-only":
        _require(type(value) is int and value in (0, 2),
                 "RESULT", "source-only", "expected the D2a exit contract")
    elif contract_kind == "coupled":
        _require(type(value) is operation.InitResult and type(getattr(value, "status", None)) is str
                 and value.status in RESULT_STATUSES,
                 "RESULT", "coupled", "expected InitResult with a ratified status")
    else:
        raise _Refusal("RESULT", "contract_kind", "unknown result contract")
    return value


@_validation
def validate_staging_membership(raw_plan, staged_paths):
    """F19 membership for a hypothetical future staged set; never alters the v1 plan."""
    plan = _plan(raw_plan)
    _require(type(staged_paths) is list
             and all(type(p) is str and contract._bad_relpath(p) is None for p in staged_paths)
             and len(staged_paths) == len(set(staged_paths)),
             "STAGING", "staged_paths", "expected unique literal file paths")
    sources = {e["path"] for e in plan["sets"]["S"]}
    _require(set(staged_paths) <= sources, "STAGING", "staged_paths",
             "F19 permits exact S paths only; V/K/E/C and unclassified paths refuse")
    return tuple(staged_paths)


def _manifest(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= store.MAX_STORE_READ_BYTES,
             "INPUT", "manifest", "expected bounded, non-empty bytes")
    return tomllib.loads(raw.decode("utf-8", "strict"))


def _compatibility(manifest, provenance):
    _require(type(manifest.get("opf")) is dict
             and manifest["opf"].get("spec_version") == "1.2.0",
             "VERSION", "manifest", "requires spec_version 1.2.0; older stores need opf upgrade")
    _require(store.SUPPORTED_HOMES == 1 and store.homes_generation(manifest) == 1,
             "HOMES", "manifest", "this bootstrap keeps homes generation 1")
    checked = store.validate_manifest(manifest)
    _require(checked.status == store.VALID and not checked.findings,
             "COMPATIBILITY", "manifest", str(checked.findings))
    if provenance is not None:
        checked = operation.validate_bootstrap_provenance(provenance)
        _require(checked.status == operation.VALID, "COMPATIBILITY", "init.toml",
                 str(checked.findings))
    return manifest


@_validation
def validate_compatibility(raw_manifest, *, provenance=None):
    """Absent provenance is valid. None explicitly denotes absence, not a failed read."""
    return _compatibility(_manifest(raw_manifest), provenance)


def _snapshot(value, location):
    _require(type(value) is dict and MANIFEST_PATH in value,
             "INPUT", location, "expected a file snapshot containing the manifest")
    _require(all(type(p) is str and contract._bad_relpath(p) is None
                 and type(raw) is bytes for p, raw in value.items()),
             "INPUT", location, "expected canonical relative paths mapped to bytes")


@_validation
def validate_upgrade_delta(before, after):
    """Check a supplied 1.1.0 -> 1.2.0 snapshot delta; neither upgrade nor inventory I/O."""
    _snapshot(before, "before")
    _snapshot(after, "after")
    old, new = _manifest(before[MANIFEST_PATH]), _manifest(after[MANIFEST_PATH])
    _require(type(old.get("opf")) is dict
             and old["opf"].get("spec_version") == "1.1.0",
             "VERSION", "before", "expected the 1.1.0 upgrade origin")
    expected = copy.deepcopy(old)
    expected["opf"]["spec_version"] = "1.2.0"
    _require(new == expected, "UPGRADE", "manifest", "only spec_version may change")
    _require(set(before) == set(after)
             and all(before[p] == after[p] for p in before if p != MANIFEST_PATH),
             "UPGRADE", "files", "no files or counters may change; never invent init.toml")
    _compatibility(new, after.get(operation.PROVENANCE_RELPATH))
    return new
