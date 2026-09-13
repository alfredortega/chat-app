/**
 * projects.js — managed-project panel (C23, read-only)
 *
 * Artifact status board grouped by role with a roll-up status (D16), change
 * history, read-only propagation-mode / token-budget status, pending artifact
 * requests (D17), open questions and an unresolved-assumption count (D20).
 */

const ProjectPanel = {
  _open: false,

  /** Open the panel for a project id. */
  async open(projectId) {
    const data = await API.getProjectArtifacts(projectId);
    const changes = await API.getProjectChanges(projectId);
    const settings = await API.getProjectSettings(projectId);
    const issues = await API.getProjectIssues(projectId);
    const assumptions = await API.getProjectAssumptions(projectId);
    const requests = await API.listArtifactRequests(projectId);
    let conversations = [];
    try {
      conversations = (await API.listConversations()).filter((c) => c.folder_id === projectId);
    } catch (_) { /* panel still opens without the inbox list */ }

    this._projectId = projectId;
    this._open = true;
    const el = document.getElementById("projectPanelContainer");
    if (!el) return;
    el.innerHTML = this._render(data, changes, settings, issues, assumptions, requests, conversations);
    this._wireInboxControls(el, conversations, settings);
    el.classList.remove("d-none");
  },

  _wireInboxControls(el, conversations, settings) {
    const btn = el.querySelector("#btnSetBaInbox");
    const sel = el.querySelector("#baInboxSelect");
    if (!btn || !sel) return;
    btn.addEventListener("click", async () => {
      const convId = Number(sel.value);
      if (!convId) return;
      try {
        await API.setBaConversation(this._projectId, convId);
        if (typeof Folders !== "undefined" && Folders.list) {
          const folder = Folders.list.find((f) => Number(f.id) === Number(this._projectId));
          if (folder) folder.ba_conversation_id = convId;
        }
        await Folders.load();
        ProjectPanel.open(this._projectId);
      } catch (err) {
        alert("Failed to set BA inbox: " + err.message);
      }
    });
  },

  close() {
    this._open = false;
    const el = document.getElementById("projectPanelContainer");
    if (el) el.classList.add("d-none");
  },

  /** Group artifacts by role and compute a per-role roll-up status. */
  _groupByRole(artifacts) {
    const groups = {};
    artifacts.forEach((a) => {
      const role = a.role || a.role_persona_id || "Unknown role";
      if (!groups[role]) groups[role] = [];
      groups[role].push(a);
    });
    const priority = { conflict: 0, stale: 1, updating: 2, needs_review: 3, blocked: 4, current: 5 };
    Object.values(groups).forEach((arr) => {
      arr.sort((x, y) => x.artifact_key.localeCompare(y.artifact_key));
    });
    const rows = Object.entries(groups).map(([role, arr]) => {
      const worst = arr.reduce((acc, a) => {
        const p = priority[a.status];
        return p < priority[acc.status] ? a : acc;
      }, arr[0]);
      return { role, artifacts: arr, rollup: worst.status };
    });
    rows.sort((a, b) => a.role.localeCompare(b.role));
    return rows;
  },

  _statusBadge(status) {
    const cls = {
      current: "status-current",
      stale: "status-stale",
      updating: "status-updating",
      conflict: "status-conflict",
      needs_review: "status-needs-review",
      blocked: "status-blocked",
    }[status] || "status-current";
    return `<span class="status-badge ${cls}">${status}</span>`;
  },

  _render(data, changes, settings, issues, assumptions, requests, conversations) {
    const rows = this._groupByRole(data.artifacts || []);
    const openQuestions = (issues || []).filter((i) => i.status === "open" && i.kind === "question");

    const currentInbox = settings.ba_conversation_id;
    const inboxOptions = (conversations || []).map((c) =>
      `<option value="${c.id}" ${Number(c.id) === Number(currentInbox) ? "selected" : ""}>${_esc(c.title)}</option>`
    ).join("") || `<option value="">No conversations in this project</option>`;
    const conversationHtml = (conversations || []).length
      ? `<select id="baInboxSelect" class="form-select form-select-sm border-secondary mb-1">
           ${inboxOptions}
         </select>
         <button type="button" class="btn btn-sm btn-outline-secondary" id="btnSetBaInbox">
           <i class="bi bi-inbox me-1"></i>Set as BA inbox
         </button>`
      : `<div class="text-secondary small">No conversations in this project yet.</div>`;

    const roleHtml = rows.map((row) => `
      <div class="project-role-group">
        <div class="project-role-header">
          <span class="project-role-name">${row.role}</span>
          ${this._statusBadge(row.rollup)}
        </div>
        ${row.artifacts.map((a) => `
          <div class="project-artifact-row" data-artifact="${a.artifact_key}">
            <span class="project-artifact-key">${a.artifact_key}</span>
            ${this._statusBadge(a.status)}
            ${a.status === "current" && a.error ? " — " + a.error : ""}
          </div>`).join("")}
      </div>`).join("");

    const changeHtml = (changes || []).map((c) => {
      const jobs = c.jobs || [];
      const done = jobs.filter((j) =>
        ["completed", "applied", "proposed", "failed", "needs_input", "cancelled", "rejected"].includes(j.state)
      ).length;
      const jobLine = jobs.length
        ? `<span class="project-change-jobs">${done}/${jobs.length} jobs done</span>`
        : "";
      const reqs = (c.changed_reqs || []).join(", ");
      return `
      <div class="project-change-row">
        <span class="project-change-id">#${c.id}</span>
        <span class="project-change-summary">${(c.summary || "?").slice(0, 120)}</span>
        ${reqs ? `<span class="project-change-reqs">${reqs}</span>` : ""}
        ${jobLine}
      </div>`;
    }).join("") || "<div class='text-secondary small'>No change events yet.</div>";

    const issueHtml = (openQuestions || []).map((i) => `
      <div class="project-issue-row">
        <span class="${i.blocking ? "issue-blocking" : "issue-assumption"}">${i.blocking ? "blocking" : "assumption"}</span>
        <span>${i.req_id || "general"}</span>
        <span class="text-secondary small">${(i.body || "").slice(0, 80)}</span>
      </div>`).join("") || "<div class='text-secondary small'>No open questions.</div>";

    const requestHtml = (requests || []).filter((r) => r.status === "pending").map((r) => `
      <div class="project-request-row" data-request-id="${r.id}">
        <span class="project-request-key">${r.artifact_key}</span>
        <span class="text-secondary small">${r.rel_path}</span>
        <span class="text-secondary small">${r.rationale || ""}</span>
      </div>`).join("") || "<div class='text-secondary small'>No pending artifact requests.</div>";

    return `
      <div class="project-panel">
        <div class="project-panel-header d-flex justify-content-between align-items-center">
          <h5 class="mb-0">Project #${data.project_id}</h5>
          <button type="button" class="btn-close" onclick="ProjectPanel.close()"></button>
        </div>

        <div class="project-summary d-flex gap-2 my-2 flex-wrap">
          <span class="badge bg-info text-dark">Mode: ${settings.propagation_mode}</span>
          <span class="badge bg-info text-dark">Token budget: ${settings.token_budget || "unlimited"}</span>
          <span class="badge bg-warning text-dark">Unresolved assumptions: ${assumptions.unresolved_count || 0}</span>
          <span class="badge bg-secondary">Open questions: ${openQuestions.length}</span>
        </div>

        <h6 class="mt-3">Question inbox (BA)</h6>
        ${conversationHtml}

        <h6 class="mt-3">Artifacts (grouped by role)</h6>
        ${roleHtml}

        <h6 class="mt-3">Change history</h6>
        ${changeHtml}

        <h6 class="mt-3">Open questions</h6>
        ${issueHtml}

        <h6 class="mt-3">Pending artifact requests</h6>
        ${requestHtml}

        <h6 class="mt-3">Unresolved assumptions by artifact</h6>
        ${this._renderAssumptions(assumptions)}
      </div>`;
  },

  _renderAssumptions(assumptions) {
    const byArtifact = assumptions.by_artifact || {};
    const keys = Object.keys(byArtifact);
    if (!keys.length) return "<div class='text-secondary small'>None.</div>";
    return keys.map((key) => `
      <div class="project-assumption-group">
        <strong>${key}</strong>: ${byArtifact[key].length} assumption(s)
      </div>`).join("");
  },
};

// Expose for inline callbacks in the DOM.
window.ProjectPanel = ProjectPanel;

/**
 * ReviewModal — C24. Opens a proposal diff in the review modal and wires the
 * Approve / Reject buttons to the backend.
 */
const ReviewModal = {
  _projectId: null,
  _jobId: null,

  async open(projectId, changeId, proposal) {
    this._projectId = projectId;
    this._jobId = proposal ? proposal.job_id : null;

    const titleEl = document.getElementById("reviewModalTitle");
    const diffEl = document.getElementById("reviewModalDiff");
    if (titleEl) titleEl.textContent = `Review proposal — ${proposal ? proposal.artifact_key : ""}`;
    if (diffEl) diffEl.innerHTML = DiffView.render(proposal ? proposal.diff : "", proposal ? proposal.artifact_key : "");

    const approveBtn = document.getElementById("btnReviewApprove");
    const rejectBtn = document.getElementById("btnReviewReject");

    const clear = () => {
      if (approveBtn) approveBtn.onclick = null;
      if (rejectBtn) rejectBtn.onclick = null;
      const modalEl = document.getElementById("reviewModal");
      if (modalEl) {
        const modal = bootstrap.Modal.getInstance(modalEl);
        if (modal) modal.hide();
      }
    };

    if (approveBtn) {
      approveBtn.onclick = async () => {
        if (!this._jobId) return;
        try {
          await API.applyProposal(this._projectId, this._jobId);
          clear();
          ProjectPanel.open(this._projectId);
        } catch (err) {
          alert("Approve failed: " + err.message);
        }
      };
    }
    if (rejectBtn) {
      rejectBtn.onclick = async () => {
        if (!this._jobId) return;
        try {
          await API.rejectProposal(this._projectId, this._jobId);
          clear();
          ProjectPanel.open(this._projectId);
        } catch (err) {
          alert("Reject failed: " + err.message);
        }
      };
    }

    const modalEl = document.getElementById("reviewModal");
    if (modalEl) {
      const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
      modal.show();
    }
  },
};

window.ReviewModal = ReviewModal;