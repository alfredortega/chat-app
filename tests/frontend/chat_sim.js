"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
let failures = 0;

function check(name, condition) {
  if (condition) console.log(`PASS: ${name}`);
  else {
    failures += 1;
    console.error(`FAIL: ${name}`);
  }
}

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.className = "";
    this.scrollHeight = 0;
    this.scrollTop = 0;
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }
}

global.document = {
  createElement: (tagName) => new FakeElement(tagName),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
};
global.marked = {
  Renderer: class {},
  use() {},
  parse: (text) => text,
};

const src = fs.readFileSync(path.join(ROOT, "static", "js", "chat.js"), "utf8");
const loaded = new Function(`${src}\nreturn { Chat, assistantDisplayMarkdown };`)();
const { Chat, assistantDisplayMarkdown } = loaded;

const leaked = [
  "Visible response.",
  "",
  "<｜DSML｜ calls>",
  '<｜DSML｜ invoke name="read_file">',
  "</｜DSML｜ invoke>",
  "</｜DSML｜ calls>",
].join("\n");
check("DSML control block is hidden", assistantDisplayMarkdown(leaked) === "Visible response.");
check(
  "incomplete DSML control block is hidden while streaming",
  assistantDisplayMarkdown("Visible\n<｜DSML｜ calls>\npartial") === "Visible"
);

const readNotice = Chat._historicalToolNotification({
  function: { name: "read_named_file", arguments: '{"name":"large.md"}' },
}, "# Large Markdown\n\n| A | B |\n|---|---|");
check("historical file result is summarized", readNotice.display === "Read file: `large.md`");
check("historical file result remains successful", readNotice.success === true);

const errorNotice = Chat._historicalToolNotification({
  function: { name: "run_python", arguments: "{}" },
}, "Script exited with code 1:\n<trace>");
check("historical tool failure uses first line", errorNotice.display === "Script exited with code 1:");
check("historical tool failure is marked failed", errorNotice.success === false);

const area = new FakeElement("div");
Chat.init(area);
const wrapper = Chat.appendToolNotification(true, "Read `<img src=x onerror=bad()>`");
const pill = wrapper.children[0];
check("notification creates an inline code node", pill.children[1].tagName === "code");
check("notification keeps HTML-looking path as text", pill.children[1].textContent === "<img src=x onerror=bad()>");

if (failures) process.exit(1);
console.log("\nAll chat rendering checks passed.");
