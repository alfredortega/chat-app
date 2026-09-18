#!/usr/bin/env node
/**
 * Frontend simulation harness for static/js/markdown_import.js (no browser).
 *
 * Verifies the server-side "Import Markdown from output folder" browser:
 *   - starts in the conversation's output folder
 *   - renders folders + Markdown files, folders navigate
 *   - clicking a file imports it as a conversation upload and refreshes the
 *     file chips + document panel
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

function makeElement(id) {
  const set = new Set();
  return {
    id,
    value: "",
    innerHTML: "",
    textContent: "",
    disabled: false,
    style: {},
    dataset: {},
    classList: {
      add(...c) { c.forEach((x) => set.add(x)); },
      remove(...c) { c.forEach((x) => set.delete(x)); },
      toggle(c, f) { const w = f === undefined ? !set.has(c) : !!f; w ? set.add(c) : set.delete(c); },
      contains(c) { return set.has(c); },
    },
    _handlers: {},
    addEventListener(t, fn) { (this._handlers[t] = this._handlers[t] || []).push(fn); },
    fire(t) { (this._handlers[t] || []).forEach((fn) => fn({ target: this })); },
    focus() {}, click() {},
    closest() { return null; },
    getAttribute(name) { return this.dataset && this.dataset[name] ? String(this.dataset[name]) : null; },
    setAttribute(n, v) { this.dataset = this.dataset || {}; this.dataset[n] = v; },
    appendChild() {}, removeChild() {}, querySelectorAll() { return []; },
  };
}

const elements = {};
const modalShows = [];
global.document = {
  getElementById(id) { if (!elements[id]) elements[id] = makeElement(id); return elements[id]; },
  createElement: (t) => makeElement(t),
  body: makeElement("body"),
};
global.bootstrap = {
  Modal: class { constructor(el) { this.el = el; } show() { modalShows.push(this.el); } hide() {} },
};
global.App = { activeConvId: 3 };

const browseCalls = [];
const importCalls = [];
let filesLoadCalls = [];
let docRefreshes = 0;
let lastNotification = "";

global.API = {
  async browseOutputImport(convId, rel) {
    browseCalls.push({ convId, rel: rel || "" });
    if (rel === "sub") {
      return { output_dir: "/out", current: "sub", up: "", entries: [{ name: "deep.md", is_dir: false, ext: ".md", size_bytes: 12, rel_path: "sub/deep.md" }] };
    }
    return {
      output_dir: "/out", current: "", up: null,
      entries: [
        { name: "docs", is_dir: true, ext: "", size_bytes: 0, rel_path: "docs" },
        { name: "report.md", is_dir: false, ext: ".md", size_bytes: 120, rel_path: "report.md" },
        { name: "notes.txt", is_dir: false, ext: ".txt", size_bytes: 40, rel_path: "notes.txt" },
      ],
    };
  },
  async importMarkdownFromOutput(convId, rel) {
    importCalls.push({ convId, rel });
    return { success: true, original_name: rel.split("/").pop(), id: 9 };
  },
};
global.Files = { async load(convId) { filesLoadCalls.push(convId); } };
global.MarkdownDocuments = { async refreshList() { docRefreshes += 1; } };
global.Chat = { appendToolNotification(ok, msg) { lastNotification = msg; } };
global.alert = () => {};

const src = fs.readFileSync(path.join(ROOT, "static", "js", "markdown_import.js"), "utf8");
const ImportMarkdown = new Function("return (function(){\n" + src + "\nreturn ImportMarkdown;\n})();")();
const MI = ImportMarkdown;
const $ = (id) => document.getElementById(id);

(async () => {
  MI.init();
  await MI.open();

  check("import browser opens", modalShows.some((el) => el.id === "mdImportModal"), "modal not shown");
  check("starts at output folder root", browseCalls.length === 1 && browseCalls[0].rel === "", `wrong start: ${JSON.stringify(browseCalls)}`);
  const listHtml = $("mdImportList").innerHTML;
  check("renders folder", listHtml.includes("docs"), "folder row missing");
  check("renders markdown file", listHtml.includes("report.md"), "file row missing");
  check("renders breadcrumb root", $("mdImportBreadcrumb").innerHTML.includes("Output folder"), "root crumb missing");
  check("up disabled at root", $("btnMdImportUp").disabled === true, "up button not disabled at root");
  check("output dir shown", $("mdImportOutputDir").textContent === "/out", "output dir label missing");

  // Navigate into a subfolder
  const dirEl = makeElement("dirEl");
  dirEl.dataset["data-mdir"] = "docs";
  const dirTarget = { closest: (sel) => (sel === "[data-mdir]" ? dirEl : null) };
  await MI._onListClick({ target: dirTarget });
  check("folder click navigates", browseCalls.length === 2 && browseCalls[1].rel === "docs", `nav not recorded: ${JSON.stringify(browseCalls)}`);

  // Back to root, then import a file
  const fileEl = makeElement("fileEl");
  fileEl.dataset["data-mfile"] = "report.md";
  const fileTarget = { closest: (sel) => (sel === "[data-mfile]" ? fileEl : null) };
  await MI._onListClick({ target: fileTarget });
  check("file click imports", importCalls.length === 1 && importCalls[0].rel === "report.md", `import not recorded`);
  check("import refreshes file chips", filesLoadCalls[0] === 3, "Files.load not called with conv id");
  check("import refreshes document panel", docRefreshes === 1, "documents panel not refreshed");
  check("import notification shown", /report\.md/.test(lastNotification), "no notification");

  // Traversal state: up button enabled after entering a folder
  await MI._navigate("sub");
  check("up enabled in subfolder", $("btnMdImportUp").disabled === false, "up button still disabled in subfolder");

  if (failures.length) {
    console.error(`\nFAILED (${failures.length}):`);
    failures.forEach((f) => console.error("  - " + f));
    process.exit(1);
  }
  console.log("\nAll markdown-import browser checks passed.");
  process.exit(0);
})().catch((err) => {
  console.error("Harness crashed:", err);
  process.exit(2);
});