/**
 * markdown_import.js — server-side "Import Markdown" browser.
 *
 * Native <input type="file"> pickers cannot be made to open in a chosen
 * folder (browser security), so the Import Markdown button opens this
 * server-side browser that starts inside the conversation's *effective*
 * output directory — jail-checked server-side.
 *
 * Folders navigate; Markdown/txt files are copied into the conversation as
 * uploads (appearing in the file chips and the unified document panel).
 */

const ImportMarkdown = {
  _modal: null,
  _convId: null,
  _current: "",   // relative path inside the output directory
  _up: null,

  init() {
    const btn = _miEl("btnImportMd");
    if (btn) btn.addEventListener("click", () => this.open());

    const list = _miEl("mdImportList");
    if (list) list.addEventListener("click", (e) => this._onListClick(e));

    _miWire("btnMdImportUp", () => this._up());
    _miWire("btnMdImportNative", () => {
      const input = _miEl("mdContextInput");
      if (input) input.click();
    });
  },

  async open() {
    let convId = null;
    if (typeof App !== "undefined") convId = App.activeConvId;
    if (!convId) return;
    this._convId = convId;
    this._current = "";
    if (this._modal === null) this._modal = this._modalInstance("mdImportModal");
    if (this._modal) this._modal.show();
    await this._navigate("");
  },

  _modalInstance(id) {
    if (!this._modals) this._modals = {};
    if (!this._modals[id]) {
      const el = _miEl(id);
      if (el && typeof bootstrap !== "undefined" && bootstrap.Modal) {
        this._modals[id] = new bootstrap.Modal(el);
      } else {
        this._modals[id] = { show() {}, hide() {} };
      }
    }
    return this._modals[id];
  },

  async _navigate(rel) {
    this._current = rel || "";
    const listEl = _miEl("mdImportList");
    if (!listEl) return;
    listEl.innerHTML = `<div class="text-secondary text-center py-4">
      <span class="spinner-border spinner-border-sm me-2"></span>Loading…</div>`;

    let data;
    try {
      data = await API.browseOutputImport(this._convId, this._current);
    } catch (err) {
      listEl.innerHTML = `<div class="text-danger small p-2">Error: ${_miEsc(err.message)}</div>`;
      return;
    }
    if (!data) return;

    this._up = (data.up === undefined ? null : data.up);
    const upBtn = _miEl("btnMdImportUp");
    if (upBtn) upBtn.disabled = (this._up === null);

    const dirEl = _miEl("mdImportOutputDir");
    if (dirEl) dirEl.textContent = data.output_dir || "";

    const crumb = _miEl("mdImportBreadcrumb");
    if (crumb) crumb.innerHTML = this._buildBreadcrumb(this._current);

    if (!data.entries || data.entries.length === 0) {
      listEl.innerHTML = `<div class="text-secondary small p-3">This folder has no Markdown files.</div>`;
      return;
    }

    listEl.innerHTML = data.entries.map((e) => this._rowHtml(e)).join("");
  },

  _up() {
    if (this._up === null || this._up === undefined) return;
    this._navigate(this._up === "" ? "" : this._up);
  },

  _buildBreadcrumb(rel) {
    const root = `<li class="breadcrumb-item">` +
      `<a href="#" class="text-info text-decoration-none" ` +
      `onclick="event.preventDefault();ImportMarkdown._navigate('')">Output folder</a></li>`;
    if (!rel) return root + `<li class="breadcrumb-item active">/</li>`;
    const parts = rel.split("/");
    let accumulated = "";
    const crumbs = parts.map((part, i) => {
      accumulated += (i === 0 ? "" : "/") + part;
      const path = accumulated;
      if (i === parts.length - 1) return `<li class="breadcrumb-item active">${_miEsc(part)}</li>`;
      return `<li class="breadcrumb-item"><a href="#" class="text-info text-decoration-none" ` +
        `onclick="event.preventDefault();ImportMarkdown._navigate('${_miAttr(path)}')">${_miEsc(part)}</a></li>`;
    });
    return root + crumbs.join("");
  },

  _rowHtml(e) {
    if (e.is_dir) {
      return `
        <div class="browser-entry browser-entry-dir d-flex align-items-center" data-mdir="${_miAttr(e.rel_path)}">
          <i class="bi bi-folder-fill text-warning me-2"></i>
          <span class="flex-grow-1">${_miEsc(e.name)}</span>
          <i class="bi bi-chevron-right text-secondary ms-auto"></i>
        </div>`;
    }
    return `
      <div class="browser-entry browser-entry-file d-flex align-items-center" data-mfile="${_miAttr(e.rel_path)}">
        <i class="${_miFileIcon(e.ext)} text-primary me-2"></i>
        <span class="flex-grow-1">${_miEsc(e.name)}</span>
        <span class="text-secondary flex-shrink-0" style="font-size:0.75rem">${_miFmtBytes(e.size_bytes)}</span>
        <i class="bi bi-plus-circle text-primary ms-2"></i>
      </div>`;
  },

  async _onListClick(e) {
    const target = e && e.target;
    const dir = target && target.closest ? target.closest("[data-mdir]") : null;
    if (dir) {
      await this._navigate(dir.getAttribute("data-mdir"));
      return;
    }
    const fileEl = target && target.closest ? target.closest("[data-mfile]") : null;
    if (fileEl) await this._import(fileEl.getAttribute("data-mfile"));
  },

  async _import(rel) {
    try {
      const res = await API.importMarkdownFromOutput(this._convId, rel);
      if (res && res.success !== false && res.original_name) {
        if (typeof Chat !== "undefined" && Chat.appendToolNotification) {
          Chat.appendToolNotification(true, `Imported \`${res.original_name}\` from the output folder.`);
        }
      }
      if (this._modal) this._modal.hide();
      if (typeof Files !== "undefined" && Files.load) { try { await Files.load(this._convId); } catch (_e) {} }
      if (typeof MarkdownDocuments !== "undefined" && MarkdownDocuments.refreshList) {
        try { await MarkdownDocuments.refreshList(); } catch (_e) {}
      }
    } catch (err) {
      if (typeof alert !== "undefined") alert(`Import failed: ${err.message}`);
    }
  },
};

// ── helpers ──────────────────────────────────────────────────────────────────

function _miEl(id) {
  if (typeof document === "undefined") return null;
  return document.getElementById(id) || null;
}

function _miWire(id, handler) {
  const el = _miEl(id);
  if (el) el.addEventListener("click", handler);
}

function _miEsc(str) {
  return String(str === undefined || str === null ? "" : str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _miAttr(str) {
  return _miEsc(String(str || "").replace(/'/g, "&#39;"));
}

function _miFileIcon(ext) {
  const map = {
    ".md": "bi-file-earmark-text", ".markdown": "bi-file-earmark-text",
    ".txt": "bi-file-earmark-text",
  };
  return map[String(ext || "").toLowerCase()] || "bi-file-earmark";
}

function _miFmtBytes(b) {
  b = Number(b || 0);
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}