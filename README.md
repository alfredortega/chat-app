# My Chat App

A rich web-based chat application built on a Python Flask and SQLite backend, with a highly interactive vanilla JavaScript sidebar and chat interface.

## Core Features

- **API Endpoints:** Connect to multiple hosted (OpenAI, Gemini) or local (LMStudio, Ollama) providers.
- **Personas:** Steer responses with specialized prompts like Python Developer, Technical Writer, or DevSecOps Engineer.
- **Sidebar Organization:** Create, rename, delete, and import/export folders to keep conversations structured.
- **File Reference:** Live-link folders or upload individual files (PDFs, Docx, spreadsheets, CSV, JSON/YAML, text) to provide background context.
- **Saving Outputs:** Automatically save generated code and content splits directly to local output paths.

## Archiving Support (New)

The application now supports archiving both folders and individual conversations to keep the active workspace clean and organized.

### Key Archive Behaviors:
1. **Sidebar Toggles:** A "Show archived" switch is located right below the search box in the sidebar to toggle the visibility of archived folders and conversations.
2. **Visual Styling:** Archived items are displayed with a reduced opacity (`0.5`) and a distinct *Archived* badge next to their names so they are easy to tell apart from active ones.
3. **Full Sync Archiving:**
   - Archiving a folder will automatically archive all the conversations inside it.
   - Unarchiving a folder will automatically unarchive all of its conversations.
4. **Standalone Unarchiving:** If a conversation is inside an archived folder and you individually unarchive it, it will automatically be moved to the **Unsorted** section so it is visible in your active workspace.
5. **Search Isolation:** When searching through past messages, archived conversations are ignored by the backend query to ensure your results remain focused and noise-free.

## Getting Started

1. **Setup the Virtual Environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Run the Application:**
   ```bash
   python app.py
   ```
   Open your browser and navigate to `http://localhost:5000`.
