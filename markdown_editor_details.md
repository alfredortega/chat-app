 # Markdown Document Viewing and Editing

## Goal

Allow users to open every Markdown file created by the model, view the rendered Markdown, edit the source, and save changes safely.

The implementation must support both kinds of files the current application can create:

1. Files written to the configured output directory when local file access is enabled.
2. Files stored in the conversation upload directory when local file access is disabled.

Managed project artifacts must remain subject to their existing proposal, Git, hash, and propagation rules. They must not be silently edited through the ordinary output-file path.

## Existing System

- [tools.py](tools.py) implements the model `write_file` tool and output-directory path jail.
- [app.py](app.py) contains the direct `/api/write_file` route and conversation file preview route.
- [database.py](database.py) stores conversation uploads in `ConvFile`.
- [file_handler.py](file_handler.py) extracts, previews, caches, and invalidates uploaded and linked-folder content.
- [static/js/files.js](static/js/files.js) manages conversation file attachments.
- [static/js/chat.js](static/js/chat.js) already renders Markdown with `marked`.
- [static/js/save_files.js](static/js/save_files.js) saves generated Markdown files.
- Existing project artifacts use hashes, Git, proposals, and propagation logic. They need a separate editing path.

## Recommended Architecture

Create a unified document service that exposes Markdown files from two storage backends:

### Output documents

Files located under the conversation's effective output directory:

```text
conversation output directory -> report.md
```

The effective directory must be resolved server-side from the conversation override or application setting. The browser must never choose the root directory.

### Conversation documents

Files stored in the conversation upload directory and represented by `ConvFile`.

Generated files saved while local file access is disabled already use this storage path. Editing should update the existing `ConvFile` row rather than create a new upload record.

### Managed project artifacts

Project artifacts should be detected separately. An artifact edit must use the existing project workflow:

- Mark the change as human-authored.
- Preserve the artifact hash and Git semantics.
- Show a diff where required.
- Trigger propagation only through the existing project routes and worker.

Do not let the generic document API bypass artifact review or propagation rules.

## Document Identity

Do not expose raw filesystem paths as browser identifiers. Use opaque, server-defined identifiers such as:

```text
output:report.md
upload:42
```

The backend must resolve these identifiers and validate ownership on every request.

Suggested document response:

```json
{
	"id": "output:report.md",
	"name": "report.md",
	"kind": "output",
	"path": "report.md",
	"file_id": null,
	"size_bytes": 1820,
	"modified_at": "2026-09-18T12:30:00Z",
	"content_hash": "sha256:...",
	"editable": true,
	"managed_artifact": false
}
```

## Backend API

### List documents

```text
GET /api/conversations/<conversation_id>/documents
```

Return Markdown files available to the conversation from both storage backends.

Requirements:

- Include `.md` and `.markdown` files only.
- Search only the effective output directory and the conversation's own upload records.
- Return paths relative to the allowed output directory.
- Resolve symlinks and reject files outside the allowed directory.
- Check that the conversation exists before listing.
- Deduplicate documents with the same display name.
- Mark managed project artifacts so the UI can route them differently.
- Include size, modification timestamp, hash, and editability.

### Read a document

```text
GET /api/conversations/<conversation_id>/documents/<document_id>
```

Return:

```json
{
	"id": "output:report.md",
	"name": "report.md",
	"content": "# Report\n\n...",
	"content_hash": "sha256:...",
	"modified_at": "2026-09-18T12:30:00Z",
	"size_bytes": 1820,
	"kind": "output",
	"editable": true,
	"managed_artifact": false
}
```

Reuse the existing upload extraction and preview behavior rather than duplicating file-reading logic.

The response should return raw Markdown. Rendering and sanitization belong in the browser.

### Update a document

```text
PUT /api/conversations/<conversation_id>/documents/<document_id>
```

Request:

```json
{
	"content": "# Updated report\n\n...",
	"expected_hash": "sha256:old-content"
}
```

Successful response:

```json
{
	"success": true,
	"content_hash": "sha256:new-content",
	"modified_at": "2026-09-18T12:35:00Z",
	"size_bytes": 1960
}
```

If the file changed since it was opened, return HTTP `409`:

```json
{
	"error": "document_changed",
	"message": "The file changed outside the editor.",
	"current_hash": "sha256:...",
	"current_content": "..."
}
```

## Safe File Writes

Use optimistic concurrency and atomic replacement.

Save procedure:

1. Resolve the document identifier server-side.
2. Verify conversation ownership.
3. Verify that the document is Markdown and editable.
4. Read the current file.
5. Compute its SHA-256 hash.
6. Compare it with `expected_hash`.
7. Return `409` if the hashes differ.
8. Create a temporary file in the same directory.
9. Write UTF-8 content to the temporary file.
10. Flush the file and call `fsync` where supported.
11. Replace the original with `os.replace`.
12. Recompute metadata and return the new hash.

Never write directly with `open(path, "w")` for editor saves. A process interruption must not leave a partially written document.

For conversation files, update the existing `ConvFile` metadata:

- `size_bytes`
- extracted character count
- snippet
- modification timestamp, if added

Invalidate the upload extraction cache after a successful save using the existing `evict_upload_cache()` mechanism.

## Path and Security Rules

Every document endpoint must enforce:

- Conversation existence and ownership.
- Server-side resolution of the effective output directory.
- Rejection of null bytes and malformed document IDs.
- Rejection of `..` path components.
- Rejection of absolute client paths.
- `os.path.realpath` or equivalent symlink resolution.
- `os.path.commonpath` validation against the allowed root.
- Markdown extension validation.
- Maximum request body size.
- Maximum editable file size.
- No access to files outside the conversation's allowed scope.
- Generic filesystem errors in browser responses where detailed paths would leak information.

The existing `_write_file()` behavior reduces relative paths to a basename. Keep that behavior for model file creation unless nested output paths are deliberately supported. The document API may accept a validated relative path internally, but must not bypass the output-directory jail.

## Frontend Document Panel

Extend [static/js/files.js](static/js/files.js) with a Markdown document list. Each Markdown row should offer:

- View
- Edit
- Download
- Refresh
- Delete, only if deletion is explicitly approved later

Example row:

```text
report.md        1.8 KB   modified 2 minutes ago
[View] [Edit]
```

Do not put the full editor inside the chat transcript. Open a dedicated modal or workspace pane.

## Markdown Viewer and Editor

Use a split view:

```text
┌──────────────────────┬──────────────────────┐
│ Markdown source       │ Rendered preview     │
│                      │                      │
│ # Report             │ Report               │
│                      │                      │
└──────────────────────┴──────────────────────┘
```

### Initial implementation

Use a styled `<textarea>` for source editing. This avoids adding a new editor dependency and is sufficient for Markdown editing.

Use the existing `marked` integration for preview rendering.

Controls:

- Edit
- Preview
- Save
- Cancel
- Refresh from disk
- Unsaved-change indicator
- Character and word count
- Conflict warning

### Later editor enhancement

Add CodeMirror or Monaco only if users need:

- Line numbers
- Markdown syntax highlighting
- Search
- Multiple cursors
- Folding
- Keyboard shortcut support

## Markdown Rendering Security

Raw Markdown should be returned by the server and rendered in the browser. Rendering must:

- Sanitize generated HTML.
- Avoid unsafe raw HTML by default.
- Sanitize link targets.
- Add `rel="noopener noreferrer"` to external links.
- Preserve safe code highlighting behavior already used by the application.
- Avoid placing unsanitized Markdown directly into `innerHTML`.

## Unsaved Changes and Conflicts

Track editor state in the frontend:

```javascript
{
	documentId,
	originalHash,
	currentContent,
	dirty: true
}
```

When the user closes or changes documents with unsaved content:

- Ask for confirmation.
- Do not silently discard changes.
- Preserve editor state while the modal remains open.

When a save returns `409`:

1. Keep the user's unsaved content.
2. Show the current server version.
3. Offer:
	 - Reload server version and discard local edits.
	 - Download or copy local edits.
	 - Manually merge and retry.
4. Never overwrite the newer server version automatically.

## Model-Created File Integration

The current `write_file` flow returns a success notification but does not register output files for the frontend. Add a document-created event to the chat SSE stream after a successful Markdown write:

```json
{
	"type": "document_created",
	"document": {
		"id": "output:report.md",
		"name": "report.md",
		"kind": "output"
	}
}
```

The frontend should refresh the document list when it receives this event.

For local file access disabled, the existing `/api/write_file` fallback stores the file as a conversation upload. Return the new or updated document metadata and refresh the same document panel.

## Conversation File Updates

Generated Markdown saved into the conversation upload directory should:

- Appear in the unified document list.
- Open in the same viewer/editor.
- Update the existing `ConvFile` row when edited.
- Update size, extracted character count, and snippet.
- Invalidate extracted-content cache.
- Be available to the model through `read_named_file` on the next request.

Do not create duplicate upload rows every time the user saves.

## Token-Efficient Model Integration

After an edit, the model should see the current file on its next request, but the full file should not be injected into every prompt.

Use the existing preview/index approach:

- Include filename, size, hash, and structural preview.
- Keep full content available through `read_named_file`.
- Invalidate cached extracted content after writes.
- Add a short context note after a recent edit:

```text
The user edited report.md since the previous turn. Use read_named_file if you need its complete current contents.
```

If the file is a managed project artifact, route the edit through the artifact workflow instead of this generic path.

## Optional Document Tracking Table

Dynamic output-directory discovery is sufficient for the first version. If editing becomes a major feature, add a `conversation_documents` table:

```text
conversation_documents
----------------------
id
conversation_id
kind
relative_path
display_name
created_at
updated_at
last_hash
```

This enables:

- Rename tracking
- Deleted-file detection
- Explicit document ownership
- Document history
- Better audit trails

Do not add this table unless dynamic discovery cannot support the required workflow.

## Optional Version History

### Lightweight backups

Before replacing an ordinary output file, keep a limited number of backups under a protected application directory:

```text
.agents/editor-backups/<hash>.md
```

Retain the last 5 to 10 versions and enforce a size limit.

### Git-backed history

For managed projects, use the existing Git integration:

- Record user edits as human-authored changes.
- Preserve propagation-origin metadata.
- Show a diff before propagation.
- Let the scanner detect changed artifacts.
- Keep rollback behavior intact.

Do not automatically commit ordinary output-directory edits unless the directory is already a managed project.

## Testing Plan

### Backend unit tests

Add tests for:

- Listing only `.md` and `.markdown` files.
- Output-directory traversal rejection.
- Symlink-outside-root rejection.
- Conversation ownership checks.
- Reading output and conversation Markdown files.
- Successful document update.
- Hash mismatch returning `409`.
- Atomic-write failure preserving the original file.
- Non-Markdown edit rejection.
- Oversized edit rejection.
- Upload cache invalidation.
- Correct `ConvFile` metadata updates.
- Duplicate output/upload name handling.

### API tests

Cover:

```text
GET /api/conversations/<id>/documents
GET /api/conversations/<id>/documents/<document_id>
PUT /api/conversations/<id>/documents/<document_id>
```

Run the tests with local file access enabled and disabled.

### Frontend tests

Extend [tests/test_c23_27_frontend.py](tests/test_c23_27_frontend.py) to cover:

- Document list rendering.
- View and edit actions.
- Save request payload and expected hash.
- Unsaved-change confirmation.
- Conflict handling.
- Refresh after `document_created` SSE events.
- Safe Markdown rendering.

### Manual test

1. Ask the model to create `report.md`.
2. Open the file from the document panel.
3. Verify rendered Markdown.
4. Switch to source editing.
5. Modify and save the file.
6. Ask the model to review the file.
7. Confirm it sees the edited version.
8. Edit the file outside the application.
9. Attempt to save an older browser version.
10. Confirm that a conflict dialog appears.
11. Repeat with local file access disabled.
12. Test a managed project artifact separately and confirm its review workflow is preserved.

## Delivery Sequence

### Milestone 1: View generated Markdown

- Add safe output-directory Markdown discovery.
- Add unified document read and list APIs.
- Add document list UI.
- Add rendered Markdown preview.
- Emit document refresh events after model writes.

### Milestone 2: Edit safely

- Add hash-based optimistic concurrency.
- Add atomic writes.
- Add split editor and preview UI.
- Add save, cancel, refresh, and unsaved-change handling.
- Support both output files and conversation uploads.

### Milestone 3: Integrate project artifacts

- Detect managed artifacts separately.
- Route artifact edits through project APIs.
- Preserve Git and scanner semantics.
- Mark edits as human-authored.
- Show diffs and require existing review rules.
- Trigger propagation only after explicit save.

### Milestone 4: History and polish

- Add editor backups or version history.
- Add download and rename operations.
- Add syntax highlighting and keyboard shortcuts if needed.
- Add recent-edit context notifications without injecting full files.

## Recommended First Scope

The smallest useful implementation is:

1. A unified document resolver.
2. List and read endpoints.
3. A Markdown document panel.
4. A rendered preview modal.
5. A textarea-based editor.
6. Hash-based conflict detection.
7. Atomic saves.
8. Cache invalidation.
9. Focused backend and frontend tests.

The most important design boundary is the distinction between ordinary output Markdown and managed project artifacts. Ordinary files can use the generic document editor; managed artifacts must continue through their existing review, Git, and propagation workflows.
