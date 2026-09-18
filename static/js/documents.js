/**
 * documents.js — unified Markdown document panel + split viewer/editor.
 *
 * Documents come from two server-side backends (output directory & conversation
 * uploads), addressed by opaque ids like `output:report.md` / `upload:42`.
 *
 * The editor uses a styled <textarea> + the app's existing marked rendering.
 * All rendered HTML is sanitised (DOMPurify when present, with a small regex
 * fallback) before it is injected into the DOM. Saves use SHA-256 optimistic
 * concurrency and surface 409 conflicts without ever overwriting the newer
 * server version automatically.
 */

const MarkdownDocuments = {
  _convId: null,
  _docs: [],           // last list response
  _documentsModal: null,
  _editorModal: null,
  _state: null,        // { docId, name, kind, baseContent, currentContent,
                       //   originalHash, expectedHash, conflictHash,
                       //   conflictContent, dirty, mode }

  // ── Wiring ──────────────────────────────────────────────────────────────

  init() {
    const btn = _docEl("btnDocuments");
    if (btn) btn.addEventListener("click", () => this.openList());

    const list = _docEl("documentsList");
    if (list) list.addEventListener("click", (e) => this._onListClick(e));

    _docWire("btnDocumentsRefresh", () => this.refreshList());
    _docWire("btnDocumentsOpenFolder", () => this.openOutputFolder());
    _docWire("btnDocEdit", () => this.edit());
    _docWire("btnDocPreview", () => this.preview());
    _docWire("btnDocSave", () => this.save());
    _docWire("btnDocCancel", () => this.cancel());
    _docWire("btnDocRefresh", () => this.refreshFromDisk());
    _docWire("btnDocDownload", () => this.download());
    _docWire("btnDocFullscreen", () => this.toggleFullscreen());
    _docWire("btnDocEditorClose", () => this.closeEditor());
    _docWire("btnDocConflictReload", () => this.reloadServerVersion());
    _docWire("btnDocConflictDownload", () => this.download());
    _docWire("btnDocConflictCopy", () => this.copyLocal());
    _docWire("btnDocConflictMerge", () => this.toggleConflictServer());

    this._initMarkdownEditor();

    // Global hardening: external links opened from sanitised Markdown get
    // rel=noopener noreferrer. Registered once.
    if (typeof window !== "undefined" && typeof DOMPurify !== "undefined" &&
        DOMPurify.addHook && !window.__docRelHookInstalled) {
      window.__docRelHookInstalled = true;
      DOMPurify.addHook("afterSanitizeAttributes", function (node) {
        if (node.tagName === "A") {
          const href = node.getAttribute("href") || "";
          if (/^(https?:)?\/\//i.test(href)) {
            node.setAttribute("rel", "noopener noreferrer");
            node.setAttribute("target", "_blank");
          }
        }
      });
    }
  },

  // ── Listing ─────────────────────────────────────────────────────────────

  async load(convId) {
    this._convId = convId || this._convId;
    if (!this._convId) return;
    try {
      const data = await API.listDocuments(this._convId);
      this._docs = (data && data.documents) || [];
    } catch (_err) {
      this._docs = [];
    }
    this._renderList();
  },

  /** Public refresh hook (SSE document_created events, save-as-files, saves). */
  async refreshList() {
    if (!this._convId) {
      if (typeof App !== "undefined" && App.activeConvId) this._convId = App.activeConvId;
    }
    if (this._convId) await this.load(this._convId);
  },

  async openList() {
    await this.refreshList();
    if (this._documentsModal === null) this._documentsModal = this._modal("documentsModal");
    if (this._documentsModal) this._documentsModal.show();
  },

  /** Open the conversation's effective output folder in the OS file manager. */
  async openOutputFolder() {
    const convId = this._convId || (typeof App !== "undefined" ? App.activeConvId : null);
    if (!convId) return;
    try {
      await API.openOutputDir(convId);
    } catch (err) {
      if (typeof alert !== "undefined") alert(`Could not open the output folder: ${err.message}`);
    }
  },

  _renderList() {
    const list = _docEl("documentsList");
    if (!list) return;
    const meta = _docEl("documentsMeta");
    if (meta) meta.textContent = this._docs.length
      ? `${this._docs.length} document${this._docs.length === 1 ? "" : "s"}`
      : "";

    if (this._docs.length === 0) {
      list.innerHTML = `<div class="text-secondary text-center py-4 small">No Markdown documents yet.
        Ask the model to create a report, or attach a .md file.</div>`;
      return;
    }

    list.innerHTML = this._docs.map((d) => this._rowHtml(d)).join("");
  },

  _rowHtml(d) {
    const editBtn = d.editable
      ? `<button class="btn btn-sm btn-outline-secondary py-0 px-2" data-doc-action="edit" title="Edit">Edit</button>`
      : `<button class="btn btn-sm btn-outline-secondary py-0 px-2" disabled title="Read only">Edit</button>`;
    const managed = d.managed_artifact
      ? '<span class="doc-managed-badge" title="Managed project artifact">managed</span>'
      : "";
    return `
      <div class="doc-row" data-doc-id="${_docEsc(d.id)}">
        <i class="bi ${_docKindIcon(d.kind)} text-primary doc-icon flex-shrink-0"></i>
        <div class="flex-grow-1 min-w-0">
          <div class="text-truncate">
            <span class="doc-name" data-doc-action="view" title="${_docEsc(d.name)}">${_docEsc(d.name)}</span>${managed}
          </div>
          <div class="doc-meta">${_docFmtBytes(d.size_bytes)} · modified ${_docTimespan(d.modified_at)}${d.editable ? "" : " · read only"}</div>
        </div>
        <div class="doc-row-actions">
          <button class="btn btn-sm btn-outline-secondary py-0 px-2" data-doc-action="view" title="View">View</button>
          ${editBtn}
          <button class="btn btn-sm btn-outline-secondary py-0 px-2" data-doc-action="rename" title="Rename">Rename</button>
          <button class="btn btn-sm btn-outline-secondary py-0 px-2" data-doc-action="download" title="Download">Download</button>
          <button class="btn btn-sm btn-outline-secondary py-0 px-2" data-doc-action="refresh" title="Refresh list">Refresh</button>
        </div>
      </div>`;
  },

  async _onListClick(e) {
    const target = e && e.target;
    const actionEl = target && target.closest ? target.closest("[data-doc-action]") : null;
    const row = actionEl && actionEl.closest ? actionEl.closest("[data-doc-id]") : null;
    if (!actionEl || !row) return;
    const action = actionEl.getAttribute("data-doc-action");
    const docId = row.getAttribute("data-doc-id");
    if (!docId) return;
    if (action === "view") await this.openDocument(docId, false);
    else if (action === "edit") await this.openDocument(docId, true);
    else if (action === "rename") await this.renameDocument(docId);
    else if (action === "download") await this.downloadDoc(docId);
    else if (action === "refresh") await this.refreshList();
  },

  /** Rename a document (server jail-checks output paths). */
  async renameDocument(docId) {
    const current = (this._docs.find((d) => d.id === docId) || {}).name || "";
    let newName;
    if (typeof prompt === "function") {
      newName = prompt(`Rename "${current}" to (Markdown only):`, current);
    } else {
      newName = current;
    }
    if (newName === null || newName === undefined) return;
    newName = String(newName).trim();
    if (!newName || newName === current) return;
    const convId = this._convId || (typeof App !== "undefined" ? App.activeConvId : null);
    if (!convId) return;
    try {
      const res = await API.renameDocument(convId, docId, newName);
      if (res && res.success) await this.refreshList();
    } catch (err) {
      if (typeof alert !== "undefined") alert(`Rename failed: ${err.message}`);
    }
  },

  // ── Reading / opening ────────────────────────────────────────────────────

  async openDocument(docId, startEditing) {
    if (this._state && this._state.dirty && this._state.docId &&
        this._state.docId !== docId && !this._confirmDiscard()) return;
    let doc;
    try {
      doc = await API.getDocument(this._convId || (typeof App !== "undefined" ? App.activeConvId : null), docId);
    } catch (err) {
      if (typeof alert !== "undefined") alert(`Failed to open document: ${err.message}`);
      return;
    }
    if (!this._convId && typeof App !== "undefined") this._convId = App.activeConvId;
    this.openEditorContent(doc, startEditing);
  },

  /** Populate + show the editor for a loaded document response. */
  openEditorContent(doc, startEditing) {
    this._state = {
      docId: doc.id,
      name: doc.name,
      kind: doc.kind,
      baseContent: doc.content,
      currentContent: doc.content,
      originalHash: doc.content_hash,
      expectedHash: doc.content_hash,
      conflictHash: null,
      conflictContent: null,
      dirty: false,
      mode: startEditing ? "edit" : "preview",
      managed: !!doc.managed_artifact,
    };

    const title = _docEl("docEditorTitle");
    if (title) title.textContent = doc.name;
    const badge = _docEl("docEditorManagedBadge");
    if (badge) badge.classList.toggle("d-none", !doc.managed_artifact);

    this._setSource(doc.content);
    this._renderPreview(doc.content);
    this._hideConflict();
    this._updateDirty();
    this._updateStats();
    this._setMode(this._state.mode);

    if (this._editorModal === null) this._editorModal = this._modal("documentEditorModal");
    if (this._editorModal) this._editorModal.show();
    if (this._mde) {
      setTimeout(() => { try { this._mde.codemirror.refresh(); } catch (_e) {} }, 80);
    }
  },

  /** Download a document by id without opening the editor. */
  async downloadDoc(docId) {
    let doc = null;
    try {
      doc = await API.getDocument(this._convId || (typeof App !== "undefined" ? App.activeConvId : null), docId);
    } catch (_err) { /* fall through */ }
    this._downloadContent(doc ? doc.name : (docId.split(/[:/]/).pop() || "document.md"), doc ? doc.content : "");
  },

  /** Create EasyMDE (CodeMirror) when available; otherwise fall back to the
   * styled <textarea>. All editor reads/writes go through _getSource/_setSource. */
  _initMarkdownEditor() {
    const source = _docEl("docEditorSource");
    if (!source) return;

    const attachFallback = () => {
      source.addEventListener("input", () => {
        if (this._settingSource) return;
        if (!this._state) return;
        this._state.currentContent = this._getSource();
        this._state.dirty = true;
        this._updateDirty();
        this._updateStats();
        this._debouncedPreview();
      });
      // Ctrl/Cmd+S saves (convenience shortcut).
      source.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
          e.preventDefault();
          this.save();
        }
      });
    };

    if (typeof EasyMDE !== "undefined" && typeof EasyMDE === "function") {
      try {
        const mde = new EasyMDE({
          element: source,
          autoDownloadFontAwesome: false,
          spellChecker: false,
          status: ["lines", "words", "cursor"],
          minHeight: "200px",
          maxHeight: "none",
          toolbar: [
            "bold", "italic", "heading", "|",
            "quote", "unordered-list", "ordered-list", "table", "|",
            "link", "image", "|",
            "code", "horizontal-rule",
          ],
        });
        this._mde = mde;
        mde.codemirror.on("change", () => {
          if (this._settingSource) return;
          if (!this._state) return;
          this._state.currentContent = this._getSource();
          this._state.dirty = true;
          this._updateDirty();
          this._updateStats();
          this._debouncedPreview();
        });
        mde.codemirror.setOption("extraKeys", {
          "Ctrl-S": () => this.save(),
          "Cmd-S": () => this.save(),
        });
      } catch (_e) {
        this._mde = null;
        attachFallback();
      }
      return;
    }
    attachFallback();
  },

  _getSource() {
    if (this._mde) {
      try { return this._mde.value() || ""; } catch (_e) { /* fall through */ }
    }
    const src = _docEl("docEditorSource");
    return src ? (src.value || "") : "";
  },

  _focusEditor() {
    if (this._mde) { try { this._mde.codemirror.focus(); } catch (_e) {} return; }
    const src = _docEl("docEditorSource");
    if (src) { try { src.focus(); } catch (_e) {} }
  },

  /** Toggle full screen (the split editor keeps working inside the modal). */
  toggleFullscreen() {
    const modal = _docEl("documentEditorModal");
    if (!modal) return;
    this._fullscreen = !this._fullscreen;
    modal.classList.toggle("modal-fullscreen", this._fullscreen);
    const btn = _docEl("btnDocFullscreen");
    if (btn) {
      btn.innerHTML = this._fullscreen
        ? '<i class="bi bi-fullscreen-exit" title="Exit full screen"></i>'
        : '<i class="bi bi-arrows-fullscreen"></i>';
      btn.title = this._fullscreen ? "Exit full screen" : "Toggle full screen";
    }
    if (this._mde) { try { this._mde.codemirror.refresh(); } catch (_e) {} }
  },

  // ── Mode toggling ────────────────────────────────────────────────────────

  edit() {
    if (!this._state) return;
    this._state.mode = "edit";
    this._setMode("edit");
    setTimeout(() => this._focusEditor(), 50);
  },

  preview() {
    if (!this._state) return;
    this._state.mode = "preview";
    this._setMode("preview");
  },

  _setMode(mode) {
    const split = _docEl("docEditorSplit");
    if (split) split.classList.toggle("preview-only", mode !== "edit");
    // Hide the source editor (EasyMDE container or textarea) in preview mode
    // so the rendered document gets the full width.
    if (this._mde) {
      try { this._mde.container.style.display = mode === "edit" ? "" : "none"; } catch (_e) {}
    } else {
      const source = _docEl("docEditorSource");
      if (source && !this._mde) source.style.display = mode === "edit" ? "" : "none";
    }
    this._renderPreview(this._state ? this._state.currentContent : "");
  },

  // ── Rendering ────────────────────────────────────────────────────────────

  _debounceTimer: null,
  _debouncedPreview() {
    if (this._debounceTimer) clearTimeout(this._debounceTimer);
    this._debounceTimer = setTimeout(() => { this._debounceTimer = null; this._renderPreview(this._state && this._state.currentContent); }, 120);
  },

  _renderPreview(text) {
    const preview = _docEl("docEditorPreview");
    if (!preview) return;
    let html = "";
    if (typeof marked !== "undefined" && typeof marked.parse === "function") {
      try {
        html = marked.parse(text || "");
      } catch (_e) { html = _docEsc(text || ""); }
    } else {
      html = _docEsc(text || "");
    }
    preview.innerHTML = this._sanitizeHtml(html);
  },

  _sanitizeHtml(html) {
    if (typeof DOMPurify !== "undefined" && typeof DOMPurify.sanitize === "function") {
      return DOMPurify.sanitize(String(html || ""), {
        USE_PROFILES: { html: true },
        ADD_ATTR: ["target"],
      });
    }
    return _docFallbackSanitize(html);
  },

  // ── Dirty / stats ────────────────────────────────────────────────────────

  _updateDirty() {
    const el = _docEl("docEditorDirty");
    if (el) el.classList.toggle("d-none", !(this._state && this._state.dirty));
  },

  _updateStats() {
    const el = _docEl("docEditorStats");
    if (!el || !this._state) return;
    const text = this._state.currentContent || "";
    const words = (text.trim() ? text.trim().split(/\s+/).length : 0);
    el.textContent = `${text.length.toLocaleString()} chars · ${words.toLocaleString()} words`;
  },

  // ── Save (hash-based OCC + conflict safety) ──────────────────────────────

  async save() {
    if (!this._state || !this._state.docId) return;
    const s = this._state;
    const convId = this._convId || (typeof App !== "undefined" ? App.activeConvId : null);
    if (!convId) return;

    const payload = { content: s.currentContent, expected_hash: s.expectedHash };

    const saveBtn = _docEl("btnDocSave");
    if (saveBtn) { saveBtn.disabled = true; saveBtn.dataset.orig = saveBtn.innerHTML; saveBtn.innerHTML = "Saving…"; }
    try {
      const res = await API.saveDocument(convId, s.docId, payload.content, payload.expected_hash);
      if (res && res.conflict) {
        this.showConflict(res.data);
        return;
      }
      const data = (res && res.data) || {};
      s.baseContent = payload.content;
      s.currentContent = payload.content;
      s.originalHash = data.content_hash || s.originalHash;
      s.expectedHash = data.content_hash || s.expectedHash;
      s.dirty = false;
      this._hideConflict();
      this._updateDirty();
      this._updateStats();
      this.refreshList();
    } catch (err) {
      if (typeof alert !== "undefined") alert(`Save failed: ${err.message}`);
    } finally {
      if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = saveBtn.dataset.orig || '<i class="bi bi-check-lg me-1"></i>Save'; }
    }
  },

  _buildSavePayload() {
    const s = this._state;
    return { content: s ? s.currentContent : "", expected_hash: s ? s.expectedHash : null };
  },

  // ── Conflicts ────────────────────────────────────────────────────────────

  showConflict(data) {
    if (!this._state) return;
    const s = this._state;
    // The server content becomes the new concurrency baseline; a later explicit
    // Save will not silently overwrite an even newer server version.
    s.conflictHash = data.current_hash || null;
    s.conflictContent = data.current_content || "";
    s.expectedHash = data.current_hash || s.expectedHash;

    const serverTa = _docEl("docEditorConflictServer");
    if (serverTa) serverTa.value = s.conflictContent;
    const banner = _docEl("docEditorConflict");
    if (banner) banner.classList.remove("d-none");
  },

  _hideConflict() {
    const banner = _docEl("docEditorConflict");
    if (banner) banner.classList.add("d-none");
  },

  reloadServerVersion() {
    if (!this._state || !this._state.conflictContent) return;
    const s = this._state;
    s.currentContent = s.conflictContent;
    s.baseContent = s.conflictContent;
    s.originalHash = s.conflictHash;
    s.expectedHash = s.conflictHash;
    s.conflictHash = null;
    s.conflictContent = null;
    s.dirty = false;
    this._hideConflict();
    this._setSource(s.currentContent);
    this._renderPreview(s.currentContent);
    this._updateDirty();
    this._updateStats();
  },

  toggleConflictServer() {
    const ta = _docEl("docEditorConflictServer");
    if (ta) ta.classList.toggle("d-none");
  },

  copyLocal() {
    const text = this._state ? this._state.currentContent : "";
    if (navigator && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => this._flashCopied(), () => {});
    }
  },

  async download() {
    const s = this._state;
    const name = s ? s.name : "document.md";
    const content = s ? s.currentContent : "";
    this._downloadContent(name, content);
  },

  _downloadContent(name, content) {
    const blob = new Blob([content || ""], { type: "text/markdown" });
    try {
      if (window.navigator && window.navigator.msSaveBlob) {
        window.navigator.msSaveBlob(blob, name);
        return;
      }
    } catch (_e) { /* ignore */ }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); }, 1000);
    document.body.removeChild(a);
  },

  _flashCopied() {
    const btn = _docEl("btnDocConflictCopy");
    if (!btn) return;
    const orig = btn.innerHTML;
    btn.innerHTML = '<i class="bi bi-clipboard-check me-1"></i>Copied to clipboard';
    setTimeout(() => { btn.innerHTML = orig; }, 1600);
  },

  // ── Cancel / refresh from disk / close ───────────────────────────────────

  cancel() {
    if (!this._state) return;
    if (this._state.dirty && !this._confirmDiscard()) return;
    const s = this._state;
    s.currentContent = s.baseContent;
    s.expectedHash = s.originalHash;
    s.dirty = false;
    this._hideConflict();
    this._setSource(s.currentContent);
    this._renderPreview(s.currentContent);
    this._updateDirty();
    this._updateStats();
  },

  async refreshFromDisk() {
    if (!this._state || !this._state.docId) return;
    if (this._state.dirty && !this._confirmDiscard()) return;
    const docId = this._state.docId;
    const convId = this._convId || (typeof App !== "undefined" ? App.activeConvId : null);
    try {
      const doc = await API.getDocument(convId, docId);
      this._state.baseContent = doc.content;
      this._state.currentContent = doc.content;
      this._state.originalHash = doc.content_hash;
      this._state.expectedHash = doc.content_hash;
      this._state.dirty = false;
      this._state.conflictHash = null;
      this._state.conflictContent = null;
      this._hideConflict();
      this._setSource(doc.content);
      this._renderPreview(doc.content);
      this._updateDirty();
      this._updateStats();
    } catch (err) {
      if (typeof alert !== "undefined") alert(`Refresh failed: ${err.message}`);
    }
  },

  closeEditor() {
    if (this._state && this._state.dirty && !this._confirmDiscard()) return;
    this._state = null;
    if (this._editorModal) this._editorModal.hide();
  },

  _confirmDiscard() {
    if (typeof confirm !== "function") return true;
    return confirm("Discard unsaved changes? Your edits have not been saved.");
  },

  // ── misc ─────────────────────────────────────────────────────────────────

  _setSource(text) {
    this._settingSource = true;
    try {
      if (this._mde) {
        try { this._mde.value(text || ""); }
        catch (_e) { const src = _docEl("docEditorSource"); if (src) src.value = text || ""; }
      } else {
        const src = _docEl("docEditorSource");
        if (src) src.value = text || "";
      }
    } finally {
      this._settingSource = false;
    }
    this._renderPreview(text || "");
  },

  _modal(id) {
    if (!this._modals) this._modals = {};
    if (!this._modals[id]) {
      const el = _docEl(id);
      if (el && typeof bootstrap !== "undefined" && bootstrap.Modal) {
        this._modals[id] = new bootstrap.Modal(el);
      } else {
        this._modals[id] = { show() {}, hide() {} };
      }
    }
    return this._modals[id];
  },
};

// ── module-local helpers ──────────────────────────────────────────────────────

function _docEl(id) {
  if (typeof document === "undefined") return null;
  return document.getElementById(id) || null;
}

function _docWire(id, handler) {
  const el = _docEl(id);
  if (el) el.addEventListener("click", handler);
}

function _docEsc(str) {
  return String(str === undefined || str === null ? "" : str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function _docKindIcon(kind) {
  return kind === "upload" ? "bi-upload" : "bi-file-earmark-text";
}

function _docFmtBytes(b) {
  b = Number(b || 0);
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}

function _docTimespan(ts) {
  if (!ts) return "unknown";
  let when;
  try { when = new Date(ts).getTime(); } catch (_e) { return "unknown"; }
  if (isNaN(when)) return "unknown";
  let diff = Math.floor((Date.now() - when) / 1000);
  if (diff < 0) diff = 0;
  if (diff < 60) return "just now";
  const steps = [["minute", 60], ["hour", 3600], ["day", 86400], ["week", 604800], ["month", 2592000], ["year", 31536000]];
  let prev = 60;
  let label = "minute";
  for (const [name, secs] of steps) {
    if (diff < secs) { label = name; break; }
    prev = secs;
    label = name;
  }
  const n = Math.max(1, Math.floor(diff / prev));
  return `${n} ${label}${n === 1 ? "" : "s"} ago`;
}

/** Small dependency-free safety filter used when DOMPurify is unavailable. */
function _docFallbackSanitize(html) {
  return String(html || "")
    .replace(/<\s*script[\s\S]*?<\s*\/\s*script\s*>/gi, "")
    .replace(/<\s*(iframe|object|embed)[^>]*>[\s\S]*?<\s*\/\s*(iframe|object|embed)\s*>/gi, "")
    .replace(/\son\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, "")
    .replace(/(href|src)\s*=\s*("javascript:[^"]*"|'javascript:[^']*')/gi, "")
    .replace(/(href|src)\s*=\s*(javascript:[^\s>]+)/gi, "");
}