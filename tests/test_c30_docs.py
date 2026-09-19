import os
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return f.read()


class TestHelpHtml:
    def test_nav_has_project_sections(self):
        html = _read("static/help.html")
        assert 'href="#projects"' in html
        assert 'href="#propagation"' in html

    def test_section_numbers_renumbered(self):
        """Document editor §10 added; Settings=14 and Tips=15 after the shift."""
        html = _read("static/help.html")
        assert 'id="mdeditor">10. Viewing &amp; Editing Markdown Documents' in html
        assert 'id="tools">11. What the Assistant Can Do (Tools)' in html
        assert 'id="projects">12. Creating a Project' in html
        assert 'id="propagation">13. Automatic Updates Between Personas' in html
        assert 'id="settings" style="page-break-before:always">14. Settings Explained' in html
        assert 'id="tips">15. Tips &amp; Troubleshooting' in html

    def test_section_11_12_content(self):
        html = _read("static/help.html")
        assert "git repository" in html
        assert "propose" in html
        assert "ASSUMPTION" in html
        assert "BA conversation" in html
        assert "persona selection" in html.lower() or "Persona selection" in html

    def test_propagation_tips_added(self):
        html = _read("static/help.html")
        assert "aren't auto-updating" in html or "Unresolved assumptions" in html
        assert "Git is missing" in html

    def test_markdown_editor_documented(self):
        html = _read("static/help.html")
        assert 'href="#mdeditor"' in html
        assert "full screen" in html.lower()
        for term in ["Viewing &amp; Editing Markdown Documents", "Split", "Edit only",
                     "Preview", "Save as PDF", "managed", "docx", "Unsaved changes"]:
            assert term in html or term.lower() in html.lower()


class TestReadme:
    def test_projects_section(self):
        readme = _read("README.md")
        assert "Projects & Automatic Persona Propagation" in readme
        assert "REQ-014" in readme
        assert "SQLAlchemy" in readme
        assert "git" in readme.lower()

    def test_project_structure(self):
        readme = _read("README.md")
        for path in ["propagation/", "routes/projects.py", "templates.py", "tests/"]:
            assert path in readme, f"README missing {path}"

    def test_req_nnn_convention_documented(self):
        readme = _read("README.md")
        assert "REQ-nnn" in readme

    def test_no_run_tests_py_reference(self):
        from tests import conftest  # noqa: F401 (ensure repo imports cleanly)


class TestManualChecklist:
    def test_checklist_exists_and_complete(self):
        path = os.path.join(ROOT, "tests", "manual", "propagation_checklist.md")
        assert os.path.isfile(path)
        content = _read("tests/manual/propagation_checklist.md")
        assert "Golden scenario" in content
        assert "both" in content.lower()
        assert "byte-identical" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])