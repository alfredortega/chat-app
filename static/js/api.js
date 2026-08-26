/**
 * api.js — all fetch() calls to the Flask backend
 */

const API = {

  async getModels() {
    const res = await fetch("/api/models");
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // { models: [...] }
  },

  /** Fetch the model list for a specific endpoint (used to validate/populate). */
  async getModelsForEndpoint(endpointId) {
    const url = endpointId
      ? `/api/models?endpoint_id=${encodeURIComponent(endpointId)}`
      : "/api/models";
    const res = await fetch(url);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  /** Probe an unsaved endpoint's model list from a base URL + key. */
  async getModelsForUrl(baseUrl, apiKey) {
    const params = new URLSearchParams({ base_url: baseUrl });
    if (apiKey) params.set("api_key", apiKey);
    const res = await fetch(`/api/models?${params.toString()}`);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  // ── Endpoints ───────────────────────────────────────────────────────────────

  async listEndpoints() {
    const res = await fetch("/api/endpoints");
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // [ { id, name, base_url, api_key_set, is_default, ... } ]
  },

  async createEndpoint(data) {
    const res = await fetch("/api/endpoints", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async updateEndpoint(id, data) {
    const res = await fetch(`/api/endpoints/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async deleteEndpoint(id) {
    const res = await fetch(`/api/endpoints/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async getSettings() {
    const res = await fetch("/api/settings");
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // { default_model: "..." }
  },

  async saveSettings(data) {
    const res = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async resetSettings() {
    const res = await fetch("/api/settings/reset", { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // { ok: true, settings: {...} }
  },

  // ── Research sources ───────────────────────────────────────────────────────

  async listResearchSources() {
    const res = await fetch("/api/research-sources");
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async createResearchSource(data) {
    const res = await fetch("/api/research-sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async updateResearchSource(id, data) {
    const res = await fetch(`/api/research-sources/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async deleteResearchSource(id) {
    const res = await fetch(`/api/research-sources/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async openOutputDir(conversationId) {
    const res = await fetch("/api/open-output-dir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: conversationId || null }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async listConversations() {
    const res = await fetch("/api/conversations");
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // [ { id, title, model_id, ... }, ... ]
  },

  async createConversation(modelId, personaId, endpointId, folderId) {
    const res = await fetch("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model_id: modelId,
        persona_id: personaId || null,
        endpoint_id: endpointId || null,
        folder_id: folderId || null,
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // { id, title, model_id, persona_id, folder_id, ... }
  },

  async getConversation(id) {
    const res = await fetch(`/api/conversations/${id}`);
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // { id, title, model_id, messages: [...] }
  },

  async updateConversation(id, data) {
    const res = await fetch(`/api/conversations/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  async deleteConversation(id) {
    const res = await fetch(`/api/conversations/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  /**
   * Send a chat message and return the raw Response for SSE streaming.
   */
  async sendMessage(convId, message, signal) {
    const res = await fetch(`/api/conversations/${convId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
      signal,
    });
    if (!res.ok) throw new Error(await res.text());
    return res; // caller reads the ReadableStream
  },

  async regenerate(convId, signal) {
    const res = await fetch(`/api/conversations/${convId}/regenerate`, {
      method: "POST",
      signal,
    });
    if (!res.ok) throw new Error(await res.text());
    return res;
  },

  async editMessage(convId, msgId, content) {
    const res = await fetch(`/api/conversations/${convId}/messages/${msgId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async searchAll(query) {
    const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async getTokenCount(convId) {
    const res = await fetch(`/api/conversations/${convId}/token-count`);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async previewFile(convId, fileId) {
    const res = await fetch(`/api/conversations/${convId}/files/${fileId}/preview`);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  // ── Personas ──────────────────────────────────────────────────────────────

  async listPersonas() {
    const res = await fetch("/api/personas");
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // [ { id, name, prompt, ... }, ... ]
  },

  async createPersona(name, prompt) {
    const res = await fetch("/api/personas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, prompt }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async updatePersona(id, data) {
    const res = await fetch(`/api/personas/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async deletePersona(id) {
    const res = await fetch(`/api/personas/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  // ── Conversation files ─────────────────────────────────────────────────────

  async listFiles(convId) {
    const res = await fetch(`/api/conversations/${convId}/files`);
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // [ { id, original_name, size_bytes, char_count, ... } ]
  },

  async uploadFile(convId, file, displayName) {
    const form = new FormData();
    // Use a Blob rename trick to send the relative path as the filename
    const name = displayName || file.name;
    form.append("file", file, name);
    const res = await fetch(`/api/conversations/${convId}/files`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // { id, original_name, warn_large, truncated, ... }
  },

  async deleteFile(convId, fileId) {
    const res = await fetch(`/api/conversations/${convId}/files/${fileId}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  // ── Folder browser ────────────────────────────────────────────────────────

  async browse(path) {
    const url = path ? `/api/browse?path=${encodeURIComponent(path)}` : "/api/browse";
    const res = await fetch(url);
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // { path, parent, entries: [{name, is_dir, ext, size_bytes}] }
  },

  // ── Linked folders ────────────────────────────────────────────────────────

  async listLinkedFolders(convId) {
    const res = await fetch(`/api/conversations/${convId}/linked-folders`);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async addLinkedFolder(convId, folderPath) {
    const res = await fetch(`/api/conversations/${convId}/linked-folders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_path: folderPath }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // { id, folder_path, file_count, warn_large, total_chars }
  },

  async deleteLinkedFolder(convId, folderId) {
    const res = await fetch(`/api/conversations/${convId}/linked-folders/${folderId}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  // ── Conversation Folders ──────────────────────────────────────────────────

  async listFolders() {
    const res = await fetch("/api/folders");
    if (!res.ok) throw new Error(await res.text());
    return res.json(); // [ { id, name, position, ... } ]
  },

  async createFolder(name, codeFolder = false) {
    const res = await fetch("/api/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, code_folder: codeFolder }),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async updateFolder(id, data) {
    const res = await fetch(`/api/folders/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  async deleteFolder(id) {
    const res = await fetch(`/api/folders/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },

  // ── Folder export / import ────────────────────────────────────────────────

  /**
   * Trigger a ZIP download for the given folder.
   * Uses a hidden <a> element so the browser's native "Save As" dialog fires.
   */
  exportFolder(folderId) {
    const a = document.createElement("a");
    a.href = `/api/folders/${folderId}/export`;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  },

  /**
   * Upload a previously-exported ZIP and import the folder.
   * @param {File} file  The .zip file chosen by the user.
   * @returns {Promise<{ok, folder_id, folder_name, conversations_imported, files_imported}>}
   */
  async importFolder(file) {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/folders/import", { method: "POST", body: form });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
};
