#!/usr/bin/env python3
"""YAML syntax + duplicate-key check for pre-commit.

Replaces the external `adrienverge/yamllint` pre-commit hook. This repo has
only two YAML files (this config file itself and `taxa_eu.yaml`, a
species-code mapping), and `.yamllint`'s config enabled little beyond
`default` — mostly cosmetic rules (indentation, trailing whitespace) that
git diffs already surface incidentally. The one rule worth keeping is
duplicate-key detection: PyYAML's own loader silently accepts a duplicate
key and keeps only the last value, which for a code-mapping file would be a
silent, wrong mapping — exactly the failure mode `docs/references.md` and
CLAUDE.md's "Species codes" section warn is worse than a missing one.

Uses `pyyaml`, already a project dependency (`pyproject.toml`) — no new
external dependency, and no external repo cloned/executed at commit time.
"""

from __future__ import annotations

import sys

import yaml


class _DuplicateKeyError(ValueError):
    pass


class _DuplicateKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            msg = f"duplicate key {key!r} at line {key_node.start_mark.line + 1}"
            raise _DuplicateKeyError(msg)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def check_file(path: str) -> str | None:
    """Return an error message, or None if `path` is valid YAML with no
    duplicate keys."""
    try:
        with open(path, encoding="utf-8") as f:
            yaml.load(f, Loader=_DuplicateKeyLoader)
    except _DuplicateKeyError as exc:
        return f"{path}: {exc}"
    except yaml.YAMLError as exc:
        return f"{path}: invalid YAML: {exc}"
    return None


def main(paths: list[str]) -> int:
    errors = [msg for path in paths if (msg := check_file(path)) is not None]
    for msg in errors:
        print(msg, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
