/**
 * save_files.js — "Save as files" modal: split a multi-section markdown
 * response into individual files and write each one to disk via write_file.
 */

const SaveFiles = {
  _modal: null,
  _sections: [],   // [{ filename, content }]

  init() {
    this._modal = new bootstrap.Modal(document.getElementById("saveFilesModal"));
    document.getElementById("btnSaveFilesConfirm")
      .addEventListener("click", () => this.save());
  },

  /**
   * Parse the markdown into sections and open the modal.
   * Each ## heading becomes a separate file.
   */
  open(markdown) {
    this._sections = this._parse(markdown);
    if (this._sections.length === 0) return;

    const outputDir = App.outputDir || "";
    document.getElementById("saveFilesOutputDir").value = outputDir;

    this._renderPreview();
    this._modal.show();
  },

  // ── Split markdown by ## headings ──────────────────────────────────────────

  _parse(markdown) {
    const lines   = markdown.split("\n");
    const sections = [];
    let current   = null;

    for (const line of lines) {
      const match = line.match(/^##\s+(.+)/);
      if (match) {
        if (current) sections.push(current);
        const title    = match[1].trim();
        const filename = _titleToFilename(title);
        current = { title, filename, content: `## ${title}\n` };
      } else if (current) {
        current.content += line + "\n";
      }
    }
    if (current) sections.push(current);

    return sections;
  },

  // ── Preview list ───────────────────────────────────────────────────────────

  _renderPreview() {
    const list = document.getElementById("saveFilesSectionList");
    list.innerHTML = this._sections.map((s, i) => `
      <div class="d-flex align-items-center gap-2 py-1">
        <i class="bi bi-file-earmark-text text-primary flex-shrink-0"></i>
        <input type="text"
               class="form-control form-control-sm border-secondary flex-grow-1"
               id="sfName_${i}"
               value="${_esc(s.filename)}" />
        <span class="text-secondary small flex-shrink-0" style="min-width:60px">
          ${_fmtBytes(new Blob([s.content]).size)}
        </span>
      </div>
    `).join("");
  },

  // ── Save ───────────────────────────────────────────────────────────────────

  async save() {
    const dir = document.getElementById("saveFilesOutputDir").value.trim();
    if (!dir) {
      alert("Please enter an output directory path.");
      return;
    }

    // Persist the chosen directory so it becomes the new default
    App.outputDir = dir;
    try { await API.saveSettings({ output_dir: dir }); } catch (_) {}

    // Collect (possibly edited) filenames
    const items = this._sections.map((s, i) => {
      const nameEl = document.getElementById(`sfName_${i}`);
      const name   = (nameEl ? nameEl.value.trim() : s.filename) || s.filename;
      const path   = dir.replace(/[\\/]$/, "") + "/" + name;
      return { path, content: s.content };
    });

    this._modal.hide();

    // Write each file via the existing write_file tool API
    for (const item of items) {
      try {
        const res = await fetch(`/api/write_file`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: item.path, content: item.content }),
        });
        const data = await res.json();
        Chat.appendToolNotification(data.success, data.display);
      } catch (err) {
        Chat.appendToolNotification(false, `Failed to save file: ${err.message}`);
      }
    }
    Chat.scrollToBottom();
  },
};

function _titleToFilename(title) {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "")
    .slice(0, 60) + ".md";
}

function _esc(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _fmtBytes(b) {
  if (b < 1024)         return `${b} B`;
  if (b < 1024 * 1024)  return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}
