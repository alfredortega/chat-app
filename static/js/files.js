/**
 * files.js — per-conversation file attachment UI
 */

// File types the server accepts (mirrors file_handler.py ALLOWED_EXTENSIONS)
const ALLOWED_EXTS = new Set([
  "txt","md","markdown","py","js","ts","jsx","tsx","html","htm","css","scss",
  "json","yaml","yml","toml","ini","env","sh","bat","ps1","sql","csv","pdf",
  "docx","xlsx","xls","xml","log","rs","go","java","c","cpp","h","cs","rb","php",
]);

const Files = {
  _files: [],       // { id, original_name, size_bytes, char_count }
  _convId: null,

  // ── Load ───────────────────────────────────────────────────────────────────

  async load(convId) {
    this._convId = convId;
    try {
      this._files = await API.listFiles(convId);
    } catch {
      this._files = [];
    }
    this._render();
  },

  clear() {
    this._files = [];
    this._convId = null;
    this._render();
  },

  // ── Upload ─────────────────────────────────────────────────────────────────

  /**
   * Upload an array of File objects. For folder uploads the File's
   * webkitRelativePath is sent as the filename so the server can store the
   * relative path for display purposes.
   */
  async uploadFiles(fileList) {
    if (!this._convId) return;

    // Filter to only supported extensions (silently skip the rest)
    const supported = Array.from(fileList).filter((f) => {
      const ext = f.name.split(".").pop().toLowerCase();
      return ALLOWED_EXTS.has(ext);
    });

    if (supported.length === 0) return;

    // Show a progress indicator in the chip area while uploading
    this._showUploadProgress(supported.length);

    let done = 0;
    for (const file of supported) {
      try {
        // Use the relative path (set for folder uploads) as the display name
        const displayName = file.webkitRelativePath || file.name;
        const record = await API.uploadFile(this._convId, file, displayName);

        if (record.warn_large) {
          const proceed = confirm(
            `"${record.original_name}" is very large (${_fmtChars(record.char_count)} characters extracted).\n\n` +
            `Injecting it into the model context may produce slow or degraded responses.\n\n` +
            `Do you want to keep it attached?`
          );
          if (!proceed) {
            await this._removeById(record.id, record.original_name, false);
            done++;
            this._showUploadProgress(supported.length, done);
            continue;
          }
        }

        if (record.truncated) {
          alert(
            `"${record.original_name}" was very large and has been truncated to 500,000 characters.\n` +
            `Only the first portion of the file will be visible to the model.`
          );
        }

        this._files.push(record);
      } catch (err) {
        console.warn(`Failed to upload "${file.name}":`, err.message);
      }

      done++;
      this._showUploadProgress(supported.length, done);
    }

    this._render();
  },

  _showUploadProgress(total, done = 0) {
    const wrapper = document.getElementById("attachedFiles");
    const list    = document.getElementById("attachedFilesList");
    if (!wrapper || !list) return;
    if (done >= total) {
      // Final render handled by _render()
      return;
    }
    wrapper.classList.remove("d-none");
    // Show a temporary progress chip
    const existing = list.querySelectorAll(".file-chip:not(.upload-progress)");
    const progressHtml = `
      <span class="file-chip upload-progress">
        <span class="spinner-border spinner-border-sm text-primary" style="width:12px;height:12px"></span>
        <span class="text-secondary">Uploading ${done}/${total}…</span>
      </span>`;
    // Rebuild: keep real chips + append progress
    list.innerHTML = this._files.map((f) => this._chipHtml(f)).join("") + progressHtml;
  },

  // ── Remove ─────────────────────────────────────────────────────────────────

  async remove(fileId) {
    const record = this._files.find((f) => f.id === fileId);
    if (!record) return;
    await this._removeById(fileId, record.original_name, true);
  },

  async _removeById(fileId, filename, confirmPrompt) {
    if (confirmPrompt && !confirm(`Remove "${filename}" from this conversation?`)) return;
    try {
      await API.deleteFile(this._convId, fileId);
      this._files = this._files.filter((f) => f.id !== fileId);
      this._render();
    } catch (err) {
      alert(`Failed to remove file: ${err.message}`);
    }
  },

  // ── Render ─────────────────────────────────────────────────────────────────

  _chipHtml(f) {
    return `
      <span class="file-chip d-inline-flex align-items-center gap-1">
        <i class="bi ${_fileIcon(f.original_name)} text-primary"></i>
        <span class="file-chip-name" title="${_esc(f.original_name)}"
              style="cursor:pointer;text-decoration:underline dotted"
              onclick="App.previewFile(${f.conversation_id}, ${f.id}, ${JSON.stringify(f.original_name)})"
        >${_esc(f.original_name)}</span>
        <span class="text-secondary" style="font-size:0.72rem">${_fmtBytes(f.size_bytes)}</span>
        <button class="file-chip-remove" onclick="Files.remove(${f.id})" title="Remove">
          <i class="bi bi-x"></i>
        </button>
      </span>`;
  },

  /** Called by LinkedFolders._render() to append file chips after folder chips */
  _appendChipsTo(listEl) {
    listEl.insertAdjacentHTML("beforeend", this._files.map((f) => this._chipHtml(f)).join(""));
  },

  _render() {
    const wrapper = document.getElementById("attachedFiles");
    const list    = document.getElementById("attachedFilesList");
    if (!wrapper || !list) return;

    const hasFiles   = this._files.length > 0;
    const hasFolders = typeof LinkedFolders !== "undefined" && LinkedFolders._folders.length > 0;

    if (!hasFiles && !hasFolders) {
      wrapper.classList.add("d-none");
      list.innerHTML = "";
      return;
    }

    wrapper.classList.remove("d-none");

    // Folder chips first, then file chips
    const folderHtml = (typeof LinkedFolders !== "undefined")
      ? LinkedFolders._folders.map((f) => LinkedFolders._chipHtml(f)).join("")
      : "";
    list.innerHTML = folderHtml + this._files.map((f) => this._chipHtml(f)).join("");
  },
};

// ── Helpers ────────────────────────────────────────────────────────────────────

function _fmtBytes(b) {
  if (b < 1024)        return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}

function _fmtChars(n) {
  return n.toLocaleString();
}

function _fileIcon(name) {
  const ext = name.split(".").pop().toLowerCase();
  const map = {
    pdf:  "bi-file-earmark-pdf",
    docx: "bi-file-earmark-word", doc: "bi-file-earmark-word",
    xlsx: "bi-file-earmark-excel", xls: "bi-file-earmark-excel",
    csv:  "bi-file-earmark-spreadsheet",
    py:   "bi-file-earmark-code",
    js: "bi-file-earmark-code", ts: "bi-file-earmark-code",
    json: "bi-file-earmark-code",
    md:   "bi-file-earmark-text", markdown: "bi-file-earmark-text",
    txt:  "bi-file-earmark-text",
    html: "bi-file-earmark-code", htm: "bi-file-earmark-code",
  };
  return map[ext] || "bi-file-earmark";
}

function _esc(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
