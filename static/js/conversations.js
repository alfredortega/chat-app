/**
 * conversations.js — sidebar conversation list management
 *
 * Drag-and-drop:
 *   • Dragging a conv-item sets dataTransfer with the conversation id.
 *   • Folder headers and the "Unsorted" drop zone are valid drop targets.
 *   • Dropping onto a folder header moves the conversation into that folder.
 *   • Dropping onto the unsorted zone removes the conversation from any folder.
 */

const Conversations = {
  _list: [],        // cached conversation array
  _activeId: null,  // currently selected conversation id

  get activeId() { return this._activeId; },

  /** Load and render the full conversation list in the sidebar */
  async load() {
    this._list = await API.listConversations();
    this._render();
  },

  /** Prepend a newly-created conversation to the list and select it */
  add(conv) {
    this._list.unshift(conv);
    this._render();
    this.setActive(conv.id);
  },

  /** Update metadata (title / model) of a conversation in the cached list */
  update(id, data) {
    const idx = this._list.findIndex((c) => c.id === id);
    if (idx !== -1) Object.assign(this._list[idx], data);
    this._render();
    if (this._activeId === id) {
      this.setActive(id); // re-highlight
    }
  },

  /** Remove a conversation from the list */
  remove(id) {
    this._list = this._list.filter((c) => c.id !== id);
    if (this._activeId === id) this._activeId = null;
    this._render();
  },

  /** Highlight the active conversation item */
  setActive(id) {
    this._activeId = id;
    document.querySelectorAll(".conv-item").forEach((el) => {
      el.classList.toggle("active", Number(el.dataset.id) === id);
    });
  },

  /** Move a conversation to the top (after a new message) */
  bumpToTop(id) {
    const idx = this._list.findIndex((c) => c.id === id);
    if (idx > 0) {
      const [conv] = this._list.splice(idx, 1);
      this._list.unshift(conv);
      this._render();
      this.setActive(id);
    }
  },

  getById(id) {
    return this._list.find((c) => c.id === id) || null;
  },

  /** Filter the sidebar list by search results */
  _renderFiltered(results, query) {
    if (!results) {
      // Clear filter — restore full list
      this._render();
      return;
    }
    const container = document.getElementById("convList");
    container.innerHTML = "";
    if (results.length === 0) {
      container.innerHTML = `<p class="text-secondary text-center small mt-3 px-2">No results for "${_esc(query)}"</p>`;
      return;
    }
    results.forEach((r) => {
      const item = document.createElement("div");
      item.className = "conv-item";
      item.dataset.id = r.id;
      if (r.id === this._activeId) item.classList.add("active");
      item.innerHTML = `
        <i class="bi bi-chat text-secondary flex-shrink-0" style="font-size:0.85rem"></i>
        <div class="conv-title d-flex flex-column" style="min-width:0">
          <span class="text-truncate" title="${_esc(r.title)}">${_esc(r.title)}</span>
          <span class="text-secondary" style="font-size:0.72rem;white-space:normal;line-height:1.3">${_esc(r.snippet || "")}</span>
        </div>`;
      item.addEventListener("click", () => App.selectConversation(r.id));
      container.appendChild(item);
    });
  },

  _render() {
    const container = document.getElementById("convList");
    container.innerHTML = "";

    const folders = (typeof Folders !== "undefined") ? Folders.list : [];
    const allConvs = this._list;

    if (allConvs.length === 0 && folders.length === 0) {
      container.innerHTML =
        '<p class="text-secondary text-center small mt-3 px-2">No conversations yet</p>';
      return;
    }

    // ── "New Folder" button at the top ──
    const newFolderBtn = document.createElement("div");
    newFolderBtn.className = "px-2 pt-1 pb-1 d-flex gap-1";
    newFolderBtn.innerHTML = `
      <button class="btn btn-sm btn-outline-secondary flex-grow-1 d-flex align-items-center gap-1" id="btnNewFolder" style="font-size:0.78rem">
        <i class="bi bi-folder-plus"></i> New Folder
      </button>
      <button class="btn btn-sm btn-outline-secondary" id="btnImportFolder" title="Import folder from ZIP">
        <i class="bi bi-box-arrow-in-down"></i>
      </button>`;
    newFolderBtn.querySelector("#btnNewFolder").addEventListener("click", () => Folders.createFolder());
    newFolderBtn.querySelector("#btnImportFolder").addEventListener("click", () => Folders.promptImport());
    container.appendChild(newFolderBtn);

    // ── Render each folder ──
    folders.forEach((folder) => {
      const folderConvs = allConvs.filter((c) => c.folder_id === folder.id);
      const collapsed   = Folders.isCollapsed(folder.id);

      const folderEl = document.createElement("div");
      folderEl.className = "folder-item";
      folderEl.dataset.folderId = folder.id;

      folderEl.innerHTML = `
        <div class="folder-header d-flex align-items-center gap-1 px-2 py-1" style="cursor:pointer">
          <i class="bi bi-chevron-${collapsed ? "right" : "down"} text-secondary flex-shrink-0" style="font-size:0.7rem"></i>
          <i class="bi bi-folder${collapsed ? "" : "-open"} text-warning flex-shrink-0" style="font-size:0.85rem"></i>
          <span class="folder-title flex-grow-1 text-truncate small fw-semibold" title="${_esc(folder.name)}">${_esc(folder.name)}</span>
          <span class="folder-count text-secondary" style="font-size:0.7rem">${folderConvs.length}</span>
          <span class="folder-actions d-none gap-1">
            <button class="btn-folder-new-chat" title="New chat in this folder"><i class="bi bi-plus-lg"></i></button>
            <button class="btn-folder-rename" title="Rename folder"><i class="bi bi-pencil"></i></button>
            <button class="btn-folder-export" title="Export folder as ZIP"><i class="bi bi-box-arrow-up"></i></button>
            <button class="btn-folder-delete text-danger" title="Delete folder"><i class="bi bi-trash"></i></button>
          </span>
        </div>
        <div class="folder-children ${collapsed ? "d-none" : ""}"></div>
      `;

      // Toggle collapse
      const header = folderEl.querySelector(".folder-header");
      header.addEventListener("click", (e) => {
        if (e.target.closest(".folder-actions")) return;
        Folders.toggle(folder.id);
      });

      // Drag-and-drop: drop onto the folder header to move a conversation in
      header.addEventListener("dragover",  (e) => this._onDragOver(e));
      header.addEventListener("dragleave", (e) => this._onDragLeave(e));
      header.addEventListener("drop",      (e) => this._onDrop(e, folder.id));

      // Show/hide actions on hover
      folderEl.addEventListener("mouseenter", () => {
        folderEl.querySelector(".folder-actions").classList.remove("d-none");
        folderEl.querySelector(".folder-actions").classList.add("d-flex");
      });
      folderEl.addEventListener("mouseleave", () => {
        folderEl.querySelector(".folder-actions").classList.add("d-none");
        folderEl.querySelector(".folder-actions").classList.remove("d-flex");
      });

      folderEl.querySelector(".btn-folder-new-chat").addEventListener("click", (e) => {
        e.stopPropagation();
        App.newConversation(folder.id);
      });
      folderEl.querySelector(".btn-folder-rename").addEventListener("click", (e) => {
        e.stopPropagation();
        Folders.startRename(folder.id);
      });
      folderEl.querySelector(".btn-folder-export").addEventListener("click", (e) => {
        e.stopPropagation();
        API.exportFolder(folder.id);
      });
      folderEl.querySelector(".btn-folder-delete").addEventListener("click", (e) => {
        e.stopPropagation();
        Folders.promptDelete(folder.id);
      });

      // Render conversations inside the folder
      const childrenEl = folderEl.querySelector(".folder-children");
      folderConvs.forEach((conv) => {
        childrenEl.appendChild(this._makeConvItem(conv, true));
      });

      container.appendChild(folderEl);
    });

    // ── Render un-grouped conversations ──
    const ungrouped = allConvs.filter((c) => !c.folder_id);

    // Drop zone for "Unsorted" section — dropping here removes from folder
    if (folders.length > 0) {
      const sep = document.createElement("div");
      sep.className = "px-2 pt-2 pb-0 unsorted-drop-zone";
      sep.innerHTML = `<span class="text-secondary" style="font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em">Unsorted</span>`;
      sep.addEventListener("dragover",  (e) => this._onDragOver(e));
      sep.addEventListener("dragleave", (e) => this._onDragLeave(e));
      sep.addEventListener("drop",      (e) => this._onDrop(e, null));
      container.appendChild(sep);
    }
    ungrouped.forEach((conv) => {
      container.appendChild(this._makeConvItem(conv, false));
    });
  },

  // ── Drag-and-drop helpers ─────────────────────────────────────────────────

  /** Called when a drag starts on a conv-item */
  _onDragStart(e, convId) {
    e.dataTransfer.setData("text/plain", String(convId));
    e.dataTransfer.effectAllowed = "move";
    e.currentTarget.classList.add("conv-item-dragging");
  },

  _onDragEnd(e) {
    e.currentTarget.classList.remove("conv-item-dragging");
  },

  _onDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    e.currentTarget.classList.add("drop-target-active");
  },

  _onDragLeave(e) {
    e.currentTarget.classList.remove("drop-target-active");
  },

  /** Drop onto a folder header or the unsorted zone */
  async _onDrop(e, folderId) {
    e.preventDefault();
    e.currentTarget.classList.remove("drop-target-active");
    const convId = parseInt(e.dataTransfer.getData("text/plain"));
    if (!convId) return;
    const conv = this._list.find((c) => c.id === convId);
    if (!conv) return;
    // Nothing to do if already in the right folder
    const targetFolderId = folderId || null;
    if (conv.folder_id === targetFolderId) return;
    try {
      await API.updateConversation(convId, { folder_id: folderId || 0 });
      conv.folder_id = targetFolderId;
      // Auto-expand the destination folder
      if (targetFolderId && typeof Folders !== "undefined") {
        Folders._collapsed.delete(targetFolderId);
      }
      this._render();
      this.setActive(this._activeId);
    } catch (err) {
      alert("Failed to move conversation: " + err.message);
    }
  },

  /** Build and return a single conversation list item element */
  _makeConvItem(conv, indented) {
    const item = document.createElement("div");
    item.className = "conv-item" + (indented ? " conv-item-indented" : "");
    item.dataset.id = conv.id;
    item.draggable = true;
    if (conv.id === this._activeId) item.classList.add("active");

    // Drag-and-drop events
    item.addEventListener("dragstart", (e) => this._onDragStart(e, conv.id));
    item.addEventListener("dragend",   (e) => this._onDragEnd(e));

    // Build folder move options for the dropdown
    const folders = (typeof Folders !== "undefined") ? Folders.list : [];
    const folderOptions = folders.map((f) =>
      `<li><a class="dropdown-item small" href="#" data-move-folder="${f.id}">${_esc(f.name)}</a></li>`
    ).join("");
    const removeFromFolder = conv.folder_id
      ? `<li><a class="dropdown-item small text-secondary" href="#" data-move-folder="0"><i class="bi bi-x me-1"></i>Remove from folder</a></li><li><hr class="dropdown-divider"></li>`
      : "";
    const folderMenuSection = folders.length
      ? `<li><hr class="dropdown-divider"></li>
         <li><span class="dropdown-header" style="font-size:0.72rem">Move to folder</span></li>
         ${removeFromFolder}
         ${folderOptions}`
      : "";

    item.innerHTML = `
      <i class="bi bi-chat text-secondary flex-shrink-0" style="font-size:0.85rem"></i>
      <span class="conv-title" title="${_esc(conv.title)}">${_esc(conv.title)}</span>
      <span class="conv-actions">
        <button class="btn-rename" title="Rename"><i class="bi bi-pencil"></i></button>
        <div class="dropdown d-inline">
          <button class="btn-more" title="More options" data-bs-toggle="dropdown" aria-expanded="false"><i class="bi bi-three-dots-vertical"></i></button>
          <ul class="dropdown-menu dropdown-menu-end shadow border-secondary" style="min-width:160px;font-size:0.85rem">
            <li><a class="dropdown-item small" href="#" data-action="rename"><i class="bi bi-pencil me-2"></i>Rename</a></li>
            <li><a class="dropdown-item small text-danger" href="#" data-action="delete"><i class="bi bi-trash me-2"></i>Delete</a></li>
            ${folderMenuSection}
          </ul>
        </div>
      </span>
    `;

    // Select conversation on click
    item.addEventListener("click", (e) => {
      if (e.target.closest(".conv-actions")) return;
      App.selectConversation(conv.id);
    });

    item.querySelector(".btn-rename").addEventListener("click", (e) => {
      e.stopPropagation();
      App.promptRename(conv.id);
    });

    // Dropdown actions
    item.querySelectorAll("[data-action]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const action = el.dataset.action;
        if (action === "rename") App.promptRename(conv.id);
        if (action === "delete") App.promptDelete(conv.id);
      });
    });

    // Move-to-folder actions
    item.querySelectorAll("[data-move-folder]").forEach((el) => {
      el.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const folderId = parseInt(el.dataset.moveFolder) || 0;
        try {
          await API.updateConversation(conv.id, { folder_id: folderId });
          const idx = this._list.findIndex((c) => c.id === conv.id);
          if (idx !== -1) this._list[idx].folder_id = folderId || null;
          this._render();
          this.setActive(this._activeId);
        } catch (err) {
          alert("Failed to move conversation: " + err.message);
        }
      });
    });

    return item;
  },
};

function _esc(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
