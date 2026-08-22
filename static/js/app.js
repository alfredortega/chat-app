/**
 * app.js — main application logic and state management
 */

const App = {
  models: [],
  personas: [],
  endpoints: [],        // [{ id, name, base_url, api_key_set, is_default }]
  defaultModel: "",
  contextWindow: 128000,
  outputDir: "",        // app-level default (from settings)
  convOutputDir: "",    // per-conversation override (empty = use app default)
  browserRoot: "",
  convEnableTools: true, // per-conversation: whether tools/function-calling are offered
  convEndpointId: null,  // per-conversation endpoint override (null = use default)
  activeConvId: null,
  isStreaming: false,
  theme: "dark",   // "dark" | "light"
  _abortController: null,   // AbortController for the active stream

  // ── Bootstrap modal instances ──────────────────────────────────────────────
  renameModal: null,
  deleteModal: null,
  settingsModal: null,
  personaModal: null,
  deletePersonaModal: null,
  outputDirModal: null,

  async init() {
    // DOM refs
    this.modelSelect       = document.getElementById("modelSelect");
    this.btnNewChat        = document.getElementById("btnNewChat");
    this.btnSend           = document.getElementById("btnSend");
    this.btnStop           = document.getElementById("btnStop");
    this.btnSettings       = document.getElementById("btnSettings");
    this.btnHelp           = document.getElementById("btnHelp");
    this.messageInput      = document.getElementById("messageInput");
    this.streamStatus      = document.getElementById("streamStatus");
    this.chatTitleDisplay  = document.getElementById("chatTitleDisplay");
    this.btnRenameConv     = document.getElementById("btnRenameConv");
    this.btnExportConv     = document.getElementById("btnExportConv");
    this.btnDeleteConv     = document.getElementById("btnDeleteConv");
    this.btnToggleTools    = document.getElementById("btnToggleTools");
    this.endpointSelect    = document.getElementById("endpointSelect");
    this.emptyState        = document.getElementById("emptyState");
    this.chatPanel         = document.getElementById("chatPanel");
    this.messagesArea      = document.getElementById("messagesArea");

    // Bootstrap modals
    this.renameModal        = new bootstrap.Modal(document.getElementById("renameModal"));
    this.deleteModal        = new bootstrap.Modal(document.getElementById("deleteModal"));
    this.settingsModal      = new bootstrap.Modal(document.getElementById("settingsModal"));
    this.personaModal       = new bootstrap.Modal(document.getElementById("personaModal"));
    this.deletePersonaModal = new bootstrap.Modal(document.getElementById("deletePersonaModal"));
    this.outputDirModal     = new bootstrap.Modal(document.getElementById("outputDirModal"));
    this.helpModal          = new bootstrap.Modal(document.getElementById("helpModal"));

    // Init sub-modules
    Chat.init(this.messagesArea);
    SaveFiles.init();
    FolderBrowser.init();

    // Apply saved theme early
    this._applyTheme(localStorage.getItem("theme") || "dark");

    // Theme toggle
    this.btnThemeToggle = document.getElementById("btnThemeToggle");
    this.btnThemeToggle.addEventListener("click", () => this._toggleTheme());

    // Bind events
    this.btnNewChat.addEventListener("click",   () => this.newConversation());
    this.btnSend.addEventListener("click",      () => this.sendMessage());
    this.btnStop.addEventListener("click",      () => this.stopStreaming());
    this.btnSettings.addEventListener("click",  () => this.openSettings());
    this.btnHelp.addEventListener("click",      () => this.openHelp());
    this.btnRenameConv.addEventListener("click",() => this.promptRename(this.activeConvId));
    this.btnExportConv.addEventListener("click",() => this.exportConversation());
    this.btnDeleteConv.addEventListener("click",() => this.promptDelete(this.activeConvId));
    this.btnToggleTools.addEventListener("click",() => this.toggleTools());

    // File upload wiring
    this.btnUpload    = document.getElementById("btnUpload");
    this.fileInput    = document.getElementById("fileInput");
    this.btnUploadDir = document.getElementById("btnUploadDir");
    this.dirInput     = document.getElementById("dirInput");

    this.btnUpload.addEventListener("click", () => this.fileInput.click());
    this.fileInput.addEventListener("change", async () => {
      if (this.fileInput.files.length) {
        await Files.uploadFiles(Array.from(this.fileInput.files));
        this.fileInput.value = "";
      }
    });

    this.btnUploadDir.addEventListener("click", () => this.dirInput.click());
    this.dirInput.addEventListener("change", async () => {
      if (this.dirInput.files.length) {
        await Files.uploadFiles(Array.from(this.dirInput.files));
        this.dirInput.value = "";
      }
    });

    // Link folder button — opens the server-side folder browser
    document.getElementById("btnLinkFolder")
      .addEventListener("click", () => {
        if (this.activeConvId) FolderBrowser.open(this.activeConvId);
      });

    // Import markdown as context
    this.mdContextInput = document.getElementById("mdContextInput");
    document.getElementById("btnImportMd")
      .addEventListener("click", () => this.mdContextInput.click());
    this.mdContextInput.addEventListener("change", () => this._importMarkdownContext());

    // Output dir button — opens the per-conversation output dir modal
    this.btnOutputDir = document.getElementById("btnOutputDir");
    this.btnOutputDir.addEventListener("click", () => this._openOutputDirModal());
    document.getElementById("btnOpenOutputDir")
      .addEventListener("click", () => this._openOutputDirInFileManager());

    // Output dir modal wiring
    document.getElementById("btnOutputDirBrowse")
      .addEventListener("click", () => this._outputDirBrowserNavigate(App.browserRoot || ""));
    document.getElementById("btnOutputDirBrowserUp")
      .addEventListener("click", () => this._outputDirBrowserUp());
    document.getElementById("btnOutputDirBrowserClose")
      .addEventListener("click", () => document.getElementById("outputDirBrowserPane").classList.add("d-none"));
    document.getElementById("btnOutputDirSelectCurrent")
      .addEventListener("click", () => this._outputDirBrowserSelectCurrent());
    document.getElementById("btnOutputDirModalSave")
      .addEventListener("click", () => this._saveOutputDirModal());

    // Settings: Folder Browser Starting Path inline browser wiring
    document.getElementById("btnSettingsBrowserRootBrowse")
      .addEventListener("click", () => this._settingsFolderBrowse("browserRoot", document.getElementById("settingsBrowserRoot").value.trim() || App.browserRoot || ""));
    document.getElementById("btnSettingsBrowserRootUp")
      .addEventListener("click", () => this._settingsFolderUp("browserRoot"));
    document.getElementById("btnSettingsBrowserRootClose")
      .addEventListener("click", () => document.getElementById("settingsBrowserRootPane").classList.add("d-none"));
    document.getElementById("btnSettingsBrowserRootSelectCurrent")
      .addEventListener("click", () => this._settingsFolderSelectCurrent("browserRoot"));

    // Settings: Default Output Folder inline browser wiring
    document.getElementById("btnSettingsOutputDirBrowse")
      .addEventListener("click", () => this._settingsFolderBrowse("outputDir", document.getElementById("settingsOutputDir").value.trim() || App.outputDir || App.browserRoot || ""));
    document.getElementById("btnSettingsOutputDirUp")
      .addEventListener("click", () => this._settingsFolderUp("outputDir"));
    document.getElementById("btnSettingsOutputDirClose")
      .addEventListener("click", () => document.getElementById("settingsOutputDirPane").classList.add("d-none"));
    document.getElementById("btnSettingsOutputDirSelectCurrent")
      .addEventListener("click", () => this._settingsFolderSelectCurrent("outputDir"));

    // Search
    this.searchInput   = document.getElementById("searchInput");
    this.btnSearchClear = document.getElementById("btnSearchClear");
    this.searchInput.addEventListener("input", () => this._onSearch());
    this.btnSearchClear.addEventListener("click", () => {
      this.searchInput.value = "";
      this._onSearch();
    });

    // Token count badge
    this.tokenCountBadge = document.getElementById("tokenCountBadge");

    // File preview modal
    this.filePreviewModal = new bootstrap.Modal(document.getElementById("filePreviewModal"));

    this.messageInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });

    // Model selector in navbar changes the active conversation's model
    this.modelSelect.addEventListener("change", () => this.onModelSelectChange());

    // Rename modal confirm
    document.getElementById("btnRenameConfirm").addEventListener("click", () => this.confirmRename());
    document.getElementById("renameInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter") this.confirmRename();
    });

    // Delete modal confirm
    document.getElementById("btnDeleteConfirm").addEventListener("click", () => this.confirmDelete());

    // Settings modal save
    document.getElementById("btnSettingsSave").addEventListener("click", () => this.saveSettings());

    // Persona modal save
    document.getElementById("btnNewPersona").addEventListener("click", () => this.openPersonaModal(null));
    document.getElementById("btnPersonaSave").addEventListener("click", () => this.savePersona());
    document.getElementById("btnDeletePersonaConfirm").addEventListener("click", () => this.confirmDeletePersona());

    // Purge button
    document.getElementById("btnPurge").addEventListener("click", () => this.purgeAll());

    // Persona selector in chat header
    this.personaSelect = document.getElementById("personaSelect");
    this.personaSelect.addEventListener("change", () => this.onPersonaSelectChange());

    // Endpoint selector in chat header
    this.endpointSelect.addEventListener("change", () => this.onEndpointSelectChange());

    // Endpoint management (Settings)
    document.getElementById("btnNewEndpoint").addEventListener("click", () => this.openEndpointModal(null));
    document.getElementById("btnEndpointSave").addEventListener("click", () => this.saveEndpoint());
    document.getElementById("btnDeleteEndpointConfirm").addEventListener("click", () => this.confirmDeleteEndpoint());
    document.getElementById("btnToggleEndpointKey").addEventListener("click", () => {
      const input = document.getElementById("endpointKeyInput");
      const btn   = document.getElementById("btnToggleEndpointKey");
      if (input.type === "password") {
        input.type = "text";
        btn.innerHTML = '<i class="bi bi-eye-slash"></i>';
      } else {
        input.type = "password";
        btn.innerHTML = '<i class="bi bi-eye"></i>';
      }
    });

    // Endpoint modal instances
    this.endpointModal       = new bootstrap.Modal(document.getElementById("endpointModal"));
    this.deleteEndpointModal = new bootstrap.Modal(document.getElementById("deleteEndpointModal"));

    // Reload the endpoint modal's model list when the URL or key changes.
    const reloadEpModels = () => this._reloadEndpointModelsFromInputs();
    document.getElementById("endpointUrlInput").addEventListener("blur", reloadEpModels);
    document.getElementById("endpointKeyInput").addEventListener("blur", reloadEpModels);

    // Load the optional model probe separately so an unavailable provider does
    // not prevent database-backed data from rendering.
    try {
      API.getModels()
        .then((data) => {
          this.models = data.models || [];
          this._populateModelSelects();
        })
        .catch((err) => {
          console.warn("Failed to load models:", err);
        });
      const [settingsData, personasData, endpointsData] = await Promise.all([
        API.getSettings(),
        API.listPersonas(),
        API.listEndpoints(),
      ]);
      this.personas = personasData        || [];
      this.endpoints = endpointsData      || [];
      this.defaultModel = settingsData.default_model || "";
      this.contextWindow = Number(settingsData.context_window) || 128000;
      this.outputDir    = settingsData.output_dir    || "";
      this.browserRoot  = settingsData.browser_root  || "";
      this._populateModelSelects();
      this._populatePersonaSelects();
      this._populateEndpointSelects();
      this._renderOutputDirBtn();    } catch (err) {
      console.error("Failed to load models/settings/personas/endpoints:", err);
      this.modelSelect.innerHTML = '<option value="">Failed to load models</option>';
    }

    // Load folders then conversations (folders must exist before sidebar renders)
    await Folders.load();
    await Conversations.load();

    // Select the first conversation automatically if one exists
    const firstConv = document.querySelector(".conv-item");
    if (firstConv) {
      this.selectConversation(Number(firstConv.dataset.id));
    }

    // If any of the core configuration values are missing, prompt the user
    // to fill them in by opening the Settings page automatically.
    this._promptSettingsIfIncomplete();
  },

  /**
   * Open the Settings modal on startup when the default model, fallback
   * default model, folder browser starting path or default output folder
   * have not yet been configured.
   */
  _promptSettingsIfIncomplete() {
    // The "fallback default model" is any endpoint-level default model.
    const anyEndpointDefault = (this.endpoints || []).some(
      (e) => e && (e.default_model || "").trim()
    );
    const missing =
      !(this.defaultModel || "").trim() ||
      !anyEndpointDefault ||
      !(this.browserRoot || "").trim() ||
      !(this.outputDir || "").trim();

    if (missing) {
      this.openSettings();
    }
  },

  // ── Models ─────────────────────────────────────────────────────────────────

  _populateModelSelects() {
    // Navbar model selector
    const opts = this.models.map(
      (m) => `<option value="${m}">${m}</option>`
    ).join("");
    this.modelSelect.innerHTML = opts || '<option value="">No models found</option>';

    // Apply the default model to the navbar selector immediately
    if (this.defaultModel && this.modelSelect.querySelector(`option[value="${this.defaultModel}"]`)) {
      this.modelSelect.value = this.defaultModel;
    } else if (this.models.length > 0) {
      this.modelSelect.selectedIndex = 0;
    }

    // Settings default model selector
    const defSel = document.getElementById("defaultModelSelect");
    defSel.innerHTML =
      '<option value="">— Select a default model —</option>' + opts;
    defSel.value = this.defaultModel;
  },

  /** Called when the user changes the model selector in the navbar */
  async onModelSelectChange() {
    if (!this.activeConvId) return;
    const newModel = this.modelSelect.value;
    try {
      await API.updateConversation(this.activeConvId, { model_id: newModel });
      Conversations.update(this.activeConvId, { model_id: newModel });
    } catch (err) {
      console.error("Failed to update conversation model:", err);
    }
  },

  /** Sync the navbar model selector to the active conversation's model */
  _syncModelSelect(modelId) {
    const val = modelId || this.defaultModel;
    if (val && this.modelSelect.querySelector(`option[value="${val}"]`)) {
      this.modelSelect.value = val;
    } else if (this.models.length > 0) {
      this.modelSelect.selectedIndex = 0;
    }
  },

  // ── Personas ────────────────────────────────────────────────────────────────

  /** Populate both the chat-header persona dropdown and the settings list */
  _populatePersonaSelects() {
    // Chat-header dropdown
    const baseOpt = '<option value="">— No Persona —</option>';
    const opts = this.personas.map(
      (p) => `<option value="${p.id}">${_escAttr(p.name)}</option>`
    ).join("");
    this.personaSelect.innerHTML = baseOpt + opts;

    // Settings panel list
    this._renderPersonaList();
  },

  /** Render the persona management list inside the Settings modal */
  _renderPersonaList() {
    const container = document.getElementById("personaList");
    if (!container) return;
    if (this.personas.length === 0) {
      container.innerHTML = '<p class="text-secondary small mb-0">No personas yet. Click "New Persona" to add one.</p>';
      return;
    }
    container.innerHTML = this.personas.map((p) => `
      <div class="persona-row d-flex align-items-center gap-2 p-2 rounded border border-secondary">
        <i class="bi bi-person-badge text-primary flex-shrink-0"></i>
        <span class="flex-grow-1 small fw-semibold">${_escAttr(p.name)}</span>
        <button class="btn btn-sm btn-outline-secondary py-0 px-2" onclick="App.openPersonaModal(${p.id})" title="Edit">
          <i class="bi bi-pencil"></i>
        </button>
        <button class="btn btn-sm btn-outline-danger py-0 px-2" onclick="App.promptDeletePersona(${p.id})" title="Delete">
          <i class="bi bi-trash"></i>
        </button>
      </div>
    `).join("");
  },

  /** Sync the chat-header persona dropdown to the active conversation */
  _syncPersonaSelect(personaId) {
    this.personaSelect.value = personaId ? String(personaId) : "";
  },

  /** Called when the user changes the persona selector in the chat header */
  async onPersonaSelectChange() {
    if (!this.activeConvId) return;
    const raw = this.personaSelect.value;
    const personaId = raw ? parseInt(raw) : null;
    try {
      await API.updateConversation(this.activeConvId, { persona_id: personaId ?? 0 });
      Conversations.update(this.activeConvId, { persona_id: personaId });
    } catch (err) {
      console.error("Failed to update conversation persona:", err);
    }
  },

  // ── Tools toggle (per conversation) ─────────────────────────────────────────

  /** Toggle function-calling tools for the active conversation */
  async toggleTools() {
    if (!this.activeConvId) return;
    this.convEnableTools = !this.convEnableTools;
    this._renderToolsBtn();
    try {
      await API.updateConversation(this.activeConvId, { enable_tools: this.convEnableTools });
      Conversations.update(this.activeConvId, { enable_tools: this.convEnableTools ? 1 : 0 });
    } catch (err) {
      console.error("Failed to update conversation tools setting:", err);
      // Revert on failure
      this.convEnableTools = !this.convEnableTools;
      this._renderToolsBtn();
    }
  },

  /** Reflect the current per-conversation tools state on the toolbar button */
  _renderToolsBtn() {
    const btn = this.btnToggleTools;
    if (!btn) return;
    if (this.convEnableTools) {
      btn.innerHTML = '<i class="bi bi-tools"></i>';
      btn.classList.remove("btn-outline-secondary");
      btn.classList.add("btn-outline-primary");
      btn.title = "Tools ENABLED (function calling). Click to disable for local models that don't support it.";
    } else {
      btn.innerHTML = '<i class="bi bi-tools"></i>';
      btn.classList.remove("btn-outline-primary");
      btn.classList.add("btn-outline-secondary");
      btn.title = "Tools DISABLED. Click to enable file/Python function calling.";
    }
  },

  // ── Endpoints ───────────────────────────────────────────────────────────────

  /** Populate the chat-header endpoint dropdown and the settings list */
  _populateEndpointSelects() {
    // Chat-header dropdown
    const defaultEp = this.endpoints.find((e) => e.is_default);
    const defaultLabel = defaultEp ? `— Default (${defaultEp.name}) —` : "— Default —";
    const baseOpt = `<option value="">${_escAttr(defaultLabel)}</option>`;
    const opts = this.endpoints.map(
      (e) => `<option value="${e.id}">${_escAttr(e.name)}</option>`
    ).join("");
    if (this.endpointSelect) {
      this.endpointSelect.innerHTML = baseOpt + opts;
      // Restore current conversation selection if any
      this.endpointSelect.value = this.convEndpointId ? String(this.convEndpointId) : "";
    }

    // Settings panel list
    this._renderEndpointList();
  },

  /** Render the endpoint management list inside the Settings modal */
  _renderEndpointList() {
    const container = document.getElementById("endpointList");
    if (!container) return;
    if (this.endpoints.length === 0) {
      container.innerHTML = '<p class="text-secondary small mb-0">No endpoints yet. Click "New Endpoint" to add one.</p>';
      return;
    }
    container.innerHTML = this.endpoints.map((e) => `
      <div class="endpoint-row d-flex align-items-center gap-2 p-2 rounded border border-secondary">
        <i class="bi bi-hdd-network text-primary flex-shrink-0"></i>
        <div class="flex-grow-1 min-w-0">
          <div class="small fw-semibold text-truncate">
            ${_escAttr(e.name)}
            ${e.is_default ? '<span class="badge bg-primary ms-1" style="font-size:0.65rem">Default</span>' : ''}
            ${e.api_key_set ? '' : '<span class="badge bg-secondary ms-1" style="font-size:0.65rem">No key</span>'}
            ${e.default_model ? '' : '<span class="badge bg-warning text-dark ms-1" style="font-size:0.65rem">No default model</span>'}
          </div>
          <div class="text-secondary text-truncate" style="font-size:0.75rem">${_escAttr(e.base_url)}</div>
          ${e.default_model ? `<div class="text-secondary text-truncate" style="font-size:0.72rem"><i class="bi bi-cpu me-1"></i>${_escAttr(e.default_model)}</div>` : ''}
        </div>
        <button class="btn btn-sm btn-outline-secondary py-0 px-2" onclick="App.openEndpointModal(${e.id})" title="Edit">
          <i class="bi bi-pencil"></i>
        </button>
        <button class="btn btn-sm btn-outline-danger py-0 px-2" onclick="App.promptDeleteEndpoint(${e.id})" title="Delete">
          <i class="bi bi-trash"></i>
        </button>
      </div>
    `).join("");
  },

  /** Sync the chat-header endpoint dropdown to the active conversation */
  _syncEndpointSelect(endpointId) {
    this.convEndpointId = endpointId || null;
    if (this.endpointSelect) {
      this.endpointSelect.value = endpointId ? String(endpointId) : "";
    }
  },

  /** Called when the user changes the endpoint selector in the chat header */
  async onEndpointSelectChange() {
    if (!this.activeConvId) return;
    const raw = this.endpointSelect.value;
    const endpointId = raw ? parseInt(raw) : null;
    this.convEndpointId = endpointId;
    try {
      // endpoint_id=0 signals "clear override / use default" on the server
      await API.updateConversation(this.activeConvId, { endpoint_id: endpointId ?? 0 });
      Conversations.update(this.activeConvId, { endpoint_id: endpointId });
      // Refresh the model list for whichever endpoint is now in effect,
      // applying that endpoint's default model.
      await this._refreshModelsForCurrentEndpoint({ applyEndpointDefault: true });
    } catch (err) {
      console.error("Failed to update conversation endpoint:", err);
    }
  },

  /** Resolve the endpoint object currently in effect (override or app default). */
  _currentEndpoint() {
    if (this.convEndpointId) {
      return this.endpoints.find((e) => e.id === this.convEndpointId) || null;
    }
    return this.endpoints.find((e) => e.is_default) || null;
  },

  /** Reload the navbar model list using the active conversation's endpoint. */
  async _refreshModelsForCurrentEndpoint(opts = {}) {
    try {
      const data = await API.getModelsForEndpoint(this.convEndpointId || null);
      this.models = data.models || [];
      this._populateModelSelects();
      const conv = this.activeConvId ? Conversations.getById(this.activeConvId) : null;

      if (opts.applyEndpointDefault) {
        // Switching endpoints: adopt the new endpoint's default model.
        const ep = this._currentEndpoint();
        const epDefault = ep && ep.default_model ? ep.default_model : "";
        const target = epDefault && this.models.includes(epDefault)
          ? epDefault
          : (this.models[0] || "");
        this._syncModelSelect(target);
        if (this.activeConvId && target && (!conv || conv.model_id !== target)) {
          try {
            await API.updateConversation(this.activeConvId, { model_id: target });
            Conversations.update(this.activeConvId, { model_id: target });
          } catch (e) { console.error("Failed to set model for new endpoint:", e); }
        }
      } else if (conv) {
        // Keep the current conversation's model selected if still present
        this._syncModelSelect(conv.model_id);
      }
    } catch (err) {
      console.error("Failed to load models for endpoint:", err);
      this.modelSelect.innerHTML = '<option value="">Failed to load models</option>';
    }
  },

  // ── Endpoint CRUD ────────────────────────────────────────────────────────────

  _editingEndpointId: null,

  openEndpointModal(id) {
    this._editingEndpointId = id;
    const titleEl   = document.getElementById("endpointModalTitle");
    const nameEl    = document.getElementById("endpointNameInput");
    const urlEl     = document.getElementById("endpointUrlInput");
    const keyEl     = document.getElementById("endpointKeyInput");
    const keyStatus = document.getElementById("endpointKeyStatus");
    const defaultEl = document.getElementById("endpointDefaultInput");
    const modelSel  = document.getElementById("endpointModelSelect");

    keyEl.value = "";
    keyEl.type  = "password";
    document.getElementById("btnToggleEndpointKey").innerHTML = '<i class="bi bi-eye"></i>';

    // Reset the model dropdown; it's populated on demand once URL+key are known.
    this._endpointModelValue = "";
    modelSel.innerHTML = '<option value="">— Select a default model —</option>';

    if (id) {
      const ep = this.endpoints.find((e) => e.id === id);
      titleEl.textContent = "Edit Endpoint";
      nameEl.value    = ep ? ep.name : "";
      urlEl.value     = ep ? ep.base_url : "";
      defaultEl.checked = ep ? !!ep.is_default : false;
      this._endpointModelValue = ep ? (ep.default_model || "") : "";
      keyStatus.textContent = ep && ep.api_key_set
        ? "A key is currently set. Enter a new value to replace it, or leave blank to keep it."
        : "No API key is set for this endpoint.";
      // Try to load the model list for this endpoint so the current default shows.
      this._loadEndpointModels(id);
    } else {
      titleEl.textContent = "New Endpoint";
      nameEl.value    = "";
      urlEl.value     = "";
      defaultEl.checked = this.endpoints.length === 0; // first one defaults to default
      keyStatus.textContent = "";
      document.getElementById("endpointModelStatus").textContent =
        "Save the endpoint first (with a valid URL and key) to load its model list.";
    }
    this.endpointModal.show();
    setTimeout(() => nameEl.focus(), 300);
  },

  /** Load the available models for an existing endpoint into the modal's dropdown. */
  async _loadEndpointModels(endpointId) {
    const modelSel = document.getElementById("endpointModelSelect");
    const status   = document.getElementById("endpointModelStatus");
    status.textContent = "Loading models…";
    try {
      const data = await API.getModelsForEndpoint(endpointId);
      this._fillEndpointModelSelect(data.models || []);
    } catch (err) {
      modelSel.innerHTML = '<option value="">— Select a default model —</option>';
      status.textContent = "Could not load models: " + err.message;
    }
  },

  /** Reload the modal's model list from the URL/key currently typed in the form. */
  async _reloadEndpointModelsFromInputs() {
    const baseUrl = document.getElementById("endpointUrlInput").value.trim();
    const apiKey  = document.getElementById("endpointKeyInput").value.trim();
    const status  = document.getElementById("endpointModelStatus");
    if (!baseUrl) return;
    // Preserve the currently chosen value so we can restore it after reload.
    const modelSel = document.getElementById("endpointModelSelect");
    if (modelSel.value) this._endpointModelValue = modelSel.value;
    status.textContent = "Loading models…";
    try {
      let data;
      // If editing and no new key typed, use the saved endpoint so its stored key applies.
      if (this._editingEndpointId && !apiKey) {
        data = await API.getModelsForEndpoint(this._editingEndpointId);
      } else {
        data = await API.getModelsForUrl(baseUrl, apiKey);
      }
      this._fillEndpointModelSelect(data.models || []);
    } catch (err) {
      status.textContent = "Could not load models: " + err.message;
    }
  },

  /** Populate the endpoint modal's model dropdown from a list of model ids. */
  _fillEndpointModelSelect(models) {
    const modelSel = document.getElementById("endpointModelSelect");
    const status   = document.getElementById("endpointModelStatus");
    const opts = models.map((m) => `<option value="${_escAttr(m)}">${_escAttr(m)}</option>`).join("");
    modelSel.innerHTML = '<option value="">— Select a default model —</option>' + opts;
    if (this._endpointModelValue &&
        modelSel.querySelector(`option[value="${_escAttr(this._endpointModelValue)}"]`)) {
      modelSel.value = this._endpointModelValue;
    }
    status.textContent = models.length
      ? "Pick the model used by default for new conversations on this endpoint."
      : "No models were returned. Check the URL and API key.";
  },

  async saveEndpoint() {
    const name     = document.getElementById("endpointNameInput").value.trim();
    const baseUrl  = document.getElementById("endpointUrlInput").value.trim();
    const apiKey   = document.getElementById("endpointKeyInput").value.trim();
    const isDefault = document.getElementById("endpointDefaultInput").checked;
    const defaultModel = document.getElementById("endpointModelSelect").value;

    if (!name || !baseUrl) {
      alert("Both a name and an API base URL are required.");
      return;
    }

    // A default endpoint must have a default model chosen. For an existing
    // endpoint whose model list has loaded, enforce the selection. For a brand
    // new endpoint the list can't load until it's saved, so we allow it and
    // prompt for the model on the follow-up edit.
    const modelSel = document.getElementById("endpointModelSelect");
    const hasModelOptions = modelSel.options.length > 1;
    if (isDefault && hasModelOptions && !defaultModel) {
      alert("A default endpoint must have a default model selected. Please pick one.");
      return;
    }

    const payload = { name, base_url: baseUrl, is_default: isDefault };
    if (apiKey) payload.api_key = apiKey;
    if (hasModelOptions || defaultModel) payload.default_model = defaultModel;

    try {
      if (this._editingEndpointId) {
        await API.updateEndpoint(this._editingEndpointId, payload);
      } else {
        await API.createEndpoint(payload);
      }
      // Reload the endpoint list from the server (default flag is exclusive)
      this.endpoints = await API.listEndpoints();
      this._populateEndpointSelects();
    } catch (err) {
      alert("Failed to save endpoint: " + err.message);
    } finally {
      this.endpointModal.hide();
      this._editingEndpointId = null;
    }
  },

  _pendingDeleteEndpointId: null,

  promptDeleteEndpoint(id) {
    this._pendingDeleteEndpointId = id;
    const ep = this.endpoints.find((e) => e.id === id);
    document.getElementById("deleteEndpointNameSpan").textContent =
      ep ? `"${ep.name}"` : "this endpoint";
    this.deleteEndpointModal.show();
  },

  async confirmDeleteEndpoint() {
    if (!this._pendingDeleteEndpointId) return;
    try {
      await API.deleteEndpoint(this._pendingDeleteEndpointId);
      this.endpoints = await API.listEndpoints();
      // If the active conversation used this endpoint, reset to default
      if (this.convEndpointId === this._pendingDeleteEndpointId) {
        this.convEndpointId = null;
      }
      this._populateEndpointSelects();
    } catch (err) {
      alert("Failed to delete endpoint: " + err.message);
    } finally {
      this.deleteEndpointModal.hide();
      this._pendingDeleteEndpointId = null;
    }
  },

  // ── Persona CRUD ────────────────────────────────────────────────────────────

  _editingPersonaId: null,

  openPersonaModal(id) {
    this._editingPersonaId = id;
    const titleEl = document.getElementById("personaModalTitle");
    const nameEl  = document.getElementById("personaNameInput");
    const promptEl = document.getElementById("personaPromptInput");

    if (id) {
      const persona = this.personas.find((p) => p.id === id);
      titleEl.textContent   = "Edit Persona";
      nameEl.value          = persona ? persona.name   : "";
      promptEl.value        = persona ? persona.prompt : "";
    } else {
      titleEl.textContent = "New Persona";
      nameEl.value        = "";
      promptEl.value      = "";
    }
    // If settings modal is open, keep it behind
    this.personaModal.show();
    setTimeout(() => nameEl.focus(), 300);
  },

  async savePersona() {
    const name   = document.getElementById("personaNameInput").value.trim();
    const prompt = document.getElementById("personaPromptInput").value.trim();
    if (!name || !prompt) {
      alert("Both a name and a prompt are required.");
      return;
    }
    try {
      if (this._editingPersonaId) {
        const updated = await API.updatePersona(this._editingPersonaId, { name, prompt });
        const idx = this.personas.findIndex((p) => p.id === this._editingPersonaId);
        if (idx !== -1) this.personas[idx] = updated;
      } else {
        const created = await API.createPersona(name, prompt);
        this.personas.push(created);
        // Sort alphabetically
        this.personas.sort((a, b) => a.name.localeCompare(b.name));
      }
      this._populatePersonaSelects();
      // Re-sync current conversation's selection
      if (this.activeConvId) {
        const conv = Conversations.getById(this.activeConvId);
        if (conv) this._syncPersonaSelect(conv.persona_id);
      }
    } catch (err) {
      alert("Failed to save persona: " + err.message);
    } finally {
      this.personaModal.hide();
      this._editingPersonaId = null;
    }
  },

  _pendingDeletePersonaId: null,

  promptDeletePersona(id) {
    this._pendingDeletePersonaId = id;
    const persona = this.personas.find((p) => p.id === id);
    document.getElementById("deletePersonaNameSpan").textContent =
      persona ? `"${persona.name}"` : "this persona";
    this.deletePersonaModal.show();
  },

  async confirmDeletePersona() {
    if (!this._pendingDeletePersonaId) return;
    try {
      await API.deletePersona(this._pendingDeletePersonaId);
      this.personas = this.personas.filter((p) => p.id !== this._pendingDeletePersonaId);
      this._populatePersonaSelects();
      // If the active conversation used this persona, clear its selector
      if (this.activeConvId) {
        const conv = Conversations.getById(this.activeConvId);
        if (conv && conv.persona_id === this._pendingDeletePersonaId) {
          this.personaSelect.value = "";
          Conversations.update(this.activeConvId, { persona_id: null });
        }
      }
    } catch (err) {
      alert("Failed to delete persona: " + err.message);
    } finally {
      this.deletePersonaModal.hide();
      this._pendingDeletePersonaId = null;
    }
  },

  // ── Conversations ──────────────────────────────────────────────────────────

  async newConversation(folderId = null) {
    const personaId = this.personaSelect.value ? parseInt(this.personaSelect.value) : null;
    const endpointId = this.endpointSelect && this.endpointSelect.value ? parseInt(this.endpointSelect.value) : null;
    // Resolve the model: explicit selection, else the chosen endpoint's default,
    // else the active endpoint's default, else the app-level default.
    const ep = endpointId
      ? this.endpoints.find((e) => e.id === endpointId)
      : this._currentEndpoint();
    const modelId = this.modelSelect.value
      || (ep && ep.default_model)
      || this.defaultModel;
    try {
      const conv = await API.createConversation(modelId, personaId, endpointId, folderId);
      Conversations.add(conv);
      // Ensure the destination folder is expanded so the new item is visible
      if (folderId && typeof Folders !== "undefined") {
        Folders._collapsed.delete(folderId);
      }
      await this.selectConversation(conv.id);
    } catch (err) {
      console.error("Failed to create conversation:", err);
    }
  },

  async selectConversation(id) {
    if (this.isStreaming) return; // don't switch during streaming
    this.activeConvId = id;
    Conversations.setActive(id);

    // Hide the token badge immediately so the previous conversation's
    // value isn't shown while the new count is being fetched.
    if (this.tokenCountBadge) {
      this.tokenCountBadge.classList.add("d-none");
      this.tokenCountBadge.textContent = "";
    }

    try {
      const conv = await API.getConversation(id);

      // Show the chat panel
      this.emptyState.classList.add("d-none");
      this.chatPanel.classList.remove("d-none");

      // Update header
      this.chatTitleDisplay.textContent = conv.title;

      // Sync model selector
      this._syncModelSelect(conv.model_id);

      // Sync persona selector
      this._syncPersonaSelect(conv.persona_id);

      // Sync endpoint selector, then refresh models for that endpoint
      this._syncEndpointSelect(conv.endpoint_id);
      this._refreshModelsForCurrentEndpoint();

      // Load attached files and linked folders
      await Promise.all([
        Files.load(id),
        LinkedFolders.load(id),
      ]);

      // Sync per-conversation output directory
      this.convOutputDir = conv.output_dir || "";
      this._renderOutputDirBtn();

      // Sync per-conversation tools toggle
      this.convEnableTools = conv.enable_tools !== 0 && conv.enable_tools !== false;
      this._renderToolsBtn();

      // Refresh token count badge first, so a later rendering error can't
      // prevent the badge from updating for this conversation.
      this._refreshTokenCount();

      // Render messages
      try {
        Chat.renderHistory(conv.messages || []);
      } catch (renderErr) {
        console.error("Failed to render message history:", renderErr);
      }
    } catch (err) {
      console.error("Failed to load conversation:", err);
    }
  },

  // ── Streaming state helpers ──────────────────────────────────────────────────

  _beginStreaming() {
    this.isStreaming = true;
    this._abortController = new AbortController();
    this.messageInput.disabled = true;
    this.btnSend.classList.add("d-none");
    this.btnStop.classList.remove("d-none");
    this.streamStatus.classList.remove("d-none");
  },

  _endStreaming() {
    this.isStreaming = false;
    this._abortController = null;
    this.messageInput.disabled = false;
    this.btnSend.classList.remove("d-none");
    this.btnStop.classList.add("d-none");
    this.streamStatus.classList.add("d-none");
  },

  stopStreaming() {
    if (this._abortController) {
      this._abortController.abort();
    }
  },

  // ── Chat ───────────────────────────────────────────────────────────────────

  async sendMessage() {
    if (this.isStreaming) return;
    if (!this.activeConvId) {
      await this.newConversation();
    }

    const text = this.messageInput.value.trim();
    if (!text) return;

    this.messageInput.value = "";
    this._beginStreaming();

    // Show the user message immediately
    Chat.appendUserMessage(text);
    Chat.scrollToBottom();

    // Streaming assistant bubble
    let streamBubble = null;

    try {
      const res = await API.sendMessage(this.activeConvId, text, this._abortController.signal);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();

        if (done) {
          if (buffer.trim()) buffer += "\n";
        } else {
          buffer += decoder.decode(value, { stream: true });
        }

        const lines = buffer.split("\n");
        buffer = done ? "" : lines.pop();

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;

          let event;
          try { event = JSON.parse(raw); } catch { continue; }

          switch (event.type) {
            case "token":
              if (!streamBubble) streamBubble = Chat.createStreamingBubble();
              streamBubble.append(event.content);
              break;
            case "tool_result":
              if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
              Chat.appendToolNotification(event.success, event.display);
              break;
            case "title":
              if (event.conv_id === this.activeConvId) {
                this.chatTitleDisplay.textContent = event.title;
                Conversations.update(event.conv_id, { title: event.title });
              }
              break;
            case "error":
              if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
              Chat.appendToolNotification(false, `Error: ${event.message}`);
              break;
            case "done":
              if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
              Conversations.bumpToTop(this.activeConvId);
              this._refreshTokenCount();
              break;
          }
        }

        if (done) break;
      }
    } catch (err) {
      if (err.name === "AbortError") {
        if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
        Chat.appendToolNotification(false, "⏹ Generation stopped by user.");
      } else {
        if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
        Chat.appendToolNotification(false, `❌ Request failed: ${err.message}`);
      }
    } finally {
      this._endStreaming();
      this.messageInput.focus();
    }
  },

  // ── Token count ─────────────────────────────────────────────────────────────

  async _refreshTokenCount() {
    const convId = this.activeConvId;
    if (!convId) return;
    try {
      const data = await API.getTokenCount(convId);
      // Guard against a stale response: the user may have switched
      // conversations while this request was in flight.
      if (convId !== this.activeConvId) return;
      const t = data.estimated_tokens;
      const contextWindow = data.context_window || this.contextWindow;
      const pct = data.usage_percent ?? Math.min(100, Math.round(t / contextWindow * 1000) / 10);
      const contextLabel = contextWindow >= 1000
        ? `${(contextWindow / 1000).toFixed(contextWindow % 1000 ? 1 : 0)}k`
        : contextWindow;
      const label = `Context Used ${pct}% of ${contextLabel}`;
      this.tokenCountBadge.textContent = label;
      this.tokenCountBadge.title = `Estimated ${t.toLocaleString()} tokens of ${contextWindow.toLocaleString()} available`;
      this.tokenCountBadge.classList.remove("d-none", "bg-danger", "bg-warning", "bg-secondary");
      // Colour-code: green < 50k, yellow < 100k, red >= 100k
      if (t >= 100000)      this.tokenCountBadge.classList.add("bg-danger");
      else if (t >= 50000)  this.tokenCountBadge.classList.add("bg-warning", "text-dark");
      else                  this.tokenCountBadge.classList.add("bg-secondary");
    } catch (_) {}
  },

  // ── Regenerate ───────────────────────────────────────────────────────────────

  async regenerateResponse() {
    if (this.isStreaming || !this.activeConvId) return;

    this._beginStreaming();

    // Remove the last assistant bubble from the DOM
    const bubbles = this.messagesArea.querySelectorAll(".message-bubble.message-assistant");
    if (bubbles.length > 0) bubbles[bubbles.length - 1].remove();
    const tools = this.messagesArea.querySelectorAll(".message-tool");
    if (tools.length > 0) tools[tools.length - 1].remove();

    let streamBubble = null;
    try {
      const res = await API.regenerate(this.activeConvId, this._abortController.signal);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) { if (buffer.trim()) buffer += "\n"; }
        else buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = done ? "" : lines.pop();
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          let event;
          try { event = JSON.parse(raw); } catch { continue; }
          switch (event.type) {
            case "token":
              if (!streamBubble) streamBubble = Chat.createStreamingBubble();
              streamBubble.append(event.content); break;
            case "tool_result":
              if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
              Chat.appendToolNotification(event.success, event.display); break;
            case "done":
              if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
              this._refreshTokenCount(); break;
            case "error":
              if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
              Chat.appendToolNotification(false, `Error: ${event.message}`); break;
          }
        }
        if (done) break;
      }
    } catch (err) {
      if (err.name === "AbortError") {
        if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
        Chat.appendToolNotification(false, "⏹ Generation stopped by user.");
      } else {
        if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
        Chat.appendToolNotification(false, `❌ Regenerate failed: ${err.message}`);
      }
    } finally {
      this._endStreaming();
    }
  },

  // ── Edit user message ────────────────────────────────────────────────────────

  editUserMessage(msgId, currentText, bubbleEl) {
    if (this.isStreaming) return;

    // Replace bubble with an inline textarea
    const orig = bubbleEl.cloneNode(true);
    bubbleEl.innerHTML = "";
    bubbleEl.classList.add("p-0", "bg-transparent", "border-0");

    const ta = document.createElement("textarea");
    ta.className = "form-control border-secondary";
    ta.value = currentText;
    ta.rows = Math.min(10, currentText.split("\n").length + 1);
    ta.style.resize = "vertical";

    const bar = document.createElement("div");
    bar.className = "d-flex gap-2 mt-2 justify-content-end";
    bar.innerHTML = `
      <button class="btn btn-sm btn-secondary" id="btnEditCancel">Cancel</button>
      <button class="btn btn-sm btn-primary" id="btnEditSend"><i class="bi bi-send-fill me-1"></i>Send</button>`;

    bubbleEl.appendChild(ta);
    bubbleEl.appendChild(bar);
    ta.focus();

    bar.querySelector("#btnEditCancel").addEventListener("click", () => {
      bubbleEl.replaceWith(orig);
    });

    bar.querySelector("#btnEditSend").addEventListener("click", async () => {
      const newText = ta.value.trim();
      if (!newText) return;

      // Restore bubble appearance
      bubbleEl.classList.remove("p-0", "bg-transparent", "border-0");
      bubbleEl.textContent = newText;

      // Remove all DOM elements after this bubble
      let next = bubbleEl.nextElementSibling;
      while (next) {
        const toRemove = next;
        next = next.nextElementSibling;
        toRemove.remove();
      }

      // Persist edit on server (truncates subsequent messages)
      await API.editMessage(this.activeConvId, msgId, newText);

      // Re-send as new stream
      this._beginStreaming();

      let streamBubble = null;
      try {
        const res = await API.sendMessage(this.activeConvId, newText, this._abortController.signal);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) { if (buffer.trim()) buffer += "\n"; }
          else buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = done ? "" : lines.pop();
          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const raw = line.slice(6).trim();
            if (!raw) continue;
            let event;
            try { event = JSON.parse(raw); } catch { continue; }
            switch (event.type) {
              case "token":
                if (!streamBubble) streamBubble = Chat.createStreamingBubble();
                streamBubble.append(event.content); break;
              case "tool_result":
                if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
                Chat.appendToolNotification(event.success, event.display); break;
              case "done":
                if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
                Conversations.bumpToTop(this.activeConvId);
                this._refreshTokenCount(); break;
              case "error":
                if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
                Chat.appendToolNotification(false, `Error: ${event.message}`); break;
            }
          }
          if (done) break;
        }
      } catch (err) {
        if (err.name === "AbortError") {
          if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
          Chat.appendToolNotification(false, "⏹ Generation stopped by user.");
        } else {
          if (streamBubble) { streamBubble.finalise(); streamBubble = null; }
          Chat.appendToolNotification(false, `❌ Request failed: ${err.message}`);
        }
      } finally {
        this._endStreaming();
        this.messageInput.focus();
      }
    });
  },

  // ── Search ───────────────────────────────────────────────────────────────────

  _searchDebounce: null,

  async _onSearch() {
    const query = this.searchInput.value.trim();
    this.btnSearchClear.classList.toggle("d-none", !query);
    clearTimeout(this._searchDebounce);
    if (!query) {
      Conversations._renderFiltered(null);
      return;
    }
    this._searchDebounce = setTimeout(async () => {
      try {
        const results = await API.searchAll(query);
        Conversations._renderFiltered(results, query);
      } catch (_) {}
    }, 300);
  },

  // ── File preview ─────────────────────────────────────────────────────────────

  async previewFile(convId, fileId, filename) {
    document.getElementById("filePreviewTitle").innerHTML =
      `<i class="bi bi-file-earmark-text me-2"></i>${_escAttr(filename)}`;
    document.getElementById("filePreviewContent").textContent = "Loading…";
    document.getElementById("filePreviewMeta").textContent = "";
    this.filePreviewModal.show();
    try {
      const data = await API.previewFile(convId, fileId);
      document.getElementById("filePreviewContent").textContent = data.content;
      document.getElementById("filePreviewMeta").textContent =
        `${data.char_count.toLocaleString()} characters` +
        (data.truncated ? " (truncated)" : "");
    } catch (err) {
      document.getElementById("filePreviewContent").textContent = `Error: ${err.message}`;
    }
  },

  // ── Purge ──────────────────────────────────────────────────────────────────

  async purgeAll() {
    const confirmed = confirm(
      "Permanently delete ALL conversations, messages and uploaded files?\n\n" +
      "Your settings and personas will not be affected.\n\n" +
      "This cannot be undone."
    );
    if (!confirmed) return;

    // Close settings modal first
    this.settingsModal.hide();

    try {
      const res  = await fetch("/api/purge", { method: "POST" });
      const data = await res.json();
      const s    = data.summary;

      // Reset local state
      this.activeConvId = null;
      this.chatPanel.classList.add("d-none");
      this.emptyState.classList.remove("d-none");
      Chat.clear();
      Files.clear();
      LinkedFolders.clear();
      await Conversations.load();

      alert(
        `Purge complete.\n\n` +
        `• ${s.conversations} conversation(s) deleted\n` +
        `• ${s.messages} message(s) deleted\n` +
        `• ${s.files_deleted} uploaded file(s) removed from disk\n` +
        `• ${s.linked_folders} linked folder reference(s) removed`
      );
    } catch (err) {
      alert("Purge failed: " + err.message);
    }
  },

  // ── Export conversation ────────────────────────────────────────────────────

  async exportConversation() {
    if (!this.activeConvId) return;

    const conv = await API.getConversation(this.activeConvId);
    const messages = conv.messages || [];
    if (messages.length === 0) {
      alert("This conversation has no messages to export.");
      return;
    }

    // Build the markdown document
    const title    = conv.title || "Conversation";
    const datePart = new Date().toISOString().slice(0, 10);
    let md = `# ${title}\n\n_Exported: ${datePart}_\n\n---\n\n`;

    for (const msg of messages) {
      if (msg.role === "user") {
        md += `## You\n\n${msg.content}\n\n`;
      } else if (msg.role === "assistant" && !msg.tool_calls_json) {
        md += `## Assistant\n\n${msg.content}\n\n`;
      }
      // Skip raw tool messages
    }

    // Derive a safe filename from the conversation title
    const safeName = title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "")
      .slice(0, 60) || "conversation";
    const filename = `${safeName}_${datePart}.md`;

    if (this.outputDir) {
      // Write to the configured output directory via the server
      const effectiveDir = this.convOutputDir || this.outputDir;
      const path = effectiveDir.replace(/[\\/]$/, "") + "/" + filename;
      try {
        const res = await fetch("/api/write_file", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path, content: md }),
        });
        const data = await res.json();
        Chat.appendToolNotification(data.success, data.display);
        Chat.scrollToBottom();
      } catch (err) {
        Chat.appendToolNotification(false, `Failed to export: ${err.message}`);
      }
    } else {
      // No output dir set — fall back to browser download
      const blob = new Blob([md], { type: "text/markdown" });
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href     = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    }
  },

  // ── Rename ─────────────────────────────────────────────────────────────────

  _pendingRenameId: null,

  promptRename(id) {
    this._pendingRenameId = id;
    const conv = Conversations.getById(id);
    const input = document.getElementById("renameInput");
    input.value = conv ? conv.title : "";
    this.renameModal.show();
    setTimeout(() => { input.focus(); input.select(); }, 300);
  },

  async confirmRename() {
    const newTitle = document.getElementById("renameInput").value.trim();
    if (!newTitle || !this._pendingRenameId) return;
    try {
      await API.updateConversation(this._pendingRenameId, { title: newTitle });
      Conversations.update(this._pendingRenameId, { title: newTitle });
      if (this._pendingRenameId === this.activeConvId) {
        this.chatTitleDisplay.textContent = newTitle;
      }
    } catch (err) {
      console.error("Rename failed:", err);
    } finally {
      this.renameModal.hide();
      this._pendingRenameId = null;
    }
  },

  // ── Delete ─────────────────────────────────────────────────────────────────

  _pendingDeleteId: null,

  promptDelete(id) {
    this._pendingDeleteId = id;
    const conv = Conversations.getById(id);
    document.getElementById("deleteTitleSpan").textContent =
      conv ? `"${conv.title}"` : "this conversation";
    this.deleteModal.show();
  },

  async confirmDelete() {
    if (!this._pendingDeleteId) return;
    try {
      await API.deleteConversation(this._pendingDeleteId);
      Conversations.remove(this._pendingDeleteId);

      if (this._pendingDeleteId === this.activeConvId) {
        this.activeConvId = null;
        this.chatPanel.classList.add("d-none");
        this.emptyState.classList.remove("d-none");
        Files.clear();
        LinkedFolders.clear();
      }
    } catch (err) {
      console.error("Delete failed:", err);
    } finally {
      this.deleteModal.hide();
      this._pendingDeleteId = null;
    }
  },

  // ── Help ───────────────────────────────────────────────────────────────────

  openHelp() {
    const frame = document.getElementById("helpFrame");
    // Load the help page only the first time the modal is opened
    if (frame && !frame.getAttribute("src")) {
      frame.src = "/help.html";
    }
    this.helpModal.show();
  },

  // ── Settings ───────────────────────────────────────────────────────────────

  openSettings() {
    document.getElementById("defaultModelSelect").value  = this.defaultModel;
    document.getElementById("settingsBrowserRoot").value = this.browserRoot;
    document.getElementById("settingsOutputDir").value   = this.outputDir;
    document.getElementById("settingsContextWindow").value = this.contextWindow;
    // Always start with the Reset Database checkbox unticked
    const resetChk = document.getElementById("settingsResetDatabase");
    if (resetChk) resetChk.checked = false;
    // Collapse the inline folder browsers on open
    document.getElementById("settingsBrowserRootPane").classList.add("d-none");
    document.getElementById("settingsOutputDirPane").classList.add("d-none");
    this._renderPersonaList();
    this._renderEndpointList();
    this.settingsModal.show();
  },

  async saveSettings() {
    const resetChk   = document.getElementById("settingsResetDatabase");
    const doReset    = resetChk && resetChk.checked;

    // If "Reset Database" is ticked, clear the stored values instead of saving.
    if (doReset) {
      const confirmed = confirm(
        "Reset the default model, fallback default model, folder browser " +
        "starting path, context window and default output folder?\n\nThis cannot be undone."
      );
      if (!confirmed) return;
      try {
        const data = await API.resetSettings();
        const s = data.settings || {};
        this.defaultModel = s.default_model || "";
        this.browserRoot  = s.browser_root  || "";
        this.outputDir    = s.output_dir    || "";
        this.contextWindow = Number(s.context_window) || 128000;
        // Clear the on-screen fields to reflect the reset
        document.getElementById("defaultModelSelect").value  = "";
        document.getElementById("settingsBrowserRoot").value = "";
        document.getElementById("settingsOutputDir").value   = "";
        document.getElementById("settingsContextWindow").value = this.contextWindow;
        // Endpoints' default models were cleared server-side; refresh them.
        try {
          this.endpoints = await API.listEndpoints();
          this._populateEndpointSelects();
        } catch (_) {}
        this._renderOutputDirBtn();
      } catch (err) {
        console.error("Failed to reset settings:", err);
      } finally {
        this.settingsModal.hide();
      }
      return;
    }

    const newDefault     = document.getElementById("defaultModelSelect").value;
    const newBrowserRoot = document.getElementById("settingsBrowserRoot").value.trim();
    const newOutputDir   = document.getElementById("settingsOutputDir").value.trim();
    const newContextWindow = Number.parseInt(document.getElementById("settingsContextWindow").value, 10);
    if (!Number.isInteger(newContextWindow) || newContextWindow <= 0) {
      document.getElementById("settingsContextWindow").focus();
      return;
    }

    const payload = {
      default_model: newDefault,
      browser_root:  newBrowserRoot,
      output_dir:    newOutputDir,
      context_window: newContextWindow,
    };

    try {
      await API.saveSettings(payload);
      this.defaultModel = newDefault;
      this.browserRoot  = newBrowserRoot;
      this.outputDir    = newOutputDir;
      this.contextWindow = newContextWindow;
      this._renderOutputDirBtn(); // re-render with updated app default
    } catch (err) {
      console.error("Failed to save settings:", err);
    } finally {
      this.settingsModal.hide();
    }
  },

  // ── Theme ──────────────────────────────────────────────────────────────────

  _applyTheme(theme) {
    this.theme = theme;
    const isDark = theme === "dark";
    document.documentElement.setAttribute("data-bs-theme", theme);

    // Swap highlight.js stylesheet
    const hljsLink = document.getElementById("hljs-theme");
    if (hljsLink) {
      hljsLink.href = isDark
        ? "https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/styles/github-dark.min.css"
        : "https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/styles/github.min.css";
    }

    // Update toggle button icon
    const btn = document.getElementById("btnThemeToggle");
    if (btn) {
      btn.innerHTML = isDark
        ? '<i class="bi bi-moon-stars-fill"></i>'
        : '<i class="bi bi-sun-fill"></i>';
      btn.title = isDark ? "Switch to light theme" : "Switch to dark theme";
    }

    localStorage.setItem("theme", theme);
  },
  _toggleTheme() {
    this._applyTheme(this.theme === "dark" ? "light" : "dark");
  },

  // ── Import markdown as context ─────────────────────────────────────────────

  async _importMarkdownContext() {
    const file = this.mdContextInput.files[0];
    if (!file) return;
    this.mdContextInput.value = "";

    if (!this.activeConvId) {
      await this.newConversation();
    }

    try {
      const text = await file.text();
      const charCount = text.length;

      if (charCount > 400000) {
        const proceed = confirm(
          `"${file.name}" is very large (${charCount.toLocaleString()} characters).\n\n` +
          `Injecting it as context may produce slow or degraded responses.\n\n` +
          `Do you want to continue?`
        );
        if (!proceed) return;
      }

      // Upload the file as a regular attachment so it gets injected into context
      await Files.uploadFiles([file]);
    } catch (err) {
      Chat.appendToolNotification(false, `Failed to import markdown: ${err.message}`);
      Chat.scrollToBottom();
    }
  },

  // ── Output directory ────────────────────────────────────────────────────────

  /** The effective output dir for the active conversation (conv override → app default) */
  _effectiveOutputDir() {
    return this.convOutputDir || this.outputDir;
  },

  async _openOutputDirInFileManager() {
    try {
      await API.openOutputDir(this.activeConvId);
    } catch (err) {
      alert(err.message);
    }
  },

  _openOutputDirModal() {
    const input = document.getElementById("outputDirModalInput");
    input.value = this.convOutputDir;

    const defaultSpan = document.getElementById("outputDirModalDefault");
    defaultSpan.textContent = this.outputDir || "not set";

    // Hide the browser pane
    document.getElementById("outputDirBrowserPane").classList.add("d-none");

    this.outputDirModal.show();
    setTimeout(() => input.focus(), 300);
  },

  async _saveOutputDirModal() {
    const val = document.getElementById("outputDirModalInput").value.trim();
    this.convOutputDir = val;
    this.outputDirModal.hide();
    this._renderOutputDirBtn();
    if (this.activeConvId) {
      try {
        await API.updateConversation(this.activeConvId, { output_dir: val });
        Conversations.update(this.activeConvId, { output_dir: val });
      } catch (err) {
        console.error("Failed to save conversation output dir:", err);
      }
    }
  },

  // ── Output directory inline folder browser ───────────────────────────────

  _outputDirBrowserCurrentPath: null,

  async _outputDirBrowserNavigate(path) {
    const pane = document.getElementById("outputDirBrowserPane");
    pane.classList.remove("d-none");

    const listEl     = document.getElementById("outputDirBrowserList");
    const breadcrumb = document.getElementById("outputDirBreadcrumb");
    const upBtn      = document.getElementById("btnOutputDirBrowserUp");

    listEl.innerHTML = `<div class="text-secondary text-center py-3">
      <span class="spinner-border spinner-border-sm me-2"></span>Loading…</div>`;

    let data;
    try {
      data = await API.browse(path);
    } catch (err) {
      listEl.innerHTML = `<div class="text-danger small p-2">Error: ${_escH(err.message)}</div>`;
      return;
    }

    this._outputDirBrowserCurrentPath = data.path;
    upBtn.disabled = !data.parent;
    breadcrumb.innerHTML = this._buildOutputDirBreadcrumb(data.path);
    listEl.innerHTML = "";

    if (data.entries.length === 0) {
      listEl.innerHTML = `<div class="text-secondary small p-3">This folder is empty.</div>`;
      return;
    }

    data.entries.forEach((e) => {
      if (!e.is_dir) return; // only show folders for output dir selection
      const btn = document.createElement("button");
      btn.className = "browser-entry browser-entry-dir w-100 text-start";
      btn.innerHTML = `
        <i class="bi bi-folder-fill text-warning me-2"></i>
        <span class="flex-grow-1">${_escH(e.name)}</span>
        <i class="bi bi-chevron-right text-secondary ms-auto"></i>`;
      const childPath = data.path.replace(/\\/g, "/").replace(/\/$/, "") + "/" + e.name;
      btn.addEventListener("click", () => App._outputDirBrowserNavigate(childPath));
      listEl.appendChild(btn);
    });
  },

  _outputDirBrowserUp() {
    if (!this._outputDirBrowserCurrentPath) return;
    API.browse(this._outputDirBrowserCurrentPath).then((data) => {
      if (data.parent) this._outputDirBrowserNavigate(data.parent);
    }).catch(() => {});
  },

  _buildOutputDirBreadcrumb(fullPath) {
    const parts = fullPath.replace(/\\/g, "/").split("/").filter(Boolean);
    if (!parts.length) return `<li class="breadcrumb-item active">/</li>`;
    let accumulated = "";
    return parts.map((part, i) => {
      accumulated += (i === 0 ? "" : "/") + part;
      const path = accumulated;
      if (i === parts.length - 1) {
        return `<li class="breadcrumb-item active">${_escH(part)}</li>`;
      }
      return `<li class="breadcrumb-item">
        <a href="#" class="text-info text-decoration-none"
           onclick="event.preventDefault();App._outputDirBrowserNavigate(${JSON.stringify(path)})"
        >${_escH(part)}</a></li>`;
    }).join("");
  },

  _outputDirBrowserSelectCurrent() {
    if (!this._outputDirBrowserCurrentPath) return;
    document.getElementById("outputDirModalInput").value = this._outputDirBrowserCurrentPath;
    document.getElementById("outputDirBrowserPane").classList.add("d-none");
  },

  // ── Settings inline folder browsers (Starting Path + Output Folder) ────────
  // Config for each of the two settings folder pickers, keyed by a short id.
  _settingsFolderCfg: {
    browserRoot: {
      paneId:       "settingsBrowserRootPane",
      listId:       "settingsBrowserRootList",
      breadcrumbId: "settingsBrowserRootBreadcrumb",
      upBtnId:      "btnSettingsBrowserRootUp",
      inputId:      "settingsBrowserRoot",
      currentPath:  null,
    },
    outputDir: {
      paneId:       "settingsOutputDirPane",
      listId:       "settingsOutputDirList",
      breadcrumbId: "settingsOutputDirBreadcrumb",
      upBtnId:      "btnSettingsOutputDirUp",
      inputId:      "settingsOutputDir",
      currentPath:  null,
    },
  },

  async _settingsFolderBrowse(key, path) {
    const cfg = this._settingsFolderCfg[key];
    if (!cfg) return;
    const pane = document.getElementById(cfg.paneId);
    pane.classList.remove("d-none");

    const listEl     = document.getElementById(cfg.listId);
    const breadcrumb = document.getElementById(cfg.breadcrumbId);
    const upBtn      = document.getElementById(cfg.upBtnId);

    listEl.innerHTML = `<div class="text-secondary text-center py-3">
      <span class="spinner-border spinner-border-sm me-2"></span>Loading…</div>`;

    let data;
    try {
      data = await API.browse(path);
    } catch (err) {
      listEl.innerHTML = `<div class="text-danger small p-2">Error: ${_escH(err.message)}</div>`;
      return;
    }

    cfg.currentPath = data.path;
    upBtn.disabled = !data.parent;
    breadcrumb.innerHTML = this._buildSettingsFolderBreadcrumb(key, data.path);
    listEl.innerHTML = "";

    if (data.entries.length === 0) {
      listEl.innerHTML = `<div class="text-secondary small p-3">This folder is empty.</div>`;
      return;
    }

    data.entries.forEach((e) => {
      if (!e.is_dir) return; // only show folders for folder selection
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "browser-entry browser-entry-dir w-100 text-start";
      btn.innerHTML = `
        <i class="bi bi-folder-fill text-warning me-2"></i>
        <span class="flex-grow-1">${_escH(e.name)}</span>
        <i class="bi bi-chevron-right text-secondary ms-auto"></i>`;
      const childPath = data.path.replace(/\\/g, "/").replace(/\/$/, "") + "/" + e.name;
      btn.addEventListener("click", () => App._settingsFolderBrowse(key, childPath));
      listEl.appendChild(btn);
    });
  },

  _settingsFolderUp(key) {
    const cfg = this._settingsFolderCfg[key];
    if (!cfg || !cfg.currentPath) return;
    API.browse(cfg.currentPath).then((data) => {
      if (data.parent) this._settingsFolderBrowse(key, data.parent);
    }).catch(() => {});
  },

  _buildSettingsFolderBreadcrumb(key, fullPath) {
    const parts = fullPath.replace(/\\/g, "/").split("/").filter(Boolean);
    if (!parts.length) return `<li class="breadcrumb-item active">/</li>`;
    let accumulated = "";
    return parts.map((part, i) => {
      accumulated += (i === 0 ? "" : "/") + part;
      const path = accumulated;
      if (i === parts.length - 1) {
        return `<li class="breadcrumb-item active">${_escH(part)}</li>`;
      }
      return `<li class="breadcrumb-item">
        <a href="#" class="text-info text-decoration-none"
           onclick="event.preventDefault();App._settingsFolderBrowse(${JSON.stringify(key)},${JSON.stringify(path)})"
        >${_escH(part)}</a></li>`;
    }).join("");
  },

  _settingsFolderSelectCurrent(key) {
    const cfg = this._settingsFolderCfg[key];
    if (!cfg || !cfg.currentPath) return;
    document.getElementById(cfg.inputId).value = cfg.currentPath;
    document.getElementById(cfg.paneId).classList.add("d-none");
  },

  _renderOutputDirBtn() {
    const btn = this.btnOutputDir;
    if (!btn) return;
    const effective = this._effectiveOutputDir();
    const isConvOverride = !!this.convOutputDir;

    if (effective) {
      const parts = effective.replace(/\\/g, "/").split("/").filter(Boolean);
      const label = parts[parts.length - 1] || effective;
      btn.innerHTML = `<i class="bi bi-folder2-open me-1"></i><span class="output-dir-label">${_escAttr(label)}</span>`;
      btn.title = `Output folder: ${effective}${isConvOverride ? " (conversation override)" : " (app default)"}`;
      btn.classList.add("btn-output-dir-set");
    } else {
      btn.innerHTML = `<i class="bi bi-folder2-open"></i>`;
      btn.title = "Set output folder for this conversation";
      btn.classList.remove("btn-output-dir-set");
    }
  },};

// Boot
document.addEventListener("DOMContentLoaded", () => App.init());

function _escAttr(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
