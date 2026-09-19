#!/usr/bin/env node
/**
 * Frontend simulation harness for static/js/documents.js (no browser, no jsdom).
 *
 * Exercises the document-panel logic against a lightweight DOM stub:
 *   - document list rendering (no Delete button)
 *   - view / edit actions and editor population
 *   - dirty tracking + unsaved-change confirmation
 *   - save payload with expected_hash + refresh after save
 *   - 409 conflict handling (content preserved, server version offered)
 *   - reload server version / download / copy decisions
 *   - cancelling never silently discards
 *   - DOMPurify sanitisation of the rendered preview
 *
 * Run: node tests/frontend/documents_sim.js   (exit 0 = success)
 */

"use strict";

const fs = require("fs");
const path = require("path");

const failures = [];
function check(name, condition, detail) {
  if (!condition) failures.push(`${name} :: ${detail || "assertion failed"}`);
  else console.log(`ok - ${name}`);
}

const ROOT = path.resolve(__dirname, "..", "..");

// ── DOM stubs ────────────────────────────────────────────────────────────────
function makeClassList() {
  const set = new Set();
  return {
    set,
    add(...c) { c.forEach((x) => set.add(x)); },
    remove(...c) { c.forEach((x) => set.delete(x)); },
    toggle(c, force) {
      const want = force === undefined ? !set.has(c) : !!force;
      if (want) set.add(c); else set.delete(c);
      return want;
    },
    contains(c) { return set.has(c); },
  };
}

function makeElement(id) {
  return {
    id,
    value: "",
    innerHTML: "",
    textContent: "",
    disabled: false,
    style: {},
    classList: makeClassList(),
    dataset: {},
    _handlers: {},
    addEventListener(type, fn) { (this._handlers[type] = this._handlers[type] || []).push(fn); },
    fire(typeOrEvent) {
      const ev = typeof typeOrEvent === "string"
        ? { type: typeOrEvent, target: this }
        : typeOrEvent;
      (this._handlers[ev.type] || []).forEach((fn) => fn(ev));
    },
    focus() {},
    click() {},
    closest() { return null; },
    getAttribute(name) { return this.dataset[name] ? String(this.dataset[name]) : null; },
    setAttribute(name, val) { this.dataset[name] = val; },
    appendChild() {},
    removeChild() {},
    querySelectorAll() { return []; },
  };
}

const elements = {};
const modalShows = [];
const modalHides = [];

global.document = {
  getElementById(id) { if (!elements[id]) elements[id] = makeElement(id); return elements[id]; },
  createElement(tag) { return makeElement(`created-${tag}`); },
  body: makeElement("body"),
};

global.bootstrap = {
  Modal: class {
    constructor(el) { this.el = el; }
    show() { modalShows.push(this.el); }
    hide() { modalHides.push(this.el); }
  },
};

global.__capturedDownload = null;
global.Blob = class { constructor(parts, opts) { this.parts = parts; this.opts = opts; } };
global.URL = {
  createObjectURL(blob) { global.__capturedDownload = { blob }; return "blob:fake"; },
  revokeObjectURL() {},
};
Object.defineProperty(global, "navigator", {
  configurable: true,
  value: { clipboard: { writeText: (t) => { global.__copiedText = t; return Promise.resolve(); } } },
});
global.confirm = () => true;
global.__alerts=[]; global.alert = (m) => global.__alerts.push(String(m));

// ── Library stubs (mirror the CDN globals the app relies on) ────────────────
global.marked = { parse: (md) => `<div class="md">${String(md || "")}</div>` };

const sanitizeCalls = [];
global.DOMPurify = {
  sanitize(html, cfg) { sanitizeCalls.push({ html: String(html), cfg }); return `[sanitized]${html}`; },
  addHook() {},
};

// ── API stub ────────────────────────────────────────────────────────────────
const listCalls = [];
const readCalls = [];
const saveCalls = [];
const renameCalls = [];
const exportCalls = [];
const openDirCalls = [];
let saveConflict = null;      // set to a conflict payload to force 409
let saveResult = { success: true, content_hash: "sha256:new", modified_at: "2026-09-18T12:35:00Z", size_bytes: 24 };
let docs = [];
let readDoc = null;

global.API = {
  async listDocuments(convId) { listCalls.push(convId); return { documents: docs }; },
  async getDocument() { readCalls.push(1); return readDoc; },
  async saveDocument(convId, docId, content, expectedHash) {
    saveCalls.push({ convId, docId, content, expectedHash });
    if (saveConflict) return { conflict: true, data: saveConflict };
    return { conflict: false, data: saveResult };
  },
  async renameDocument(convId, docId, name) {
    renameCalls.push({ convId, docId, name });
    return { success: true, id: "output:renamed.md", name: "renamed.md" };
  },
  async exportDocument(convId, docId, format, content) {
    exportCalls.push({ convId, docId, format, content });
    const MT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    return { ok: true, async blob() { return new Blob(["PK-docx"], { type: MT }); } };
  },
  async openOutputDir(convId) { openDirCalls.push(convId); return { ok: true }; },
};

global.App = { activeConvId: 7 };

// Load the module under test inside a closure so its top-level `const`
// stays reachable, while all browser globals resolve through globalThis.
const src = fs.readFileSync(path.join(ROOT, "static", "js", "documents.js"), "utf8");
const wrappedSrc = "return (function(){\n" + src + "\nreturn MarkdownDocuments;\n})();";
const MarkdownDocuments = new Function(wrappedSrc)();
const MD = MarkdownDocuments;
const $ = (id) => document.getElementById(id);

// ── Scenario data ────────────────────────────────────────────────────────────
const NOW = new Date().toISOString();
docs = [
  { id: "output:report.md", name: "report.md", kind: "output", file_id: null, size_bytes: 1234,
    modified_at: NOW, content_hash: "sha256:abc", editable: true, managed_artifact: false },
  { id: "upload:5", name: "notes.md", kind: "upload", file_id: 5, size_bytes: 80,
    modified_at: NOW, content_hash: "sha256:abc2", editable: true, managed_artifact: false },
];
readDoc = Object.assign({ content: "# Report\n\nHello world\n" }, docs[0]);

(async () => {
  MD.init();

  // 1 ── List rendering
  await MD.load(1);
  const listHtml = $("documentsList").innerHTML;
  check("list renders name", listHtml.includes("report.md"), "missing report.md");
  check("list renders view", listHtml.includes("View"), "missing View action");
  check("list renders edit", listHtml.includes("Edit"), "missing Edit action");
  check("list renders download", listHtml.includes("Download"), "missing Download");
  check("list omits rename", !listHtml.includes("Rename"), "Rename still shown in list");
  check("list omits refresh", !listHtml.includes("Refresh"), "Refresh still shown in list");
  check("list has no delete (not approved)", !/Delete/i.test(listHtml), "Delete present");

  // 2 ── Edit action via list click
  const actionBtn = makeElement("actionBtn");
  actionBtn.dataset["data-doc-action"] = "edit";
  const rowEl = makeElement("rowEl");
  rowEl.dataset["data-doc-id"] = "output:report.md";
  actionBtn.closest = (sel) => (sel === "[data-doc-id]" ? rowEl : (sel === "[data-doc-action]" ? actionBtn : null));
  await MD._onListClick({ target: { closest: () => actionBtn, getAttribute: () => null } });
  if (process.env.DBG) console.log("DEBUG readCalls=", readCalls.length, "state=", !!MD._state, "shows=", modalShows.map(el=>el.id).join(","));
  check("editor opened via edit action", modalShows.some((el) => el.id === "documentEditorModal"), "editor modal not shown");
  check("editor populated with document content", $("docEditorSource").value === "# Report\n\nHello world\n", "source not populated");
  check("title set", $("docEditorTitle").textContent === "report.md", "title not set");
  check("not dirty on open", $("docEditorDirty").classList.contains("d-none"), "dirty indicator visible on open");
  check("stats show chars", /chars/i.test($("docEditorStats").textContent), "stats missing");

  // 3 ── Dirty tracking on input
  $("docEditorSource").value = "# Edited report\n\nNew body\n";
  $("docEditorSource").fire("input");
  check("dirty after edit", !$("docEditorDirty").classList.contains("d-none"), "dirty indicator not shown");
  check("dirty state tracked", MD._state && MD._state.dirty, "state.dirty false");
  const editedBody = "# Edited report\n\nNew body\n";
  const expectedStats = `${editedBody.length.toLocaleString()} chars`;
  check("stats reflect edit", $("docEditorStats").textContent.includes(expectedStats), `stats not updated (got: ${$("docEditorStats").textContent})`);

  // 4 ── Save payload + success path
  const editsCount = listCalls.length;
  await MD.save();
  const saved = saveCalls[saveCalls.length - 1];
  check("save sends expected_hash", saved.expectedHash === "sha256:abc", "wrong expected hash");
  check("save sends current content", saved.content.includes("Edited report"), "wrong content");
  check("save clears dirty", $("docEditorDirty").classList.contains("d-none"), "dirty remains after save");
  check("save refreshes list", listCalls.length > editsCount, "list not refreshed after save");

  // 5 ── Build-save-payload shape
  const payload = MD._buildSavePayload();
  check("payload shape", payload && Object.keys(payload).length === 2 && "expected_hash" in payload, "payload shape wrong");

  // 6 ── Conflict handling
  $("docEditorSource").value = "# My local edit\n";
  $("docEditorSource").fire("input");
  saveConflict = { error: "document_changed", message: "The file changed outside the editor.", current_hash: "sha256:server", current_content: "# Newer server version\n" };
  await MD.save();
  check("conflict banner shown", !$("docEditorConflict").classList.contains("d-none"), "conflict banner hidden");
  check("conflict shows server content", $("docEditorConflictServer").value === "# Newer server version\n", "server textarea not populated");
  check("local content preserved", $("docEditorSource").value === "# My local edit\n", "local content overwritten");
  check("expected hash rebased to server", MD._state.expectedHash === "sha256:server", "expected hash not rebased");

  // Conflict decisions
  MD.reloadServerVersion();
  check("reload server clears conflict", $("docEditorConflict").classList.contains("d-none"), "conflict not cleared");
  check("reload server sets content", $("docEditorSource").value === "# Newer server version\n", "server content not applied");
  check("reload server clears dirty", $("docEditorDirty").classList.contains("d-none"), "still dirty after reload");

  // Conflict + download keeps local edits
  $("docEditorSource").value = "# My local edit\n";
  $("docEditorSource").fire("input");
  saveConflict = { error: "document_changed", message: "changed", current_hash: "sha256:server2", current_content: "server v3" };
  await MD.save();
  MD.download();
  check("download exports local edit", global.__capturedDownload && global.__capturedDownload.blob.parts[0] === "# My local edit\n",
    `downloaded content wrong: ${JSON.stringify(global.__capturedDownload)}`);

  // Conflict + copy keeps local edits
  MD.copyLocal();
  check("copy writes local edit to clipboard", global.__copiedText === "# My local edit\n", "clipboard content wrong");

  // 6b ── DOCX export posts current (unsaved) content and returns a payload
  const exportBefore = exportCalls.length;
  await MD.downloadDocx();
  const expCall = exportCalls[exportCalls.length - 1];
  check("docx export posts current content", exportCalls.length > exportBefore && expCall &&
    expCall.format === "docx" && expCall.content === "# My local edit\n",
    `docx export call wrong: ${JSON.stringify(expCall)}`);
  check("docx download captured", global.__capturedDownload &&
    global.__capturedDownload.blob.parts[0] && typeof global.__capturedDownload.blob.parts[0].parts !== "undefined",
    "docx blob not captured for download");

  // 7 ── Cancel reverts edits (after confirming)
  await MD.cancel();
  check("cancel reverts to base", $("docEditorSource").value === MD._state.baseContent, "cancel did not revert");

  // 8 ── Unsaved-change confirmation on close
  $("docEditorSource").value = "# unsaved\n";
  $("docEditorSource").fire("input");
  global.confirm = () => false;
  MD.closeEditor();
  check("cancel-close keeps editor state", MD._state !== null && MD._state.dirty, "state discarded despite cancel");
  global.confirm = () => true;
  MD.closeEditor();
  check("confirmed close discards state", MD._state === null, "state not discarded after confirm");

  // 9 ── Preview uses tonnes of sanitised HTML
  await MD.openDocument("output:report.md", false);
  check("preview rendered through DOMPurify", sanitizeCalls.length > 0 && /Hello/.test(sanitizeCalls[sanitizeCalls.length - 1].html),
    "DOMPurify not used for preview");
  check("preview innerHTML is sanitised output", $("docEditorPreview").innerHTML === `[sanitized]${global.marked.parse(readDoc.content)}`,
    "sanitised preview not inserted");

  // 9b ── Full screen toggle + preview-only layout
  MD.preview();
  check("preview-only collapses to one column", $("docEditorSplit").classList.contains("preview-only"),
    "preview-only class not applied");
  MD.edit();
  check("edit restores split columns", !$("docEditorSplit").classList.contains("preview-only"),
    "preview-only class not removed on edit");

  // 9c ── Explicit edit-only / split / preview modes
  MD.editOnly();
  check("edit-only adds edit-only class", $("docEditorSplit").classList.contains("edit-only"),
    "edit-only class not applied");
  check("edit-only hides preview pane", $("docEditorPreview").style.display === "none",
    "preview pane not hidden in edit-only");
  check("edit-only removes preview-only", !$("docEditorSplit").classList.contains("preview-only"),
    "preview-only not removed in edit-only");
  MD.splitView();
  check("split removes preview-only and edit-only",
    !$("docEditorSplit").classList.contains("preview-only") &&
    !$("docEditorSplit").classList.contains("edit-only"),
    "split view classes not reset");
  check("split shows preview pane", $("docEditorPreview").style.display === "",
    "preview pane not restored in split");
  MD.preview();
  check("preview re-applies preview-only", $("docEditorSplit").classList.contains("preview-only"),
    "preview-only not re-applied");
  check("opening a document defaults to full screen",
    $("documentEditorModal").classList.contains("modal-fullscreen"),
    "modal-fullscreen not applied on open");
  MD.toggleFullscreen();
  check("fullscreen toggle removes modal-fullscreen", !$("documentEditorModal").classList.contains("modal-fullscreen"),
    "modal-fullscreen not removed");
  MD.toggleFullscreen();
  check("fullscreen toggle re-adds modal-fullscreen", $("documentEditorModal").classList.contains("modal-fullscreen"),
    "modal-fullscreen not re-added");

  // 10 ── Rename action routes through the API
  global.prompt = () => "renamed.md";
  await MD.renameDocument("output:report.md");
  const renameCall = renameCalls[renameCalls.length - 1];
  check("rename sends doc id + new name", renameCall && renameCall.docId === "output:report.md" && renameCall.name === "renamed.md",
    `rename not recorded: ${JSON.stringify(renameCalls)}`);
  check("rename updates open editor title",
    $("docEditorTitle").textContent === "renamed.md" && MD._state.docId === "output:renamed.md",
    "open editor not updated after rename");
  check("rename cancels on null prompt", (() => { global.prompt = () => null; const before = renameCalls.length; MD.renameDocument("output:report.md"); return renameCalls.length === before; })(),
    "null prompt still renamed");

  // 11 ── Ctrl+S on the editor triggers save
  await MD.openDocument("output:report.md", true);
  $("docEditorSource").value = "# saved by shortcut\n";
  $("docEditorSource").fire("input");
  const savesBefore = saveCalls.length;
  $("docEditorSource").fire({ type: "keydown", ctrlKey: true, key: "s", preventDefault() {} });
  await new Promise((resolve) => setTimeout(resolve, 0));
  check("ctrl+s saves", saveCalls.length > savesBefore && saveCalls[saveCalls.length - 1].content.includes("saved by shortcut"),
    "ctrl+s did not save");

  // 12 ── Refresh list targets the active conversation's output folder
  const before = listCalls.length;
  await MD.refreshList();
  check("refreshList refetches documents", listCalls.length > before, "list not refetched");
  check("refreshList targets active conversation", listCalls[listCalls.length - 1] === 7,
    `wrong conv targeted: ${JSON.stringify(listCalls)}`);

  // 13 ── Open output folder routes through the server for the active conversation
  await MD.openOutputFolder();
  check("open output folder posts to server", openDirCalls.length === 1 && openDirCalls[0] === 7,
    `openOutputDir not called: ${JSON.stringify(openDirCalls)}`);

  if (failures.length) {
    console.error(`\nFAILED (${failures.length}):`);
    failures.forEach((f) => console.error("  - " + f));
    process.exit(1);
  }
  console.log("\nAll frontend document-panel checks passed.");
  process.exit(0);
})().catch((err) => {
  console.error("Harness crashed:", err);
  process.exit(2);
});