/**
 * diff_view.js — unified-diff renderer for the review modal (C24).
 *
 * No external diff library is loaded; the diff is rendered from plain unified
 * diff text. Colours are theme-safe (CSS classes live in style.css and work
 * in both themes).
 */

const DiffView = {
  /** Parse unified diff text into grouped line objects. */
  _parse(diffText) {
    if (!diffText) return [];
    const hunks = [];
    let current = null;
    diffText.split(/\r?\n/).forEach((raw) => {
      const line = raw;
      if (line.startsWith("@@")) {
        current = { header: line, lines: [] };
        hunks.push(current);
        return;
      }
      if (current) {
        current.lines.push({
          type: line.startsWith("+") ? "add" : (line.startsWith("-") ? "del" : "ctx"),
          text: line,
        });
      }
    });
    return hunks;
  },

  /**
   * Render a diff string into HTML for the review modal.
   * @param {string} diffText unified diff text
   * @param {string} title optional heading (e.g., artifact key)
   */
  render(diffText, title = "") {
    const hunks = this._parse(diffText || "");
    if (!hunks.length) {
      return `<div class="diff-empty text-secondary small">No diff available.</div>`;
    }
    const body = hunks.map((h) => `
      <div class="diff-hunk">
        <div class="diff-header">${h.header}</div>
        ${h.lines.map((l) => `<div class="diff-line diff-${l.type}">${this._escape(l.text)}</div>`).join("")}
      </div>`).join("");
    return `<div class="diff-view">
      ${title ? `<div class="diff-title">${title}</div>` : ""}
      ${body}
    </div>`;
  },

  _escape(text) {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  },
};

window.DiffView = DiffView;