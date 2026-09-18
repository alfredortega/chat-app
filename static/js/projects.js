/**
 * projects.js — managed-project panel (C23, read-only)
 *
 * Artifact status board grouped by role with a roll-up status (D16), read-only
 * propagation-mode / token-budget status, pending artifact requests (D17),
 * open questions and an unresolved-assumption count (D20).
 *
 * "Check for changes" scans the workspace for human-authored BA edits and, when
 * it finds any, automatically runs the propagation wave (all affected-artifact
 * rewrites) for each detected change, polling until the waves reach a terminal
 * state.
 */

const ProjectPanel = {
  _open: false,
  _artifactsCollapsed: true,
  _checkingChanges: false,
  _checkComplete: false,
  _workingArtifactKeys: new Set(),

  /** Open the panel for a project id. */
  async open(projectId) {
    this._projectId = projectId;
    this._open = true;
    const overlay = document.getElementById("projectPanelOverlay");
    const bodyEl = document.getElementById("projectPanelContainer");
    const titleEl = document.getElementById("projectPanelTitle");
    if (!overlay || !bodyEl) return;

    overlay.classList.remove("d-none");
    bodyEl.innerHTML = `<div class="d-flex align-items-center gap-2 text-secondary py-3">
      <span class="spinner-border spinner-border-sm" aria-hidden="true"></span>
      Loading project…
    </div>`;
    if (titleEl) titleEl.textContent = "Project #" + projectId;

    // Preserve any in-progress answers already typed into the question boxes so
    // a re-render (e.g. after answering one question) doesn't wipe the others,
    // and keep the scroll position so the panel doesn't jump to the top.
    const drafts = {};
    bodyEl.querySelectorAll("textarea[data-issue-id]").forEach((t) => {
      if (t.value.trim()) drafts[t.dataset.issueId] = t.value;
    });
    const scrollTop = bodyEl.scrollTop;

    // Fetch everything independently so one failing endpoint can't blank the
    // panel. Defaults keep rendering safe when data is missing.
    const settled = await Promise.allSettled([
      API.getProjectArtifacts(projectId),
      API.getProjectSettings(projectId),
      API.getProjectIssues(projectId),
      API.getProjectAssumptions(projectId),
      API.listArtifactRequests(projectId),
    ]);
    const [data, settings, issues, assumptions, requests] = settled.map((r) =>
      r.status === "fulfilled" ? (r.value || {}) : {}
    );
    const errors = settled.filter((r) => r.status === "rejected").map((r) => r.reason);

    // Map artifact keys -> display role so long-running propagation can show
    // "<Role> Assessing Changes…" on the button.
    this._artifactRoles = this._artifactRoles || {};
    (data.artifacts || []).forEach((a) => {
      if (a.artifact_key) this._artifactRoles[a.artifact_key] = a.role || a.artifact_key;
    });

    bodyEl.innerHTML = this._render(data, settings, issues, assumptions, requests, errors);
    Object.entries(drafts).forEach(([issueId, value]) => {
      const t = bodyEl.querySelector(`textarea[data-issue-id="${issueId}"]`);
      if (t && !t.value.trim()) t.value = value;
    });
    bodyEl.scrollTop = scrollTop;
    this._wireScanButton(bodyEl);
    this._wireIssueControls(bodyEl);
    this._wireRequestControls(bodyEl);
    this._wireArtifactsToggle(bodyEl);
  },

  _wireRequestControls(el) {
    el.querySelectorAll("[data-approve-request]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const requestId = Number(btn.dataset.approveRequest);
        btn.disabled = true;
        try {
          await API.approveArtifactRequest(this._projectId, requestId);
        } catch (err) {
          alert("Failed to approve request: " + err.message);
          return;
        }
        ProjectPanel.open(this._projectId);
      });
    });
    el.querySelectorAll("[data-reject-request]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const requestId = Number(btn.dataset.rejectRequest);
        btn.disabled = true;
        try {
          await API.rejectArtifactRequest(this._projectId, requestId);
        } catch (err) {
          alert("Failed to reject request: " + err.message);
          return;
        }
        ProjectPanel.open(this._projectId);
      });
    });
  },

  _wireArtifactsToggle(el) {
    const header = el.querySelector("#artifactsToggleHeader");
    const body = el.querySelector("#artifactsToggleBody");
    if (!header || !body) return;
    header.addEventListener("click", () => {
      this._artifactsCollapsed = !this._artifactsCollapsed;
      const icon = header.querySelector(".artifacts-toggle-icon");
      if (icon) icon.className = `bi bi-chevron-${this._artifactsCollapsed ? "right" : "down"} artifacts-toggle-icon`;
      body.classList.toggle("d-none", this._artifactsCollapsed);
    });
  },

  _wireIssueControls(el) {
    el.querySelectorAll("[data-answer-issue]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const issueId = Number(btn.dataset.answerIssue);
        const textarea = el.querySelector(`textarea[data-issue-id="${issueId}"]`);
        const answer = (textarea ? textarea.value : "").trim();
        if (!answer) { alert("Type an answer first."); return; }
        try {
          await API.answerIssue(this._projectId, issueId, { answer });
        } catch (err) {
          alert("Failed to save answer: " + err.message);
          return;
        }
        ProjectPanel.open(this._projectId);
      });
    });
    el.querySelectorAll("[data-dismiss-issue]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const issueId = Number(btn.dataset.dismissIssue);
        const note = (el.querySelector(`textarea[data-issue-id="${issueId}"]`) || {}).value || "";
        try {
          await API.dismissIssue(this._projectId, issueId, { note });
        } catch (err) {
          alert("Failed to dismiss: " + err.message);
          return;
        }
        ProjectPanel.open(this._projectId);
      });
    });
  },

  _wireScanButton(el) {
    const btn = el.querySelector("#btnCheckChanges");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      let checkCompleted = false;
      this._checkingChanges = true;
      this._checkComplete = false;
      this._workingArtifactKeys.clear();
      this._updateCheckProgress();
      try {
        const report = await API.scanProject(this._projectId);
        let events = (report && report.change_events) || [];
        let retrying = false;
        if (!events.length) {
          // A previous wave may have failed after the BA update itself was
          // applied. In that case the scan sees no new BA delta, but its stale
          // downstream jobs must still be retried.
          const changes = await API.getProjectChanges(this._projectId);
          const retryEvent = (changes || []).find((change) =>
            (change.jobs || []).some((job) =>
              ["failed", "needs_input"].includes(job.state)
            )
          );
          if (retryEvent) {
            events = [retryEvent];
            retrying = true;
          }
        }
        if (!events.length) {
          const stale = (report && report.stale_artifacts) ? report.stale_artifacts.length : 0;
          alert(`Scan complete. No BA changes detected.${stale ? ` ${stale} artifact(s) marked stale.` : ""}`);
          checkCompleted = true;
          return;
        }

        // Kick off propagation for every detected change event.
        const startedChangeIds = [];
        const startFailures = [];
        for (const ev of events) {
          if (!ev || !ev.id) continue;
          try {
            const res = await API.runPropagation(this._projectId, ev.id);
            if (res && res.started !== false) startedChangeIds.push(ev.id);
            else startFailures.push(`Change #${ev.id} did not start.`);
          } catch (err) {
            startFailures.push(`Change #${ev.id}: ${err.message || err}`);
          }
        }

        // Do not wait for a wave that the server rejected. Previously, a 400 or
        // 409 was swallowed here and the button polled for up to ten minutes.
        if (!startedChangeIds.length) {
          alert(`Scan complete. Propagation did not start. ${startFailures.join(" ")}`);
          return;
        }

        // Poll all change waves until every job is terminal (or timeout).
        const wavesDone = await this._waitForWaves(
          this._projectId,
          startedChangeIds,
          {
            pollMs: 3000,
            onProgress: (jobs) => this._showWorkingArtifacts(jobs),
          }
        );

        const stale = (report && report.stale_artifacts) ? report.stale_artifacts.length : 0;
        let summary = `${retrying ? "Retried" : "Found"} ${events.length} change event(s); propagation ${startedChangeIds.length} started.`;
        if (startFailures.length) summary += ` ${startFailures.join(" ")}`;
        if (wavesDone) summary += " Propagation complete.";
        else summary += " Propagation still running — reopen the panel shortly.";
        if (stale) summary += ` ${stale} artifact(s) marked stale.`;
        alert(`Scan complete. ${summary}`);
        checkCompleted = wavesDone;
      } catch (err) {
        alert("Scan failed: " + err.message);
      } finally {
        this._checkingChanges = false;
        this._checkComplete = checkCompleted;
        this._workingArtifactKeys.clear();
        ProjectPanel.open(this._projectId);
      }
    });
  },

  /** Show spinners only for artifacts whose agents are actively running. */
  _showWorkingArtifacts(jobs) {
    this._workingArtifactKeys = new Set(
      (jobs || [])
        .filter((job) => job.state === "running" && job.artifact_key)
        .map((job) => job.artifact_key)
    );
    this._updateCheckProgress();
  },

  _updateCheckProgress() {
    const panel = document.getElementById("projectPanelContainer");
    if (!panel) return;
    const btn = panel.querySelector("#btnCheckChanges");
    if (btn) {
      btn.disabled = this._checkingChanges;
      btn.innerHTML = this._checkingChanges
        ? '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Checking for changes'
        : '<i class="bi bi-arrow-repeat me-1"></i>Check for changes';
    }
    panel.querySelectorAll("[data-artifact-status]").forEach((statusEl) => {
      const artifactKey = statusEl.closest("[data-artifact]")?.dataset.artifact;
      statusEl.innerHTML = this._workingArtifactKeys.has(artifactKey)
        ? this._workingStatus()
        : this._statusBadge(statusEl.dataset.status);
    });
    panel.querySelectorAll("[data-role-status]").forEach((statusEl) => {
      const role = statusEl.closest("[data-role]")?.dataset.role;
      const isWorking = [...this._workingArtifactKeys].some(
        (artifactKey) => this._artifactRoles[artifactKey] === role
      );
      statusEl.innerHTML = isWorking
        ? this._workingStatus()
        : this._statusBadge(statusEl.dataset.status);
    });
  },

  _workingStatus() {
    return '<span class="spinner-border spinner-border-sm" role="status" aria-label="Working"></span>';
  },

  /**
   * Poll several change waves until all their jobs are terminal, or the
   * timeout elapses. A single long wave can take minutes, so this returns
   * early with ``false`` once the ceiling is reached.
   *
   * ``opts.onProgress(jobs)`` is invoked after every poll with the full list
   * of relevant jobs so callers can surface "which agent is working now".
   */
  async _waitForWaves(projectId, changeIds, opts = {}) {
    const pollMs    = opts.pollMs    || 3000;
    const timeoutMs = opts.timeoutMs || 10 * 60 * 1000; // 10 min ceiling
    const terminal  = ["applied", "proposed", "failed", "needs_input", "cancelled", "completed", "rejected"];
    const ids = new Set((changeIds || []).map((n) => Number(n)));
    if (!ids.size) return true;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, pollMs));
      try {
        const changes = (await API.getProjectChanges(projectId)) || [];
        const relevant = changes.filter((c) => ids.has(Number(c.id)));
        const jobs = relevant.flatMap((c) => c.jobs || []);
        if (opts.onProgress) opts.onProgress(jobs);
        if (!relevant.length) continue; // not claimed yet — keep polling
        if (!jobs.length) return false; // successful starts always create jobs
        if (jobs.length && jobs.every((j) => terminal.includes(j.state))) {
          return true;
        }
      } catch (_) { /* transient — keep polling */ }
    }
    return false;
  },

  close() {
    this._open = false;
    const overlay = document.getElementById("projectPanelOverlay");
    if (overlay) overlay.classList.add("d-none");
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

  _render(data, settings, issues, assumptions, requests, errors) {
    const rows = this._groupByRole(data.artifacts || []);
    const openQuestions = (issues || []).filter((i) => i.status === "open" && i.kind === "question");
    const errorHtml = (errors || []).length
      ? `<div class="alert alert-warning py-2 small mb-2">Some sections failed to load:
          ${errors.map((e) => `<span class="d-block">${_esc(e.message || e)}</span>`).join("")}
        </div>`
      : "";

    const roleHtml = rows.map((row) => `
      <div class="project-role-group" data-role="${_esc(row.role)}">
        <div class="project-role-header">
          <span class="project-role-name">${row.role}</span>
          <span data-role-status data-status="${_esc(row.rollup)}">${this._workingArtifactKeys.size && row.artifacts.some((a) => this._workingArtifactKeys.has(a.artifact_key)) ? this._workingStatus() : this._statusBadge(row.rollup)}</span>
        </div>
        ${row.artifacts.map((a) => `
          <div class="project-artifact-row" data-artifact="${a.artifact_key}">
            <span class="project-artifact-key">${a.artifact_key}</span>
            <span data-artifact-status data-status="${_esc(a.status)}">${this._workingArtifactKeys.has(a.artifact_key) ? this._workingStatus() : this._statusBadge(a.status)}</span>
            ${a.status === "current" && a.error ? " — " + a.error : ""}
          </div>`).join("")}
      </div>`).join("");

    const issueHtml = (openQuestions || []).map((i) => `
      <div class="project-issue-row">
        <div class="project-issue-question">
          <span class="${i.blocking ? "issue-blocking" : "issue-assumption"}">${i.blocking ? "blocking" : "assumption"}</span>
          <span>${i.req_id || "general"}</span>
          <span class="text-secondary small fw-semibold">from ${_esc(i.raised_by_key || "agent")}</span>
          <span class="text-secondary small">${_esc(i.body || "")}</span>
          ${i.proposed_answer ? `<span class="text-secondary small fst-italic">Proposed: ${_esc(String(i.proposed_answer))}</span>` : ""}
        </div>
        <textarea class="form-control form-control-sm border-secondary my-1" rows="2"
                  data-issue-id="${i.id}" placeholder="Type your answer…"></textarea>
        <div class="d-flex gap-1">
          <button type="button" class="btn btn-sm btn-outline-success" data-answer-issue="${i.id}">
            <i class="bi bi-check2 me-1"></i>Answer
          </button>
          <button type="button" class="btn btn-sm btn-outline-secondary" data-dismiss-issue="${i.id}">
            <i class="bi bi-x me-1"></i>Not a requirement — dismiss
          </button>
        </div>
      </div>`).join("");

    const assumptionHtml = this._renderAssumptions(assumptions);
    const needsInputHtml = (openQuestions.length || assumptions.unresolved_count)
      ? `${issueHtml}${assumptionHtml}`
      : "<div class='text-secondary small'>Nothing needs your input right now.</div>";

    const requestHtml = (requests || []).filter((r) => r.status === "pending").map((r) => `
      <div class="project-request-row" data-request-id="${r.id}">
        <div class="d-flex flex-wrap gap-1 flex-grow-1">
          <span class="project-request-key">${r.artifact_key}</span>
          <span class="text-secondary small">${r.rel_path}</span>
          <span class="text-secondary small">${r.rationale || ""}</span>
        </div>
        <div class="d-flex gap-1">
          <button type="button" class="btn btn-sm btn-outline-success" data-approve-request="${r.id}">
            <i class="bi bi-check2 me-1"></i>Approve
          </button>
          <button type="button" class="btn btn-sm btn-outline-danger" data-reject-request="${r.id}">
            <i class="bi bi-x me-1"></i>Reject
          </button>
        </div>
      </div>`).join("") || "<div class='text-secondary small'>No pending artifact requests.</div>";

    return `
      <div class="project-panel">
        ${errorHtml}
        <div class="project-summary d-flex gap-2 my-2 flex-wrap">
          <span class="badge bg-info text-dark">Mode: ${_esc(settings.propagation_mode || "off")}</span>
          <span class="badge bg-info text-dark">Token budget: ${_esc(settings.token_budget || "unlimited")}</span>
          <span class="badge bg-warning text-dark">Unresolved assumptions: ${assumptions.unresolved_count || 0}</span>
          <span class="badge bg-secondary">Open questions: ${openQuestions.length}</span>
           <button type="button" class="btn btn-sm btn-outline-primary" id="btnCheckChanges">
             ${this._checkingChanges
               ? '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Checking for changes'
               : '<i class="bi bi-arrow-repeat me-1"></i>Check for changes'}
           </button>
           ${this._checkComplete ? '<span class="text-success small fw-semibold"><i class="bi bi-check-square-fill me-1" aria-hidden="true"></i>Done!</span>' : ""}
        </div>

        <h6 class="mt-3">Needs your input</h6>
        ${needsInputHtml}

        <h6 class="mt-3">Pending artifact requests</h6>
        ${requestHtml}

        <h6 class="mt-3 d-flex align-items-center gap-1" id="artifactsToggleHeader" style="cursor:pointer">
          <i class="bi bi-chevron-${this._artifactsCollapsed ? "right" : "down"} artifacts-toggle-icon"></i>
          <span>Artifacts (grouped by role)</span>
        </h6>
        <div id="artifactsToggleBody" class="${this._artifactsCollapsed ? "d-none" : ""}">
          ${roleHtml}
        </div>
      </div>`;
  },

  _renderAssumptions(assumptions) {
    const byArtifact = assumptions.by_artifact || {};
    const keys = Object.keys(byArtifact);
    if (!keys.length) return "";
    return keys.map((key) => `
      <div class="project-assumption-group">
        <span class="issue-assumption">assumed</span>
        <strong>${_esc(key)}</strong>: ${byArtifact[key].length} unresolved assumption(s)
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
