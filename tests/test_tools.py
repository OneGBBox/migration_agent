"""
tests/test_tools.py

Unit tests for migration_tools.py.
All tools are pure Python (no LLM calls), so no mocking is needed.

Run with: uv run pytest tests/test_tools.py -v
"""

import pytest

from migration_tools import (
    ReadFileTool,
    WriteFileTool,
    ListFilesTool,
    RunCommandTool,
    ReadMultipleFilesTool,
    get_developer_tools,
    get_tester_tools,
    get_critic_tools,
)


# ──────────────────────────────────────────────
# ReadFileTool
# ──────────────────────────────────────────────

class TestReadFileTool:
    def test_reads_existing_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("Hello World", encoding="utf-8")
        result = ReadFileTool()._run(str(f))
        assert result == "Hello World"

    def test_returns_error_for_missing_file(self):
        result = ReadFileTool()._run("/nonexistent/path/file.txt")
        assert result.startswith("ERROR: File not found")

    def test_reads_unicode_content(self, tmp_path):
        f = tmp_path / "unicode.txt"
        f.write_text("Hello 你好", encoding="utf-8")
        result = ReadFileTool()._run(str(f))
        assert "Hello 你好" in result

    def test_reads_multiline_content(self, tmp_path):
        f = tmp_path / "multi.cs"
        f.write_text("line1\nline2\nline3", encoding="utf-8")
        result = ReadFileTool()._run(str(f))
        assert "line1" in result
        assert "line3" in result


# ──────────────────────────────────────────────
# WriteFileTool
# ──────────────────────────────────────────────

class TestWriteFileTool:
    def test_writes_file_content(self, tmp_path):
        path = tmp_path / "output.txt"
        result = WriteFileTool()._run(str(path), "test content")
        assert "SUCCESS" in result
        assert path.read_text(encoding="utf-8") == "test content"

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "a" / "b" / "c" / "file.cs"
        result = WriteFileTool()._run(str(path), "nested content")
        assert "SUCCESS" in result
        assert path.exists()

    def test_overwrites_existing_file(self, tmp_path):
        path = tmp_path / "existing.txt"
        path.write_text("old content", encoding="utf-8")
        WriteFileTool()._run(str(path), "new content")
        assert path.read_text(encoding="utf-8") == "new content"

    def test_success_message_contains_path(self, tmp_path):
        path = tmp_path / "Program.cs"
        result = WriteFileTool()._run(str(path), "content")
        assert "Program.cs" in result


# ──────────────────────────────────────────────
# ListFilesTool
# ──────────────────────────────────────────────

class TestListFilesTool:
    def test_lists_all_files_in_directory(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.cs").write_text("b")
        result = ListFilesTool()._run(str(tmp_path))
        assert "a.txt" in result
        assert "b.cs" in result

    def test_filters_by_extension(self, tmp_path):
        (tmp_path / "controller.cs").write_text("cs")
        (tmp_path / "config.json").write_text("json")
        result = ListFilesTool()._run(str(tmp_path), extension=".cs")
        assert "controller.cs" in result
        assert "config.json" not in result

    def test_lists_files_recursively(self, tmp_path):
        sub = tmp_path / "Controllers"
        sub.mkdir()
        (sub / "HomeController.cs").write_text("nested")
        result = ListFilesTool()._run(str(tmp_path))
        assert "HomeController.cs" in result

    def test_returns_error_for_missing_directory(self):
        result = ListFilesTool()._run("/nonexistent/directory/xyz")
        assert result.startswith("ERROR: Directory not found")

    def test_returns_no_files_message_for_empty_dir(self, tmp_path):
        result = ListFilesTool()._run(str(tmp_path))
        assert result == "No files found."

    def test_extension_filter_excludes_wrong_suffix(self, tmp_path):
        (tmp_path / "view.cshtml").write_text("html")
        (tmp_path / "model.cs").write_text("cs")
        result = ListFilesTool()._run(str(tmp_path), extension=".cshtml")
        assert "view.cshtml" in result
        assert "model.cs" not in result


# ──────────────────────────────────────────────
# RunCommandTool
# ──────────────────────────────────────────────

class TestRunCommandTool:
    def test_blocks_disallowed_command_rm(self):
        result = RunCommandTool()._run("rm -rf /")
        assert "BLOCKED" in result

    def test_blocks_python_command(self):
        result = RunCommandTool()._run("python --version")
        assert "BLOCKED" in result

    def test_blocks_powershell_command(self):
        result = RunCommandTool()._run("powershell Get-Process")
        assert "BLOCKED" in result

    def test_allows_echo_command(self):
        result = RunCommandTool()._run("echo hello_world")
        assert "hello_world" in result
        assert "EXIT CODE: 0" in result

    def test_output_contains_exit_code(self):
        result = RunCommandTool()._run("echo test")
        assert "EXIT CODE" in result

    def test_blocked_message_lists_allowed_commands(self):
        result = RunCommandTool()._run("curl http://example.com")
        assert "dotnet" in result  # blocked msg shows allowed list


# ──────────────────────────────────────────────
# ReadMultipleFilesTool
# ──────────────────────────────────────────────

class TestReadMultipleFilesTool:
    def test_reads_multiple_files(self, tmp_path):
        f1 = tmp_path / "file1.txt"
        f2 = tmp_path / "file2.txt"
        f1.write_text("content_one", encoding="utf-8")
        f2.write_text("content_two", encoding="utf-8")
        result = ReadMultipleFilesTool()._run([str(f1), str(f2)])
        assert "content_one" in result
        assert "content_two" in result

    def test_handles_missing_file_gracefully(self, tmp_path):
        f1 = tmp_path / "exists.txt"
        f1.write_text("real content", encoding="utf-8")
        result = ReadMultipleFilesTool()._run([str(f1), "/nonexistent/missing.txt"])
        assert "real content" in result
        assert "NOT FOUND" in result

    def test_empty_list_returns_empty_string(self):
        result = ReadMultipleFilesTool()._run([])
        assert result == ""

    def test_output_includes_file_path_header(self, tmp_path):
        f = tmp_path / "MyFile.cs"
        f.write_text("class Foo {}", encoding="utf-8")
        result = ReadMultipleFilesTool()._run([str(f)])
        assert "MyFile.cs" in result


# ──────────────────────────────────────────────
# Tool getter functions
# ──────────────────────────────────────────────

class TestToolGetters:
    def test_developer_tools_has_five_tools(self):
        assert len(get_developer_tools()) == 5

    def test_tester_tools_has_five_tools(self):
        assert len(get_tester_tools()) == 5

    def test_critic_tools_has_three_tools(self):
        assert len(get_critic_tools()) == 3

    def test_critic_tools_exclude_write_file(self):
        names = [t.name for t in get_critic_tools()]
        assert "write_file" not in names

    def test_critic_tools_exclude_run_command(self):
        names = [t.name for t in get_critic_tools()]
        assert "run_command" not in names

    def test_developer_tools_include_write_file(self):
        names = [t.name for t in get_developer_tools()]
        assert "write_file" in names

    def test_developer_tools_include_run_command(self):
        names = [t.name for t in get_developer_tools()]
        assert "run_command" in names

    def test_critic_tools_are_all_read_tools(self):
        names = [t.name for t in get_critic_tools()]
        for name in names:
            assert "read" in name or "list" in name
