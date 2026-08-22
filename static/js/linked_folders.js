/**
 * linked_folders.js — per-conversation linked folder chips UI
 */

const LinkedFolders = {
  _folders: [],   // { id, folder_path, file_count, is_file }
  _convId:  null,

  // ── Load ───────────────────────────────────────────────────────────────────

  async load(convId) {
    this._convId  = convId;
    try {
      this._folders = await API.listLinkedFolders(convId);
    } catch {
      this._folders = [];
    }
    this._render();
  },

  clear() {
    this._folders = [];
    this._convId  = null;
    this._render();
  },

  // ── Remove ─────────────────────────────────────────────────────────────────

  async remove(folderId) {
    const record = this._folders.find((f) => f.id === folderId);
    if (!record) return;
    const name = _folderBasename(record.folder_path);
    const kind = record.is_file ? "file" : "folder";
    if (!confirm(`Unlink ${kind} "${name}" from this conversation?\n\nFiles on disk will not be affected.`)) return;
    try {
      await API.deleteLinkedFolder(this._convId, folderId);
      this._folders = this._folders.filter((f) => f.id !== folderId);
      this._render();
    } catch (err) {
      alert(`Failed to unlink ${kind}: ${err.message}`);
    }
  },

  // ── Render ─────────────────────────────────────────────────────────────────

  _chipHtml(f) {
    const name = _folderBasename(f.folder_path);
    if (f.is_file) {
      return `
        <span class="file-chip linked-folder-chip d-inline-flex align-items-center gap-1"
              title="${_escLF(f.folder_path)}">
          <i class="bi bi-file-earmark-text text-info"></i>
          <span class="file-chip-name">${_escLF(name)}</span>
          <button class="file-chip-remove" onclick="LinkedFolders.remove(${f.id})" title="Unlink file">
            <i class="bi bi-x"></i>
          </button>
        </span>`;
    }
    const count = f.file_count != null ? ` (${f.file_count} files)` : "";
    return `
      <span class="file-chip linked-folder-chip d-inline-flex align-items-center gap-1"
            title="${_escLF(f.folder_path)}">
        <i class="bi bi-folder-symlink text-warning"></i>
        <span class="file-chip-name">${_escLF(name)}${_escLF(count)}</span>
        <button class="file-chip-remove" onclick="LinkedFolders.remove(${f.id})" title="Unlink folder">
          <i class="bi bi-x"></i>
        </button>
      </span>`;
  },

  _render() {
    // Delegate entirely to Files._render() which owns the DOM and
    // calls back into this._chipHtml() for folder chips.
    Files._render();
  },
};

function _folderBasename(p) {
  return p.replace(/\\/g, "/").split("/").filter(Boolean).pop() || p;
}

function _escLF(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
