#!/usr/bin/env python3
"""
Validate conferences.yaml.

Checks structure, required keys, date formats, and deadline types so that a
typo cannot silently break the daily post. Exits non-zero on any error.

Run locally before pushing:  python validate_conferences.py
"""

import sys
from datetime import date
from pathlib import Path

import yaml

from bot import CONFERENCES_FILE, TYPE_EMOJI

REQUIRED_CONF_KEYS = {"name", "short", "deadlines"}
OPTIONAL_CONF_KEYS = {"url", "tags", "bsky"}
REQUIRED_DL_KEYS = {"type", "label", "date"}
OPTIONAL_DL_KEYS = {"round", "stage"}
VALID_TYPES = set(TYPE_EMOJI)


def validate(path: Path) -> list[str]:
    errors: list[str] = []

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"YAML is not parseable: {e}"]
    except FileNotFoundError:
        return [f"{path} not found"]

    if not isinstance(data, dict) or "conferences" not in data:
        return ["Top level must be a mapping with a 'conferences' key"]

    conferences = data["conferences"]
    if not isinstance(conferences, list) or not conferences:
        return ["'conferences' must be a non-empty list"]

    seen_shorts: dict[str, int] = {}

    for i, conf in enumerate(conferences):
        where = f"conference #{i + 1}"
        if not isinstance(conf, dict):
            errors.append(f"{where}: must be a mapping")
            continue

        name = conf.get("short") or conf.get("name") or where
        where = f"'{name}'"

        missing = REQUIRED_CONF_KEYS - conf.keys()
        if missing:
            errors.append(f"{where}: missing required key(s): {', '.join(sorted(missing))}")

        unknown = conf.keys() - REQUIRED_CONF_KEYS - OPTIONAL_CONF_KEYS
        if unknown:
            errors.append(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")

        # A duplicated short name makes posts ambiguous.
        if short := conf.get("short"):
            if short in seen_shorts:
                errors.append(
                    f"{where}: duplicate 'short' name (also used by conference "
                    f"#{seen_shorts[short]})"
                )
            else:
                seen_shorts[short] = i + 1

        tags = conf.get("tags")
        if tags is not None:
            if not isinstance(tags, list):
                errors.append(f"{where}: 'tags' must be a list")
            else:
                for tag in tags:
                    if not isinstance(tag, str) or not tag.startswith("#"):
                        errors.append(f"{where}: tag {tag!r} should be a string starting with '#'")

        if (bsky := conf.get("bsky")) is not None:
            if not isinstance(bsky, str) or bsky.startswith("@") or "." not in bsky:
                errors.append(
                    f"{where}: 'bsky' should be a bare handle like "
                    f"'name.bsky.social' (got {bsky!r})"
                )

        deadlines = conf.get("deadlines")
        if not isinstance(deadlines, list) or not deadlines:
            errors.append(f"{where}: 'deadlines' must be a non-empty list")
            continue

        seen_deadlines: set[tuple] = set()

        for j, dl in enumerate(deadlines):
            dl_where = f"{where} deadline #{j + 1}"
            if not isinstance(dl, dict):
                errors.append(f"{dl_where}: must be a mapping")
                continue

            missing = REQUIRED_DL_KEYS - dl.keys()
            if missing:
                errors.append(f"{dl_where}: missing required key(s): {', '.join(sorted(missing))}")

            unknown = dl.keys() - REQUIRED_DL_KEYS - OPTIONAL_DL_KEYS
            if unknown:
                errors.append(f"{dl_where}: unknown key(s): {', '.join(sorted(unknown))}")

            dl_type = dl.get("type")
            if dl_type is not None and dl_type not in VALID_TYPES:
                errors.append(
                    f"{dl_where}: invalid type {dl_type!r} "
                    f"(expected one of: {', '.join(sorted(VALID_TYPES))})"
                )

            # Dates must be quoted ISO strings; an unquoted YAML date parses to a
            # date object, which works but drifts from the file's convention.
            raw_date = dl.get("date")
            parsed: date | None = None
            if isinstance(raw_date, date):
                parsed = raw_date
                errors.append(
                    f"{dl_where}: date {raw_date} should be quoted, e.g. \"{raw_date}\""
                )
            elif isinstance(raw_date, str):
                try:
                    parsed = date.fromisoformat(raw_date)
                except ValueError:
                    errors.append(
                        f"{dl_where}: date {raw_date!r} is not valid ISO format (YYYY-MM-DD)"
                    )
            elif raw_date is not None:
                errors.append(f"{dl_where}: date must be a YYYY-MM-DD string (got {raw_date!r})")

            if (rnd := dl.get("round")) is not None and not isinstance(rnd, int):
                errors.append(f"{dl_where}: 'round' must be an integer (got {rnd!r})")

            if (stage := dl.get("stage")) is not None and not isinstance(stage, str):
                errors.append(f"{dl_where}: 'stage' must be a string (got {stage!r})")

            # Two deadlines of the same type/round/stage would post twice.
            if parsed and dl_type:
                key = (dl_type, dl.get("round"), dl.get("stage"))
                if key in seen_deadlines:
                    qual = "".join(
                        f" {k}={v}" for k, v in
                        (("round", dl.get("round")), ("stage", dl.get("stage"))) if v
                    )
                    errors.append(
                        f"{where}: duplicate '{dl_type}'{qual} deadline — add a "
                        f"distinguishing 'round' or 'stage'"
                    )
                else:
                    seen_deadlines.add(key)

    return errors


def main() -> int:
    errors = validate(CONFERENCES_FILE)

    if errors:
        print(f"✗ {CONFERENCES_FILE.name} has {len(errors)} problem(s):\n")
        for err in errors:
            print(f"  - {err}")
        return 1

    # Summarise what passed, so the CI log is informative rather than silent.
    with open(CONFERENCES_FILE) as f:
        data = yaml.safe_load(f)
    conferences = data["conferences"]
    n_deadlines = sum(len(c["deadlines"]) for c in conferences)
    upcoming = sum(
        1
        for c in conferences
        for d in c["deadlines"]
        if (d["date"] if isinstance(d["date"], date) else date.fromisoformat(d["date"]))
        >= date.today()
    )
    print(
        f"✓ {CONFERENCES_FILE.name} is valid — "
        f"{len(conferences)} conferences, {n_deadlines} deadlines "
        f"({upcoming} upcoming)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
