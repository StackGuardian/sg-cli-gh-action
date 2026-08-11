"""
The README's inputs table must match `action.yml`.

Written because it did not. `tirith-version` was documented as defaulting to `1.2.0` — a version pin —
while `action.yml` defaults it to a *branch*, which the same file's description explicitly warns is a
moving ref that "can turn a green pipeline red with nothing in the repository changing". A reader
following the README believed they were pinned and were not. `github-token` was missing from the table
altogether while being referenced in prose two sections later.

Both are mechanical to check, and neither was caught by review, twice. So they are checked here rather
than trusted: a hand-maintained table beside a machine-readable declaration only stays right while
someone remembers it exists.

Deliberately not asserted: the description text. Prose has to be free to differ from a one-line YAML
description, and pinning it would make the test a nuisance that gets deleted.
"""

import os
import re

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTION = os.path.join(ROOT, "action.yml")
README = os.path.join(ROOT, "README.md")

# `| `name` | `default` | description |`, and the slash forms the table uses to group related
# inputs on one row (`comment` / `check`, `sg-api-url` / `sg-dashboard-url`).
ROW = re.compile(r"^\|\s*(`[^|]+`)\s*\|([^|]*)\|", re.M)


def _documented():
    """Map input name -> the default cell, expanding the grouped rows."""
    table = README_TEXT.split("## Inputs", 1)[1].split("## Outputs", 1)[0]
    out = {}
    for names, default in ROW.findall(table):
        if names.strip("`") in ("Input",):
            continue
        for name in re.findall(r"`([^`]+)`", names):
            out[name] = default.strip().strip("`")
    return out


with open(README) as f:
    README_TEXT = f.read()

with open(ACTION) as f:
    DECLARED = yaml.safe_load(f)["inputs"]


def test_every_declared_input_is_documented():
    """An undocumented input is an escape hatch nobody can find -- `github-token` was one."""
    missing = sorted(set(DECLARED) - set(_documented()))
    assert not missing, f"declared in action.yml but absent from the README table: {missing}"


def test_the_readme_documents_no_input_that_does_not_exist():
    """The other direction: a removed input left in the table is a promise the action cannot keep."""
    extra = sorted(set(_documented()) - set(DECLARED))
    assert not extra, f"documented but not declared in action.yml: {extra}"


def test_every_documented_default_is_the_real_one():
    """
    The `tirith-version` defect: `1.2.0` documented, a branch shipped.

    Compared loosely -- the table writes `$SG_API_TOKEN` for a default of `${{ env.SG_API_TOKEN }}`,
    and prose like "derived" or "platform default" stands in where the real default is empty or
    computed. What must not happen is the README naming a *different concrete value*.
    """
    documented = _documented()
    for name, spec in DECLARED.items():
        real = str(spec.get("default", "")).strip()
        shown = documented[name]
        if not real or not shown:
            continue
        stripped = real.strip("${{ }}").replace("env.", "").replace("github.", "").strip()
        if stripped in shown or shown in real:
            continue
        # Prose stand-ins are fine; a competing literal is not.
        assert not re.fullmatch(r"[\w.\-/:]+", shown), (
            f"README documents `{name}` as defaulting to `{shown}`, "
            f"but action.yml defaults it to `{real}`"
        )


def test_the_tirith_version_default_is_not_described_as_a_pin():
    """
    Specific guard on the one that actually shipped wrong, because the generic check above would
    accept "1.2.0" again the moment the default becomes a tag *different* from what the README says.

    While the default is a branch, the README has to say so: a user who thinks they are pinned and is
    not has no reason to investigate when a passing pipeline starts failing.
    """
    real = str(DECLARED["tirith-version"].get("default", ""))
    if re.fullmatch(r"\d+\.\d+\.\d+", real):
        return  # a real version pin; nothing to warn about
    row = next(line for line in README_TEXT.splitlines() if line.startswith("| `tirith-version`"))
    assert real in row, f"the default is `{real}`; the README row does not mention it: {row}"
    assert "not a pin" in row, "a branch default must be flagged as not a pin"
