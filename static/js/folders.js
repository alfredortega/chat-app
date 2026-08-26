/**
 * folders.js — sidebar conversation folder management
 *
 * Folders group conversations in the sidebar. Each folder has an editable
 * name and can be collapsed/expanded. Conversations can be dragged into
 * folders or moved via the conversation context menu.
 */

const Folders = {
  _list: [],   // [{ id, name, position }]

  get list() { return this._list; },

  /** Load folders from the server */
  async load() {
    this._list = await API.listFolders();
  },

  getById(id) {
    return this._list.find((f) => f.id === id) || null;
  },

  /** Add a newly-created folder and re-render */
  add(folder) {
    this._list.push(folder);
    this._list.sort((a, b) => a.position - b.position || a.name.localeCompare(b.name));
  },

  /** Update a folder in local cache */
  update(id, data) {
    const idx = this._list.findIndex((f) => f.id === id);
    if (idx !== -1) Object.assign(this._list[idx], data);
  },

  /** Remove a folder from local cache */
  remove(id) {
    this._list = this._list.filter((f) => f.id !== id);
  },

  // ── Create ────────────────────────────────────────────────────────────────

  async createFolder() {
    return new Promise((resolve) => {
      // Create a modal dialog for folder creation with Code Folder toggle
      const modalHtml = `
        <div class="modal fade" id="newFolderModal" tabindex="-1">
          <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content app-modal border-secondary">
              <div class="modal-header border-secondary">
                <h5 class="modal-title">New Folder</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
              </div>
              <div class="modal-body">
                <div class="mb-3">
                  <label class="form-label text-secondary">Folder name</label>
                  <input type="text" id="newFolderNameInput" class="form-control border-secondary" placeholder="Enter folder name" autocomplete="off" />
                </div>
                <div class="form-check form-switch mb-3">
                  <input class="form-check-input" type="checkbox" role="switch" id="newFolderCodeSwitch" />
                  <label class="form-check-label text-secondary" for="newFolderCodeSwitch">
                    Code Folder
                  </label>
                  <div class="form-text text-secondary">
                    When enabled, creates a project structure with Analysis, Test Planning, UX Design, Data Modeling, and Project Management conversations linked to a system directory.
                  </div>
                </div>
              </div>
              <div class="modal-footer border-secondary">
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                <button type="button" class="btn btn-primary" id="btnNewFolderConfirm">Create</button>
              </div>
            </div>
          </div>
        </div>
      `;

      // Remove any existing modal
      const existingModal = document.getElementById("newFolderModal");
      if (existingModal) existingModal.remove();

      // Add modal to body
      document.body.insertAdjacentHTML("beforeend", modalHtml);

      const modalEl = document.getElementById("newFolderModal");
      const modal = new bootstrap.Modal(modalEl);
      const nameInput = document.getElementById("newFolderNameInput");
      const codeSwitch = document.getElementById("newFolderCodeSwitch");
      const confirmBtn = document.getElementById("btnNewFolderConfirm");

      // Focus the name input when modal is shown
      modalEl.addEventListener("shown.bs.modal", () => {
        nameInput.focus();
      });

      // Clean up modal when hidden
      modalEl.addEventListener("hidden.bs.modal", () => {
        modalEl.remove();
        resolve();
      });

      // Handle confirm button
      confirmBtn.addEventListener("click", async () => {
        const name = nameInput.value.trim();
        const codeFolder = codeSwitch.checked;

        if (!name) {
          nameInput.focus();
          return;
        }

        modal.hide();

        try {
          const folder = await API.createFolder(name, codeFolder);
          this.add(folder);
          Conversations._render();
        } catch (err) {
          alert("Failed to create folder: " + err.message);
        }

        resolve();
      });

      // Handle Enter key in name input
      nameInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          confirmBtn.click();
        }
      });

      modal.show();
    });
  },

  // ── Rename (inline) ───────────────────────────────────────────────────────

  startRename(id) {
    const titleEl = document.querySelector(`.folder-item[data-folder-id="${id}"] .folder-title`);
    if (!titleEl) return;

    const folder = this.getById(id);
    if (!folder) return;

    const input = document.createElement("input");
    input.type = "text";
    input.value = folder.name;
    input.className = "folder-rename-input form-control form-control-sm border-secondary p-0 px-1";
    input.style.cssText = "height:1.4rem;font-size:0.875rem;display:inline-block;width:auto;min-width:80px;max-width:160px;";

    titleEl.replaceWith(input);
    input.focus();
    input.select();

    const commit = async () => {
      const newName = input.value.trim();
      if (!newName) { this._cancelRename(id, folder.name); return; }
      try {
        await API.updateFolder(id, { name: newName });
        this.update(id, { name: newName });
      } catch (err) {
        alert("Failed to rename folder: " + err.message);
      }
      Conversations._render();
    };

    input.addEventListener("blur", commit);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter")  { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { input.removeEventListener("blur", commit); this._cancelRename(id, folder.name); }
    });
  },

  _cancelRename(id, originalName) {
    Conversations._render();
  },

  // ── Delete ────────────────────────────────────────────────────────────────

  async promptDelete(id) {
    const folder = this.getById(id);
    if (!folder) return;
    const msg = `Delete folder "${folder.name}"?\n\nConversations inside it will be kept but removed from this folder.`;
    if (!confirm(msg)) return;
    try {
      await API.deleteFolder(id);
      this.remove(id);
      // Clear folder_id on any conversation that referenced this folder
      Conversations._list.forEach((c) => {
        if (c.folder_id === id) c.folder_id = null;
      });
      Conversations._render();
    } catch (err) {
      alert("Failed to delete folder: " + err.message);
    }
  },

  // ── Collapse state ────────────────────────────────────────────────────────

  _collapsed: new Set(),   // set of folder ids that are collapsed

  isCollapsed(id) { return this._collapsed.has(id); },

  toggle(id) {
    if (this._collapsed.has(id)) {
      this._collapsed.delete(id);
    } else {
      this._collapsed.add(id);
    }
    Conversations._render();
  },

  // ── Export ────────────────────────────────────────────────────────────────

  /** Trigger a ZIP download for the given folder. */
  exportFolder(id) {
    API.exportFolder(id);
  },

  // ── Import ────────────────────────────────────────────────────────────────

  /**
   * Open a hidden file input, let the user pick a previously-exported ZIP,
   * then POST it to the server and reload the sidebar.
   */
  promptImport() {
    // Re-use or create a hidden file input
    let inp = document.getElementById("_folderImportInput");
    if (!inp) {
      inp = document.createElement("input");
      inp.id = "_folderImportInput";
      inp.type = "file";
      inp.accept = ".zip";
      inp.style.display = "none";
      document.body.appendChild(inp);
    }
    // Remove previous listener to avoid duplicate firings
    const fresh = inp.cloneNode();
    inp.replaceWith(fresh);
    fresh.id = "_folderImportInput";
    fresh.addEventListener("change", async () => {
      const file = fresh.files[0];
      if (!file) return;
      try {
        const result = await API.importFolder(file);
        // Reload folders and conversations from the server
        await Folders.load();
        await Conversations.load();
        // Expand the newly imported folder
        Folders._collapsed.delete(result.folder_id);
        Conversations._render();
        const convCount  = result.conversations_imported;
        const fileCount  = result.files_imported;
        alert(
          `Folder "${result.folder_name}" imported successfully.\n` +
          `${convCount} conversation${convCount !== 1 ? "s" : ""} · ` +
          `${fileCount} uploaded file${fileCount !== 1 ? "s" : ""} restored.`
        );
      } catch (err) {
        alert("Import failed: " + err.message);
      }
    });
    fresh.click();
  },
};
