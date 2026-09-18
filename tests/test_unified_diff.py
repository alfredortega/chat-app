"""Unit tests for the pure-Python unified-diff applier used by propagations."""

import difflib
import re

from propagation.diff import apply_unified_diff


def _patch(old: str, new: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile="a/x",
        tofile="b/x",
    ))


class TestApplyUnifiedDiff:
    def test_empty_diff_returns_none(self):
        assert apply_unified_diff("a\nb\n", "") is None

    def test_no_hunks_returns_none(self):
        assert apply_unified_diff("a\nb\n", "diff --git a/x b/x\n") is None

    def test_identity_diff(self):
        old = "## REQ-001 — Title\nbody\nnext\n"
        new = "## REQ-001 — Title\nbody\nnext\n"
        # difflib emits NO hunk for identical content -> nothing to apply.
        assert apply_unified_diff(old, _patch(old, new)) is None

    def test_single_line_change(self):
        old = "line1\nline2\nline3\n"
        new = old.replace("line2", "line2 changed")
        applied = apply_unified_diff(old, _patch(old, new))
        assert applied == new

    def test_multiple_hunks(self):
        old = "".join(f"line{i}\n" for i in range(20))
        new_lines = list(old.splitlines())
        new_lines[2] = "CHANGED 2"
        new_lines[15] = "CHANGED 15"
        new = "\n".join(new_lines) + "\n"
        assert apply_unified_diff(old, _patch(old, new)) == new

    def test_add_and_remove_block(self):
        old = "a\nb\nc\nd\ne\n"
        new = "a\nb\nD1\nD2\nd\ne\nF1\n"
        assert apply_unified_diff(old, _patch(old, new)) == new

    def test_additions_only(self):
        old = "a\nb\n"
        new = "a\nb\nc\nd\n"
        applied = apply_unified_diff(old, _patch(old, new))
        assert applied == new

    def test_fuzzy_anchoring_when_line_numbers_are_off(self):
        """A hunk claiming to start several lines late must still apply."""
        old = "alpha\nbeta\ngamma\ndelta\nepsilon\nzeta\neta\n"
        old_patched = old.replace("delta", "DELTA").replace("zeta", "ZETA")
        # Build a real patch, then hand-write it with intentionally wrong hunk
        # start numbers (off by 2). The applier re-anchors on context.
        new = old_patched
        diff = _patch(old, new)
        re_hunk = re.compile(r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@")
        offset = 0
        fixed_lines = []
        for line in diff.splitlines(keepends=True):
            if line.startswith("@@"):
                m = re_hunk.match(line.strip())
                old_s, old_c, new_s, new_c = m.groups()
                old_s = str(int(old_s) + 2)
                new_s = str(int(new_s) + 2)
                header = f"@@ -{old_s},{old_c} +{new_s},{new_c} @@\n"
                fixed_lines.append(header)
            else:
                fixed_lines.append(line)
        distorted = "".join(fixed_lines)
        assert apply_unified_diff(old, distorted) == new

    def test_hunk_beyond_initial_fuzzy_window(self):
        old = "".join(f"line{i}\n" for i in range(400))
        new = old.replace("line320\n", "line320 changed\n")

        # The hunk is correctly located well after the initial 250-line
        # window. The applier must honor its claimed position.
        assert apply_unified_diff(old, _patch(old, new)) == new

    def test_missing_context_returns_none(self):
        result = apply_unified_diff("aaa\nbbb\nccc\n", "@@ -5,1 +5,1 @@\n-zzz\n+yyy\n")
        assert result is None
