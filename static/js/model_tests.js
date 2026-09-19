/**
 * model_tests.js — Model Testing window.
 *
 * Sends one prompt to every selected model on the active conversation's
 * endpoint and renders a side-by-side comparison table (model / response as
 * Markdown). Responses longer than 200 characters are saved to
 * model_test_results/<model>_<yyyy.mm.dd>.md; the table cell then shows a
 * link that opens the saved file in the existing Markdown editor (read-only).
 */

const ModelTests = {
  _modal: null,
  _models: [],
  _fullscreen: false,

  init() {
    const btn = document.getElementById("btnModelTest");
    if (!btn) return;
    btn.addEventListener("click", () => this.open());

    const submit = document.getElementById("btnModelTestSubmit");
    if (submit) submit.addEventListener("click", () => this.run());

    const fullscreen = document.getElementById("btnModelTestFullscreen");
    if (fullscreen) fullscreen.addEventListener("click", () => this.toggleFullscreen());

    // Clicking a "Full response" link opens the saved file in the editor.
    const results = document.getElementById("modelTestResults");
    if (results) {
      results.addEventListener("click", (e) => {
        const el = e.target && e.target.closest ? e.target.closest("[data-test-file]") : null;
        if (!el) return;
        this.openResultFile(el.getAttribute("data-test-file"));
      });
    }

    if (typeof bootstrap !== "undefined" && bootstrap.Modal) {
      this._modal = new bootstrap.Modal(document.getElementById("modelTestModal"));
    } else {
      this._modal = { show() {}, hide() {} };
    }
  },

  /** Open the window and load the active conversation's model list. */
  async open() {
    await this._loadModels();
    this._renderModelList();
    document.getElementById("modelTestStatus").textContent = "";
    if (this._modal) this._modal.show();
    setTimeout(() => document.getElementById("modelTestPrompt").focus(), 300);
  },

  /** Fetch the fresh model list for the active conversation's endpoint. */
  async _loadModels() {
    try {
      await App._refreshModelsForCurrentEndpoint();
    } catch (_e) {
      // App.models still holds the last known good list on failure.
    }
    this._models = (typeof App !== "undefined" && App.models) || [];
  },

  _renderModelList() {
    const box = document.getElementById("modelTestModelList");
    if (!box) return;
    if (!this._models.length) {
      box.innerHTML = '<div class="text-secondary small p-2">No models available for the current endpoint.</div>';
      return;
    }
    const rows = this._models.map((m) => `
      <div class="d-flex align-items-center gap-2 p-2 border-bottom border-secondary">
        <input class="form-check-input mt-0 model-test-check" type="checkbox" value="${_mtEsc(m)}">
        <label class="form-check-label small text-truncate flex-grow-1" title="${_mtEsc(m)}">${_mtEsc(m)}</label>
      </div>
    `).join("");
    box.innerHTML = rows;
  },

  /** Expand the window to full screen. */
  toggleFullscreen() {
    const dialog = document.getElementById("modelTestModalDialog");
    if (!dialog) return;
    this._fullscreen = !this._fullscreen;
    dialog.classList.toggle("modal-fullscreen", this._fullscreen);
    const btn = document.getElementById("btnModelTestFullscreen");
    if (btn) {
      btn.innerHTML = this._fullscreen
        ? '<i class="bi bi-fullscreen-exit" title="Exit full screen"></i>'
        : '<i class="bi bi-arrows-fullscreen"></i>';
      btn.title = this._fullscreen ? "Exit full screen" : "Toggle full screen";
    }
  },

  /** Send the prompt to every selected model and render the comparison table. */
  async run() {
    const boxes = Array.from(document.querySelectorAll("#modelTestModelList .model-test-check"));
    const selected = boxes.filter((c) => c.checked).map((c) => c.value);
    const prompt = document.getElementById("modelTestPrompt").value.trim();
    if (!selected.length) { alert("Select at least one model."); return; }
    if (!prompt) { alert("Enter a prompt first."); return; }

    const status = document.getElementById("modelTestStatus");
    const submit = document.getElementById("btnModelTestSubmit");
    submit.disabled = true;
    status.innerHTML = `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>` +
      `Running tests on ${selected.length} model${selected.length === 1 ? "" : "s"}…`;
    try {
      const data = await API.runModelTest(
        (typeof App !== "undefined" && App.convEndpointId) || null,
        selected,
        prompt,
      );
      this._renderResults((data && data.results) || []);
      status.innerHTML = '<i class="bi bi-check2 text-success me-1"></i>Done.';
    } catch (err) {
      status.textContent = "";
      const body = document.getElementById("modelTestResults");
      if (body) body.innerHTML = `<div class="text-danger small">Test failed: ${_mtEsc(err.message)}</div>`;
    } finally {
      submit.disabled = false;
    }
  },

  _renderResults(results) {
    const container = document.getElementById("modelTestResults");
    if (!container) return;
    if (!results.length) {
      container.innerHTML = '<div class="text-secondary small">No results were returned.</div>';
      return;
    }
    const rows = results.map((r) => {
      const body = r.error
        ? `<span class="text-danger">Error: ${_mtEsc(r.error)}</span>`
        : r.saved
          ? this._savedLink(r.file_name)
          : this._renderMarkdown(r.response);
      return `<tr>
        <td class="align-top fw-semibold">${_mtEsc(r.model)}</td>
        <td class="align-top">${body}</td>
      </tr>`;
    }).join("");
    container.innerHTML = `
      <div class="d-flex align-items-center gap-2 mb-1">
        <span class="fw-semibold small">Results</span>
        <span class="text-secondary small">(long responses are saved to the <code>model_test_results</code> folder; click Full response to open in the editor)</span>
      </div>
      <table class="table table-sm align-middle border-secondary">
        <thead>
          <tr><th style="width:30%">Model</th><th>Response</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
    // Bring the results into view — with tall prompts/long lists they can
    // render below the modal's visible area otherwise.
    const scroller = container.closest ? container.closest(".modal-body") || container.closest(".modal") : container.parentElement;
    if (scroller) {
      try { scroller.scrollIntoView({ block: "nearest", behavior: "smooth" }); } catch (_e) {
        try { scroller.scrollTop = scroller.scrollHeight; } catch (_e2) {}
      }
    }
  },

  /** Link to the saved file; opens it read-only in the Markdown editor. */
  _savedLink(fileName) {
    return `<div class="mt-1">
        <button type="button" class="btn btn-sm btn-link p-0" data-test-file="${_mtEsc(fileName)}">
          <i class="bi bi-file-earmark-text me-1"></i>Full response (${_mtEsc(fileName)}) — open in editor
        </button>
      </div>`;
  },

  /** Open a saved test-result file in the existing Markdown editor. */
  async openResultFile(fileName) {
    try {
      const doc = await API.getModelTestFile(fileName);
      if (typeof MarkdownDocuments !== "undefined") {
        MarkdownDocuments.openEditorContent(doc, false);
      } else {
        alert("The Markdown editor is unavailable.");
      }
    } catch (err) {
      alert(`Could not open the test result: ${err.message}`);
    }
  },

  _renderMarkdown(md) {
    let html = "";
    if (typeof parseMarkdown === "function") {
      try { html = parseMarkdown(md || ""); } catch (_e) { html = _mtEsc(md || ""); }
    } else {
      html = _mtEsc(md || "");
    }
    if (typeof DOMPurify !== "undefined" && typeof DOMPurify.sanitize === "function") {
      return DOMPurify.sanitize(html, { USE_PROFILES: { html: true }, ADD_ATTR: ["target"] });
    }
    return html;
  },
};

function _mtEsc(str) {
  return String(str === undefined || str === null ? "" : str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}