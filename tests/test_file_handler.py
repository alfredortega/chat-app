"""
Tests for linked-folder content memoisation (file_handler).

The per-turn hot path is ``build_linked_folder_context``: it is called on
every chat message and regenerate, so extracting every file from disk each
time is wasteful. It must be short-circuited by an mtime/size fingerprint and
invalidated whenever a file's content, or the file set itself, changes.
"""

import os
import time

import pytest

import file_handler as fh


@pytest.fixture(autouse=True)
def _clear_caches():
    fh._linked_folder_cache.clear()
    fh._linked_context_cache.clear()
    fh._upload_content_cache.clear()
    yield
    fh._linked_folder_cache.clear()
    fh._linked_context_cache.clear()
    fh._upload_content_cache.clear()


def _workspace(tmp_path):
    ws = tmp_path / "project"
    (ws / "Requirements").mkdir(parents=True)
    (ws / "Design").mkdir()
    (ws / "Requirements" / "BA-REQ.md").write_text("# Req\n\nSome requirement text.\n", encoding="utf-8")
    (ws / "Design" / "UX-WIRE.md").write_text("# UX\n\nWireframe notes here.\n", encoding="utf-8")
    return str(ws)


def _bump_mtime(path: str):
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


class TestLinkedFolderFingerprint:
    def test_unchanged_files_give_same_fingerprint(self, tmp_path):
        ws = _workspace(tmp_path)
        entries = fh.scan_linked_folder(ws)
        assert fh._linked_folder_fingerprint(entries) == fh._linked_folder_fingerprint(entries)

    def test_content_edit_changes_fingerprint(self, tmp_path):
        ws = _workspace(tmp_path)
        entries = fh.scan_linked_folder(ws)
        before = fh._linked_folder_fingerprint(entries)

        req = os.path.join(ws, "Requirements", "BA-REQ.md")
        with open(req, "a", encoding="utf-8") as f:
            f.write("Changed requirement.\n")
        _bump_mtime(req)

        after = fh._linked_folder_fingerprint(fh.scan_linked_folder(ws))
        assert after != before

    def test_added_file_changes_fingerprint(self, tmp_path):
        ws = _workspace(tmp_path)
        before = fh._linked_folder_fingerprint(fh.scan_linked_folder(ws))

        new_file = os.path.join(ws, "Design", "NEW.md")
        with open(new_file, "w", encoding="utf-8") as f:
            f.write("# New\n")
        _bump_mtime(new_file)

        # The scan list itself is cached for CACHE_TTL; simulate the refresh
        # that happens once the TTL lapses.
        fh._linked_folder_cache.clear()
        after = fh._linked_folder_fingerprint(fh.scan_linked_folder(ws))
        assert after != before


class TestBuildLinkedFolderContextCaching:
    def test_second_call_is_served_from_cache(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path)
        linked = [{"folder_path": ws}]
        reads = {"n": 0}

        original = fh.extract_text
        def counting_extract(path, name):
            reads["n"] += 1
            return original(path, name)
        monkeypatch.setattr(fh, "extract_text", counting_extract)

        first, first_chars = fh.build_linked_folder_context(linked, [])
        reads_before = reads["n"]
        assert reads_before == 2  # both markdown files were read

        second, second_chars = fh.build_linked_folder_context(linked, [])
        assert second == first
        assert second_chars == first_chars
        assert reads["n"] == reads_before  # no re-read on the fast path

    def test_edit_invalidates_cache(self, tmp_path, monkeypatch):
        ws = _workspace(tmp_path)
        linked = [{"folder_path": ws}]

        first, _ = fh.build_linked_folder_context(linked, [])
        assert "Changed requirement" not in first

        req = os.path.join(ws, "Requirements", "BA-REQ.md")
        with open(req, "a", encoding="utf-8") as f:
            f.write("\nChanged requirement.\n")
        _bump_mtime(req)

        second, _ = fh.build_linked_folder_context(linked, [])
        assert "Changed requirement" in second
        assert second != first

    def test_new_file_invalidates_cache(self, tmp_path):
        ws = _workspace(tmp_path)
        linked = [{"folder_path": ws}]

        first, _ = fh.build_linked_folder_context(linked, [])
        assert "Brand new doc" not in first

        new_file = os.path.join(ws, "Design", "SEC-NEW.md")
        with open(new_file, "w", encoding="utf-8") as f:
            f.write("# Brand new doc\n")
        _bump_mtime(new_file)

        # Let the scan-list cache TTL lapse so the new file appears in the list,
        # then verify the content cache rebuilds.
        fh._linked_folder_cache.clear()
        second, _ = fh.build_linked_folder_context(linked, [])
        assert "Brand new doc" in second

    def test_single_linked_file_is_cached(self, tmp_path, monkeypatch):
        f = tmp_path / "snippet.md"
        f.write_text("hello cached world\n", encoding="utf-8")
        linked = [{"folder_path": str(f)}]
        reads = {"n": 0}

        original = fh.extract_text
        def counting_extract(path, name):
            reads["n"] += 1
            return original(path, name)
        monkeypatch.setattr(fh, "extract_text", counting_extract)

        first, _ = fh.build_linked_folder_context(linked, [])
        assert reads["n"] == 1

        second, _ = fh.build_linked_folder_context(linked, [])
        assert first == second
        assert reads["n"] == 1