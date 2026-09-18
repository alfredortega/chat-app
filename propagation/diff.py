"""
Pure-Python unified-diff application for propagation rewrites.

The propagation agents rewrite artifacts as *diffs* rather than whole-file
dumps (drastically cheaper to generate — output tokens scale with the change,
not the artifact size). This module applies a standard unified patch to the
artifact's current content, in memory, so the wave can keep its normal
"regenerate everything, nothing on disk until approved" contract.

The applier is intentionally tolerant of imperfect LLM-produced hunks: it
locates each hunk by searching a bounded window forward when the stated line
numbers are off, and falls back to ``None`` (caller rejects the rewrite) when
the context genuinely does not match. No shelling out to ``patch``.
"""

import re

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
# How far (in lines) we search forward for a hunk's opening context when the
# model's line numbers are slightly off.
FUZZ_LIMIT = 250


def _norm(line: str) -> str:
    """Normalise a line for comparison (strip line endings only)."""
    return line.rstrip("\r\n")


def apply_unified_diff(content: str, diff_text: str) -> str | None:
    """
    Apply a unified diff to ``content`` in memory.

    Returns the patched content, or ``None`` if the diff cannot be applied.
    """
    if not diff_text:
        return None

    old_lines = content.splitlines(keepends=True)
    newline = "\r\n" if (old_lines and old_lines[0].endswith("\r\n")) else "\n"

    # ── 1. Parse hunks from the diff text ─────────────────────────────────────
    hunks: list[tuple[int, list[str]]] = []  # (old_start_idx 0-based, body)
    header = None
    body: list[str] = []
    for raw in diff_text.splitlines():
        m = _HUNK_RE.search(raw)
        if m:
            if header is not None and body:
                hunks.append((header - 1, body))
            header = int(m.group(1))
            body = []
        elif header is not None and not raw.startswith("---") and not raw.startswith("+++"):
            body.append(raw)
    if header is not None and body:
        hunks.append((header - 1, body))
    if not hunks:
        return None

    # ── 2. Apply hunks, re-anchoring when the model's numbers are off ─────────
    out: list[str] = []
    pos = 0  # index into old_lines

    for claimed_start, body in hunks:
        # Find the first body line that must match existing content (a context
        # or deletion line). We use it to anchor the hunk wherever it actually
        # occurs, so slightly-wrong line numbers do not break the whole patch.
        anchor = next((l for l in body if l and l[0] in "- "), None)
        if anchor is None:
            # Pure addition — apply at the claimed (or later, if consumed) slot.
            target = max(claimed_start, pos)
            out.extend(old_lines[pos:target])
            pos = target
            for line in body:
                if line.startswith("+"):
                    out.append(line[1:] + newline)
            continue

        anchor_text = _norm(anchor[1:])
        # A large artifact's first change can be far beyond the first 250
        # lines. Include the model's claimed hunk location in the fuzzy window
        # instead of rejecting an otherwise valid patch before reaching it.
        search_end = min(len(old_lines), max(pos + FUZZ_LIMIT, claimed_start + FUZZ_LIMIT))
        match = None
        for i in range(pos, search_end):
            if _norm(old_lines[i]) == anchor_text:
                match = i
                break
        if match is None:
            return None

        out.extend(old_lines[pos:match])
        pos = match

        for line in body:
            if not line or line == "\\ No newline at end of file":
                continue
            op = line[0]
            text = line[1:]
            if op == "+":
                out.append(text + newline)
            elif op == "-":
                if pos < len(old_lines) and _norm(old_lines[pos]) == _norm(text):
                    pos += 1
                else:
                    return None
            elif op == " ":
                if pos < len(old_lines) and _norm(old_lines[pos]) == _norm(text):
                    out.append(old_lines[pos])
                    pos += 1
                else:
                    return None

    if pos < len(old_lines):
        out.extend(old_lines[pos:])
    return "".join(out)
