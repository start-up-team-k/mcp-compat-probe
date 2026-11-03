"""Probe corpus: each probe is a minimal MCP tool definition exercising ONE keyword or structure.

A probe also carries a detector (`match`) so the checker can tell which probes a real
tool's schema "contains". Detectors are deliberately simple key lookups.
"""
from __future__ import annotations

import json

_MAPS = ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
_ONE = ("items", "additionalProperties", "not", "if", "then", "else",
        "propertyNames", "unevaluatedProperties", "contains")
_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems", "items")


def nodes(s):
    """Yield every sub-schema of `s` (including `s`), descending only through schema positions."""
    if not isinstance(s, dict):
        return
    yield s
    for k in _MAPS:
        if isinstance(s.get(k), dict):
            for v in s[k].values():
                yield from nodes(v)
    for k in _ONE:
        yield from nodes(s.get(k))
    for k in _LISTS:
        if isinstance(s.get(k), list):
            for v in s[k]:
                yield from nodes(v)


def _any(key, pred=lambda v: True):
    return lambda n, s, d: any(key in x and pred(x[key]) for x in nodes(s))


def _top(key, pred=lambda v: True):
    return lambda n, s, d: isinstance(s, dict) and key in s and pred(s[key])


def _fmt(f):
    return lambda n, s, d: any(x.get("format") == f for x in nodes(s))


def _recursive(n, s, d):
    if not isinstance(s, dict):
        return False
    text = json.dumps(s)
    if '"$ref": "#"' in text:
        return True
    for key in ("$defs", "definitions"):
        for name, body in (s.get(key) or {}).items():
            if '"$ref": "#/%s/%s"' % (key, name) in json.dumps(body):
                return True
    return False


def _top_ap(check):
    return lambda n, s, d: isinstance(s, dict) and s.get("type") == "object" and check(s)


def _required_partial(n, s, d):
    return isinstance(s, dict) and bool(s.get("required")) and \
        set(s["required"]) < set((s.get("properties") or {}).keys())


def _non_top(key):
    return lambda n, s, d: any(key in x for x in list(nodes(s))[1:])


def _one(key):
    return lambda n, s, d: any(key in x for x in nodes(s))


def wrap(frag):
    """Property-level probe: one property `x` carrying the fragment under test."""
    return {"type": "object", "properties": {"x": frag}, "required": ["x"],
            "additionalProperties": False}


def base(**extra):
    """Control schema plus top-level extras."""
    s = {"type": "object", "properties": {"q": {"type": "string", "description": "query"}},
         "required": ["q"], "additionalProperties": False}
    s.update(extra)
    return s


S = {"type": "string"}
# Every object below satisfies OpenAI strict rules (type, all properties required, additionalProperties false)
# so a strict rejection can only come from the keyword under test.
_QOBJ = {"type": "object", "properties": {"q": S}, "required": ["q"], "additionalProperties": False}
_ALT = [_QOBJ, _QOBJ]
_DEFS = {"s": {"type": "string"}}
_NODE = {"type": "object", "properties": {"children": {"type": "array", "items": {"$ref": "#/$defs/node"}}},
         "required": ["children"], "additionalProperties": False}

# (id, description, inputSchema, detector, tool-name override, tool-description override)
_RAW = [
    ("control", "Plain strict-compatible object schema, no special keywords", base(), lambda n, s, d: True),
    ("top_anyof", "Top-level anyOf (E-003)", base(anyOf=_ALT), _top("anyOf")),
    ("top_oneof", "Top-level oneOf (E-003)", base(oneOf=_ALT), _top("oneOf")),
    ("top_allof", "Top-level allOf (E-003)", base(allOf=_ALT), _top("allOf")),
    ("schema_draft07", "$schema draft-07", base(**{"$schema": "http://json-schema.org/draft-07/schema#"}),
     _top("$schema", lambda v: "draft-07" in str(v))),
    ("schema_2020_12", "$schema 2020-12", base(**{"$schema": "https://json-schema.org/draft/2020-12/schema"}),
     _top("$schema", lambda v: "2020-12" in str(v))),
    ("ref_defs", "$ref into $defs", wrap({"$ref": "#/$defs/s"}) | {"$defs": _DEFS},
     lambda n, s, d: '"#/$defs/' in json.dumps(s)),
    ("ref_definitions", "$ref into definitions", wrap({"$ref": "#/definitions/s"}) | {"definitions": _DEFS},
     lambda n, s, d: '"#/definitions/' in json.dumps(s)),
    ("ref_recursive", "Recursive $ref", wrap({"$ref": "#/$defs/node"}) | {"$defs": {"node": _NODE}}, _recursive),
    ("additional_props_missing", "Object without additionalProperties",
     {"type": "object", "properties": {"q": S}, "required": ["q"]},
     _top_ap(lambda s: "additionalProperties" not in s)),
    ("additional_props_true", "additionalProperties: true", base(additionalProperties=True),
     _top_ap(lambda s: s.get("additionalProperties") is True)),
    ("additional_props_schema", "additionalProperties: {schema}", base(additionalProperties=S),
     _top_ap(lambda s: isinstance(s.get("additionalProperties"), dict))),
    ("required_partial", "required lists only some properties",
     {"type": "object", "properties": {"a": S, "b": S}, "required": ["a"], "additionalProperties": False},
     _required_partial),
    ("type_array_null", "Property type array [string, null] (E-004)", wrap({"type": ["string", "null"]}),
     lambda n, s, d: any(isinstance(x.get("type"), list) for x in nodes(s))),
    ("nullable_keyword", "OpenAPI-style nullable: true", wrap({"type": "string", "nullable": True}), _one("nullable")),
    ("const", "const", wrap({"type": "string", "const": "a"}), _one("const")),
    ("enum_numbers", "Numeric enum", wrap({"type": "number", "enum": [1, 2, 3]}),
     _any("enum", lambda v: isinstance(v, list) and v and all(isinstance(i, (int, float)) for i in v))),
    ("empty_properties", "Object with empty properties",
     {"type": "object", "properties": {}, "additionalProperties": False},
     lambda n, s, d: any(x.get("type") == "object" and x.get("properties") == {} for x in nodes(s))),
    ("object_no_properties", "type object without a properties key", {"type": "object", "additionalProperties": False},
     _top_ap(lambda s: "properties" not in s)),
    ("format_uri", "format: uri", wrap({"type": "string", "format": "uri"}), _fmt("uri")),
    ("format_datetime", "format: date-time", wrap({"type": "string", "format": "date-time"}), _fmt("date-time")),
    ("format_email", "format: email", wrap({"type": "string", "format": "email"}), _fmt("email")),
    ("pattern", "pattern", wrap({"type": "string", "pattern": "^[a-z]+$"}), _one("pattern")),
    ("min_max_length", "minLength/maxLength", wrap({"type": "string", "minLength": 1, "maxLength": 10}),
     lambda n, s, d: any("minLength" in x or "maxLength" in x for x in nodes(s))),
    ("min_max_number", "minimum/maximum", wrap({"type": "number", "minimum": 0, "maximum": 10}),
     lambda n, s, d: any("minimum" in x or "maximum" in x for x in nodes(s))),
    ("exclusive_minimum", "exclusiveMinimum (numeric, draft 6+)", wrap({"type": "number", "exclusiveMinimum": 0}),
     lambda n, s, d: any("exclusiveMinimum" in x or "exclusiveMaximum" in x for x in nodes(s))),
    ("multiple_of", "multipleOf", wrap({"type": "number", "multipleOf": 2}), _one("multipleOf")),
    ("default", "default", wrap({"type": "string", "default": "a"}), _one("default")),
    ("examples", "examples", wrap({"type": "string", "examples": ["a"]}), _one("examples")),
    ("title", "title on a property", wrap({"type": "string", "title": "X"}), _one("title")),
    ("prop_anyof", "anyOf inside a property", wrap({"anyOf": [S, {"type": "number"}]}), _non_top("anyOf")),
    ("not", "not", wrap({"not": {"type": "null"}}), _one("not")),
    ("if_then_else", "if/then/else", base(**{"if": {"required": ["q"]}, "then": {}, "else": {}}), _one("if")),
    ("pattern_properties", "patternProperties",
     base(patternProperties={"^a": S}), _one("patternProperties")),
    ("property_names", "propertyNames", base(propertyNames={"type": "string", "pattern": "^[a-z]+$"}), _one("propertyNames")),
    ("unevaluated_properties", "unevaluatedProperties: false", base(unevaluatedProperties=False), _one("unevaluatedProperties")),
    ("prefix_items", "prefixItems (2020-12 tuples)", wrap({"type": "array", "prefixItems": [S], "items": S}),
     _one("prefixItems")),
    ("unique_min_items", "uniqueItems/minItems", wrap({"type": "array", "items": S, "uniqueItems": True, "minItems": 1}),
     lambda n, s, d: any("uniqueItems" in x or "minItems" in x or "maxItems" in x for x in nodes(s))),
]

_NAMES = [
    ("name_dot", "Tool name containing a dot", "probe.tool", lambda n, s, d: "." in n),
    ("name_hyphen", "Tool name containing a hyphen", "probe-tool", lambda n, s, d: "-" in n),
    ("name_too_long", "Tool name longer than 64 chars (65)", "t" * 65, lambda n, s, d: len(n) > 64),
    ("name_too_long_129", "Tool name longer than 128 chars (129)", "t" * 129, lambda n, s, d: len(n) > 128),
]


def _mk(id_, desc, schema, match, name="probe_tool", description="Probe tool."):
    return {"id": id_, "description": desc, "match": match,
            "tool": {"name": name, "description": description, "inputSchema": schema}}


PROBES = [_mk(*r) for r in _RAW]
PROBES += [_mk(i, d, base(), m, name=nm) for i, d, nm, m in _NAMES]
PROBES.append(_mk("long_description", "Tool description of 10,000 characters", base(),
                  lambda n, s, d: len(d or "") > 1024, description="Lorem ipsum dolor sit amet. " * 358))


def _nest(levels):
    """Schema whose _depth() is `levels` (root = level 1, string leaf = last level), strict-compatible."""
    node = S
    for i in range(levels - 1, 0, -1):
        node = {"type": "object", "properties": {"k%d" % i: node}, "required": ["k%d" % i],
                "additionalProperties": False}
    return node


# OpenAI documents a limit of 10 nesting levels; 10 should pass and 11 should be refused (measured, not assumed).
for _n in (10, 11):
    PROBES.append(_mk("deep_nesting_%d" % _n, "Nesting %d levels deep" % _n, _nest(_n),
                      lambda n, s, d, _n=_n: _depth(s) >= _n))


def _depth(s, k=0):
    if not isinstance(s, dict):
        return k
    kids = [v for key in _MAPS if isinstance(s.get(key), dict) for v in s[key].values()]
    return max([_depth(v, k + 1) for v in kids] + [k + 1])


BY_ID = {p["id"]: p for p in PROBES}


def match_probes(tool):
    """Probe ids whose feature appears in this MCP tool definition (control excluded)."""
    n, s, d = tool.get("name", ""), tool.get("inputSchema") or {}, tool.get("description", "")
    return [p["id"] for p in PROBES if p["id"] != "control" and p["match"](n, s, d)]
