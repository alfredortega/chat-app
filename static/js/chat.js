/**
 * chat.js — message rendering, markdown, streaming
 */

// Custom renderer: syntax-highlight fenced code blocks with highlight.js
const _renderer = new marked.Renderer();
_renderer.code = function (code, infostring) {
  // marked@9 passes the code and info string as separate arguments.
  const languageName = (infostring || "").trim().split(/\s+/)[0];
  const canHighlight = typeof hljs !== "undefined";
  const language = canHighlight && languageName && hljs.getLanguage(languageName)
    ? languageName
    : "plaintext";
  const highlighted = canHighlight
    ? hljs.highlight(code, { language }).value
    : escapeHtml(code);
  return `<pre><code class="hljs language-${language}">${highlighted}</code></pre>`;
};

// marked v5+ uses marked.use() for all configuration — setOptions is a no-op
marked.use({
  renderer: _renderer,
  gfm:      true,
  breaks:   true,
  async:    false,
});

/** Parse markdown → HTML string, guaranteed synchronous. */
function parseMarkdown(md) {
  const result = marked.parse(md || "");
  // Guard: if a Promise slips through (shouldn't with async:false), fall back
  if (result && typeof result.then === "function") {
    console.warn("marked.parse returned a Promise — falling back to plain text");
    return `<pre>${md}</pre>`;
  }
  return result;
}

/** Remove provider control syntax that was emitted as visible assistant text. */
function assistantDisplayMarkdown(markdown) {
  return String(markdown || "").replace(
    /<\uFF5CDSML\uFF5C\s*calls>[\s\S]*?(?:<\/\uFF5CDSML\uFF5C\s*calls>|$)/gi,
    ""
  ).trimEnd();
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Rendering the whole markdown buffer on every SSE token is wasteful; batch
// renders at this interval and always flush before the bubble is finalised.
const STREAM_RENDER_MS = 80;

const Chat = {
  messagesArea: null,

  init(container) {
    this.messagesArea = container;
  },

  /** Remove all messages from the display */
  clear() {
    this.messagesArea.innerHTML = "";
  },

  /**
   * Render a full list of stored messages (when loading a conversation).
   * Skips internal tool-call assistant messages and raw tool result messages —
   * those are represented by tool-notification bubbles instead.
   */
  renderHistory(messages) {
    this.clear();
    let i = 0;
    while (i < messages.length) {
      const msg = messages[i];

      if (msg.role === "user") {
        this.appendUserMessage(msg.content, msg.id);
        i++;
        continue;
      }

      if (msg.role === "assistant" && msg.tool_calls_json) {
        // This assistant turn triggered tool calls — find the following tool results
        let j = i + 1;
        const toolResults = [];
        while (j < messages.length && messages[j].role === "tool") {
          toolResults.push(messages[j]);
          j++;
        }
        // Render any partial text the assistant produced before tool call
        if (msg.content && msg.content.trim()) {
          this.appendAssistantMessage(msg.content);
        }
        // Render tool notifications
        let toolCalls = [];
        try { toolCalls = JSON.parse(msg.tool_calls_json) || []; } catch (_) {}
        toolResults.forEach((tr, index) => {
          const notification = this._historicalToolNotification(toolCalls[index], tr.content);
          this.appendToolNotification(notification.success, notification.display);
        });
        i = j;
        continue;
      }

      if (msg.role === "assistant") {
        this.appendAssistantMessage(msg.content, msg.id);
        i++;
        continue;
      }

      // Skip raw tool messages (already handled above)
      i++;
    }
    this.scrollToBottom();
  },

  appendUserMessage(text, msgId) {
    const el = document.createElement("div");
    el.className = "message-bubble message-user";
    if (msgId) el.dataset.msgId = msgId;
    el.textContent = text;

    // Edit button (shown on hover via CSS)
    const editBtn = document.createElement("button");
    editBtn.className = "btn btn-sm btn-link text-white-50 p-0 ms-2 edit-msg-btn";
    editBtn.title = "Edit message";
    editBtn.innerHTML = '<i class="bi bi-pencil"></i>';
    editBtn.addEventListener("click", () => {
      if (msgId) App.editUserMessage(msgId, text, el);
    });
    el.appendChild(editBtn);

    this.messagesArea.appendChild(el);
    return el;
  },

  appendAssistantMessage(markdown, msgId) {
    const el = document.createElement("div");
    el.className = "message-bubble message-assistant";
    if (msgId) el.dataset.msgId = msgId;
    const displayMarkdown = assistantDisplayMarkdown(markdown);
    const html = parseMarkdown(displayMarkdown);
    el.innerHTML = (typeof html === "string") ? html : displayMarkdown;

    // Add copy buttons to all code blocks
    Chat._addCopyButtons(el);

    // Add "Save as files" button if the response has multiple ## headings
    const headings = (markdown || "").match(/^##\s+.+/gm);
    if (headings && headings.length >= 2) {
      const bar = document.createElement("div");
      bar.className = "mt-2 pt-2 border-top border-secondary d-flex justify-content-end";
      bar.innerHTML = `
        <button class="btn btn-sm btn-outline-secondary save-as-files-btn" title="Save each section as a separate file">
          <i class="bi bi-files me-1"></i>Save as files
        </button>`;
      bar.querySelector(".save-as-files-btn").addEventListener("click", () => {
        SaveFiles.open(markdown);
      });
      el.appendChild(bar);
    }

    // Regenerate button
    const regenBar = document.createElement("div");
    regenBar.className = "d-flex justify-content-end mt-1";
    regenBar.innerHTML = `<button class="btn btn-sm btn-link text-secondary p-0 regen-btn" title="Regenerate response" style="font-size:0.78rem"><i class="bi bi-arrow-clockwise me-1"></i>Regenerate</button>`;
    regenBar.querySelector(".regen-btn").addEventListener("click", () => App.regenerateResponse());
    el.appendChild(regenBar);

    this.messagesArea.appendChild(el);
    return el;
  },

  /**
   * Create an empty assistant bubble for streaming into.
   * Returns { el, append(token), finalise() }
   */
  createStreamingBubble() {
    const el = document.createElement("div");
    el.className = "message-bubble message-assistant streaming-cursor";
    this.messagesArea.appendChild(el);
    this.scrollToBottom();

    let raw = "";
    let lastRender = 0;
    let pendingTimer = null;

    const render = () => {
      const displayMarkdown = assistantDisplayMarkdown(raw);
      const html = parseMarkdown(displayMarkdown);
      el.innerHTML = (typeof html === "string") ? html : displayMarkdown;
      lastRender = Date.now();
    };
    const flush = () => {
      if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null; }
      render();
    };

    return {
      el,
      append(token) {
        raw += token;
        const now = Date.now();
        if (now - lastRender >= STREAM_RENDER_MS) {
          flush();
        } else if (!pendingTimer) {
          pendingTimer = setTimeout(() => { pendingTimer = null; render(); }, STREAM_RENDER_MS);
        }
        Chat.scrollToBottom();
      },
      finalise() {
        el.classList.remove("streaming-cursor");
        flush();

        // Add copy buttons to all code blocks
        Chat._addCopyButtons(el);

        // Add "Save as files" button if multiple ## sections present
        const headings = raw.match(/^##\s+.+/gm);
        if (headings && headings.length >= 2) {
          const bar = document.createElement("div");
          bar.className = "mt-2 pt-2 border-top border-secondary d-flex justify-content-end";
          bar.innerHTML = `
            <button class="btn btn-sm btn-outline-secondary save-as-files-btn" title="Save each section as a separate file">
              <i class="bi bi-files me-1"></i>Save as files
            </button>`;
          bar.querySelector(".save-as-files-btn").addEventListener("click", () => {
            SaveFiles.open(raw);
          });
          el.appendChild(bar);
        }

        // Regenerate button
        const regenBar = document.createElement("div");
        regenBar.className = "d-flex justify-content-end mt-1";
        regenBar.innerHTML = `<button class="btn btn-sm btn-link text-secondary p-0 regen-btn" title="Regenerate response" style="font-size:0.78rem"><i class="bi bi-arrow-clockwise me-1"></i>Regenerate</button>`;
        regenBar.querySelector(".regen-btn").addEventListener("click", () => App.regenerateResponse());
        el.appendChild(regenBar);

        Chat.scrollToBottom();
      },
    };
  },

  appendScopeNote(meta) {
    const m = meta || {};
    const scopeLabels = {
      role: m.role ? `Model sees: ${m.role} scope` : "Model sees: role scope",
      linked_folder: "Model sees: linked workspace",
      uploads: "Model sees: uploaded files",
      none: "Model sees: chat only",
    };
    const label = scopeLabels[m.scope] || "Model sees: chat only";
    const note = document.createElement("div");
    note.className = "chat-scope-note";
    note.textContent = `${label} · ${(m.chars || 0).toLocaleString()} chars`;
    this.messagesArea.appendChild(note);

    if (m.warn) {
      const warn = document.createElement("div");
      warn.className = "chat-scope-warn";
      warn.textContent = `⚠ Large context (${(m.chars || 0).toLocaleString()} chars) — responses may be slow.`;
      this.messagesArea.appendChild(warn);
    }
    this.scrollToBottom();
    return note;
  },

  appendToolNotification(success, displayText, blockedUrl = "") {
    const wrapper = document.createElement("div");
    wrapper.className = "message-tool";

    const pill = document.createElement("span");
    pill.className = `tool-notification ${success ? "success" : "error"}`;

    // Support backtick paths without treating tool output as trusted HTML.
    String(displayText || "").split(/(`[^`]+`)/g).forEach((part) => {
      if (part.startsWith("`") && part.endsWith("`")) {
        const code = document.createElement("code");
        code.textContent = part.slice(1, -1);
        pill.appendChild(code);
      } else {
        pill.appendChild(document.createTextNode(part));
      }
    });

    wrapper.appendChild(pill);
    if (blockedUrl) {
      const approveButton = document.createElement("button");
      approveButton.className = "btn btn-sm btn-outline-primary ms-2";
      approveButton.title = "Add this URL to approved research sources";
      approveButton.innerHTML = '<i class="bi bi-shield-check me-1"></i>Approve source';
      approveButton.addEventListener("click", () => App.openResearchSourceModal(
        null,
        blockedUrl,
        () => App.regenerateResponse()
      ));
      wrapper.appendChild(approveButton);
    }
    this.messagesArea.appendChild(wrapper);
    this.scrollToBottom();
    return wrapper;
  },

  /** Rebuild the short status shown live from a persisted tool result. */
  _historicalToolNotification(toolCall, resultContent) {
    const content = String(resultContent || "");
    const failed = /^(Failed|Invalid|Unknown|No file|Script exited|Execution timed out|Path not found|Permission denied)/i.test(content);
    const fn = toolCall && toolCall.function ? toolCall.function : {};
    let args = {};
    try { args = JSON.parse(fn.arguments || "{}"); } catch (_) {}

    if (failed) {
      const firstLine = content.split("\n", 1)[0];
      return { success: false, display: firstLine.slice(0, 500) || `Tool failed: ${fn.name || "unknown"}` };
    }

    const value = (key) => String(args[key] || "");
    switch (fn.name) {
      case "read_named_file":
        return { success: true, display: `Read file: \`${value("name")}\`` };
      case "read_file":
        return { success: true, display: `Read file: \`${value("path")}\`` };
      case "list_directory":
        return { success: true, display: `Listed directory: \`${value("path")}\`` };
      case "write_file": {
        const writtenPath = content.match(/^File written to:\s*(.+)$/m);
        return { success: true, display: `File saved: \`${writtenPath ? writtenPath[1] : value("path")}\`` };
      }
      case "run_python":
        return { success: true, display: "Python executed successfully" };
      case "fetch_webpage":
        return { success: true, display: `Fetched research source: \`${value("url")}\`` };
      default:
        return { success: true, display: `Tool completed: ${fn.name || "unknown"}` };
    }
  },

  scrollToBottom() {
    this.messagesArea.scrollTop = this.messagesArea.scrollHeight;
  },

  /** Add a copy button to every <pre><code> block inside a bubble */
  _addCopyButtons(el) {
    el.querySelectorAll("pre").forEach((pre) => {
      if (pre.querySelector(".copy-code-btn")) return; // already added
      const btn = document.createElement("button");
      btn.className = "copy-code-btn";
      btn.title = "Copy code";
      btn.innerHTML = '<i class="bi bi-clipboard"></i>';
      btn.addEventListener("click", () => {
        const code = pre.querySelector("code");
        const text = code ? code.innerText : pre.innerText;
        navigator.clipboard.writeText(text).then(() => {
          btn.innerHTML = '<i class="bi bi-clipboard-check"></i>';
          setTimeout(() => { btn.innerHTML = '<i class="bi bi-clipboard"></i>'; }, 1500);
        });
      });
      pre.style.position = "relative";
      pre.appendChild(btn);
    });
  },
};
