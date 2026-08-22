/**
 * folder_browser.js — server-side folder browser modal.
 *
 * Opens a modal that lets the user navigate the server filesystem and
 * select a folder to link to the active conversation.
 */

const FolderBrowser = {
  _modal:   null,
  _convId:  null,
  _currentPath: null,
  _selected: new Map(), // path -> { path, name, is_dir }

  init() {
    this._modal = new bootstrap.Modal(document.getElementById("folderBrowserModal"));
    document.getElementById("btnFolderBrowserSelect")
      .addEventListener("click", () => this._selectCurrent());
    document.getElementById("btnFolderBrowserLinkSelected")
      .addEventListener("click", () => this._linkSelected());
    document.getElementById("btnFolderBrowserUp")
      .addEventListener("click", () => this._navigateUp());
  },

  // ── Open ───────────────────────────────────────────────────────────────────

  async open(convId) {
    this._convId = convId;
    this._selected = new Map();
    this._modal.show();
    // Start at browser_root (server resolves the default if empty)
    await this._navigate(App.browserRoot || "");
  },

  // ── Navigation ─────────────────────────────────────────────────────────────

  async _navigate(path) {
    const listEl    = document.getElementById("folderBrowserList");
    const breadcrumb = document.getElementById("folderBrowserBreadcrumb");
    const upBtn      = document.getElementById("btnFolderBrowserUp");

    listEl.innerHTML = `
      <div class="text-secondary text-center py-4">
        <span class="spinner-border spinner-border-sm me-2"></span>Loading…
      </div>`;

    let data;
    try {
      data = await API.browse(path);
    } catch (err) {
      listEl.innerHTML = `<div class="text-danger small p-2">Error: ${_escH(err.message)}</div>`;
      return;
    }

    this._currentPath = data.path;

    // Breadcrumb
    breadcrumb.innerHTML = this._buildBreadcrumb(data.path);

    // Up button
    upBtn.disabled = !data.parent;

    // Select-this-folder button label
    const selectBtn  = document.getElementById("btnFolderBrowserSelect");
    const folderName = data.path.replace(/\\/g, "/").split("/").filter(Boolean).pop() || data.path;
    selectBtn.textContent = `Select "${folderName}"`;

    // File/folder list
    if (data.entries.length === 0) {
      listEl.innerHTML = `<div class="text-secondary small p-3">This folder is empty.</div>`;
      this._renderSelectionBar();
      return;
    }

    const normalBase = data.path.replace(/\\/g, "/").replace(/\/$/, "");
    listEl.innerHTML = "";

    data.entries.forEach((e) => {
      const fullPath = normalBase + "/" + e.name;
      const allowed  = e.is_dir || ALLOWED_EXTS.has(e.ext.replace(".", "").toLowerCase());

      const row = document.createElement("div");
      row.className = "browser-entry d-flex align-items-center"
        + (e.is_dir ? " browser-entry-dir" : " browser-entry-file")
        + (allowed ? "" : " opacity-50");

      // Checkbox for multiselect (only for linkable entries)
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "form-check-input flex-shrink-0 me-2 browser-entry-check";
      check.checked = this._selected.has(fullPath);
      check.disabled = !allowed;
      check.title = allowed ? "Select for linking" : "Unsupported file type";
      check.addEventListener("click", (ev) => ev.stopPropagation());
      check.addEventListener("change", () => {
        FolderBrowser._toggleSelect(fullPath, e.name, e.is_dir, check.checked);
      });
      row.appendChild(check);

      // Body (navigates into folders, or toggles selection for files)
      const body = document.createElement("button");
      body.type = "button";
      body.className = "browser-entry-body d-flex align-items-center flex-grow-1 text-start border-0 bg-transparent p-0";
      if (e.is_dir) {
        body.innerHTML = `
          <i class="bi bi-folder-fill text-warning me-2"></i>
          <span class="flex-grow-1">${_escH(e.name)}</span>
          <i class="bi bi-chevron-right text-secondary ms-auto"></i>`;
        body.addEventListener("click", () => FolderBrowser._navigate(fullPath));
      } else {
        body.disabled = !allowed;
        body.title = allowed ? "Link this file" : "Unsupported file type";
        body.innerHTML = `
          <i class="bi ${_browserFileIcon(e.ext)} text-secondary me-2"></i>
          <span class="flex-grow-1">${_escH(e.name)}</span>
          <span class="text-secondary" style="font-size:0.75rem">${_fmtBytesB(e.size_bytes)}</span>`;
        if (allowed) {
          body.addEventListener("click", () => {
            check.checked = !check.checked;
            FolderBrowser._toggleSelect(fullPath, e.name, e.is_dir, check.checked);
          });
        }
      }
      row.appendChild(body);

      listEl.appendChild(row);
    });

    this._renderSelectionBar();
  },

  _navigateUp() {
    if (!this._currentPath) return;
    // Use the parent path returned by the server in the last response.
    // Re-fetch data to get the accurate parent rather than guessing client-side.
    API.browse(this._currentPath).then((data) => {
      if (data.parent) this._navigate(data.parent);
    }).catch(() => {});
  },

  _buildBreadcrumb(fullPath) {
    const parts = fullPath.replace(/\\/g, "/").split("/").filter(Boolean);
    if (parts.length === 0) return `<span class="breadcrumb-item active">/</span>`;

    let accumulated = "";
    const items = parts.map((part, i) => {
      // On Windows the first part is the drive letter e.g. "C:"
      accumulated += (i === 0 ? "" : "/") + part;
      const path = accumulated;
      const isLast = i === parts.length - 1;
      if (isLast) {
        return `<li class="breadcrumb-item active">${_escH(part)}</li>`;
      }
      return `<li class="breadcrumb-item">
        <a href="#" class="text-info text-decoration-none"
           onclick="event.preventDefault();FolderBrowser._navigate(${JSON.stringify(path)})"
        >${_escH(part)}</a>
      </li>`;
    });
    return items.join("");
  },

  // ── Multiselect ─────────────────────────────────────────────────────────────

  _toggleSelect(path, name, isDir, checked) {
    if (checked) {
      this._selected.set(path, { path, name, is_dir: isDir });
    } else {
      this._selected.delete(path);
    }
    this._renderSelectionBar();
  },

  _renderSelectionBar() {
    const bar   = document.getElementById("folderBrowserSelectionBar");
    const label = document.getElementById("folderBrowserSelectionLabel");
    const btn   = document.getElementById("btnFolderBrowserLinkSelected");
    if (!bar) return;

    const count = this._selected.size;
    if (count === 0) {
      bar.classList.add("d-none");
      return;
    }
    bar.classList.remove("d-none");
    label.textContent = `${count} item${count === 1 ? "" : "s"} selected`;
    btn.innerHTML = `<i class="bi bi-link-45deg me-1"></i>Link ${count} selected`;
  },

  clearSelection() {
    this._selected.clear();
    // Uncheck any visible checkboxes
    document.querySelectorAll("#folderBrowserList .browser-entry-check")
      .forEach((c) => { c.checked = false; });
    this._renderSelectionBar();
  },

  // ── Select ─────────────────────────────────────────────────────────────────

  async _selectCurrent() {
    if (!this._currentPath || !this._convId) return;
    const btnSel = document.getElementById("btnFolderBrowserSelect");
    const original = btnSel.textContent;
    btnSel.disabled = true;
    btnSel.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Linking…`;
    try {
      const linked = await this._link(this._currentPath);
      if (linked) this._modal.hide();
    } finally {
      btnSel.disabled = false;
      btnSel.textContent = original;
    }
  },

  /** Link every checkbox-selected item in a single batch. */
  async _linkSelected() {
    if (!this._convId || this._selected.size === 0) return;
    const btn = document.getElementById("btnFolderBrowserLinkSelected");
    const items = Array.from(this._selected.values());
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Linking…`;

    const failures = [];
    let anyLinked = false;
    for (const item of items) {
      try {
        const linked = await this._link(item.path, { silent: true });
        if (linked) anyLinked = true;
      } catch (err) {
        failures.push(`${item.name}: ${err.message}`);
      }
    }

    btn.disabled = false;
    this._renderSelectionBar();

    if (failures.length) {
      alert(`Some items could not be linked:\n\n${failures.join("\n")}`);
    }
    if (anyLinked) {
      this.clearSelection();
      this._modal.hide();
    }
  },

  /**
   * Link a single path (file or folder).
   * Returns true if the link was kept, false if the user cancelled.
   * Pass { silent:true } to skip the modal-hide (batch caller handles it)
   * — the large-content warning is still shown per item.
   */
  async _link(path, opts = {}) {
    const record = await API.addLinkedFolder(this._convId, path);

    if (record.warn_large) {
      const chars = record.total_chars.toLocaleString();
      const scope = record.is_file
        ? `${chars} characters`
        : `${chars} characters across ${record.file_count} files`;
      const proceed = confirm(
        `"${_folderBasename(record.folder_path)}" contains a large amount of text (${scope}).\n\n` +
        `Injecting it into the model context may produce slow or degraded responses.\n\n` +
        `Do you want to keep it linked?`
      );
      if (!proceed) {
        await API.deleteLinkedFolder(this._convId, record.id);
        return false;
      }
    }

    // Avoid duplicate chips if the same path is already present
    if (!LinkedFolders._folders.some((f) => f.id === record.id)) {
      LinkedFolders._folders.push(record);
      LinkedFolders._render();
    }
    return true;
  },
};

// ── Helpers ────────────────────────────────────────────────────────────────────

function _browserFileIcon(ext) {
  const map = {
    ".pdf":  "bi-file-earmark-pdf",
    ".docx": "bi-file-earmark-word",  ".doc": "bi-file-earmark-word",
    ".xlsx": "bi-file-earmark-excel", ".xls": "bi-file-earmark-excel",
    ".csv":  "bi-file-earmark-spreadsheet",
    ".py":   "bi-file-earmark-code",
    ".js":   "bi-file-earmark-code",  ".ts": "bi-file-earmark-code",
    ".json": "bi-file-earmark-code",
    ".md":   "bi-file-earmark-text",
    ".txt":  "bi-file-earmark-text",
    ".html": "bi-file-earmark-code",
  };
  return map[ext] || "bi-file-earmark";
}

function _escH(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _fmtBytesB(b) {
  if (!b)              return "";
  if (b < 1024)        return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}
