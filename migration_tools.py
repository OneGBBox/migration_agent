"""
tools/migration_tools.py

Custom CrewAI tools for the migration agents.

KEY CHANGE from 0.28.8 → 1.9.3:
  BaseTool now imported from crewai.tools (NOT langchain.tools)
  Everything else — _run, args_schema, name, description — is identical.
"""

import os
import subprocess
from pathlib import Path
from typing import ClassVar, Optional, Type

from crewai.tools import BaseTool          # ← crewai.tools, not langchain.tools
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Input Schemas (Pydantic v2 — unchanged)
# ──────────────────────────────────────────────

class ReadFileInput(BaseModel):
    file_path: str = Field(
        description="Absolute or relative path to the file to read")


class WriteFileInput(BaseModel):
    file_path: str = Field(description="Path where the file should be written")
    content: str = Field(description="Content to write into the file")


class ListFilesInput(BaseModel):
    directory: str = Field(description="Directory path to list files from")
    extension: Optional[str] = Field(
        default=None, description="Filter by extension e.g. .cs .cshtml")


class RunCommandInput(BaseModel):
    command: str = Field(
        description="Shell command to run (dotnet CLI commands only)")
    working_dir: Optional[str] = Field(
        default=".", description="Working directory for the command")


class ReadMultipleFilesInput(BaseModel):
    file_paths: list[str] = Field(
        description="List of file paths to read at once")


class FileEntry(BaseModel):
    path: str = Field(description="File path to write")
    content: str = Field(description="Full content of the file")


class WriteBatchFilesInput(BaseModel):
    files: list[FileEntry] = Field(
        description=(
            "List of files to write, each with 'path' and 'content' fields. "
            "Example: [{'path': './output/App/Program.cs', 'content': '...'}, "
            "{'path': './output/App/appsettings.json', 'content': '...'}]"
        )
    )


# ──────────────────────────────────────────────
# Tool 1: Read File
# ──────────────────────────────────────────────

class ReadFileTool(BaseTool):
    name: str = "read_file"
    description: str = (
        "Reads the full contents of a single file. "
        "Use to read legacy ASP.NET MVC files: controllers, models, views, Web.config, .csproj."
    )
    args_schema: Type[BaseModel] = ReadFileInput

    def _run(self, file_path: str) -> str:
        try:
            path = Path(file_path)
            if not path.exists():
                return f"ERROR: File not found: {file_path}"
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"ERROR reading file: {str(e)}"


# ──────────────────────────────────────────────
# Tool 2: Write File
# ──────────────────────────────────────────────

class WriteFileTool(BaseTool):
    name: str = "write_file"
    description: str = (
        "Writes content to a file. Creates parent directories automatically if missing. "
        "Use to write all migrated .NET Core 8 files: Program.cs, controllers, views, "
        "appsettings.json, DbContext, .csproj."
    )
    args_schema: Type[BaseModel] = WriteFileInput

    def _run(self, file_path: str, content: str) -> str:
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"SUCCESS: Written → {file_path}"
        except Exception as e:
            return f"ERROR writing file: {str(e)}"


# ──────────────────────────────────────────────
# Tool 3: List Files
# ──────────────────────────────────────────────

class ListFilesTool(BaseTool):
    name: str = "list_files"
    description: str = (
        "Lists all files in a directory recursively. "
        "Optionally filter by file extension (e.g. '.cs', '.cshtml', '.json'). "
        "Use to discover what exists in the legacy project or the migrated output."
    )
    args_schema: Type[BaseModel] = ListFilesInput

    def _run(self, directory: str, extension: Optional[str] = None) -> str:
        try:
            base = Path(directory)
            if not base.exists():
                return f"ERROR: Directory not found: {directory}"
            files = list(base.rglob("*"))
            if extension:
                files = [f for f in files if f.suffix == extension]
            result = [f.as_posix() for f in files if f.is_file()]
            return "\n".join(result) if result else "No files found."
        except Exception as e:
            return f"ERROR listing files: {str(e)}"


# ──────────────────────────────────────────────
# Tool 4: Run Command (dotnet CLI only)
# ──────────────────────────────────────────────

class RunCommandTool(BaseTool):
    name: str = "run_command"
    description: str = (
        "Runs a dotnet CLI or basic shell command. "
        "Allowed: 'dotnet new mvc', 'dotnet build', 'dotnet test', 'dotnet run', "
        "'ls', 'find', 'cat', 'echo'. "
        "Returns stdout and stderr from the command."
    )
    args_schema: Type[BaseModel] = RunCommandInput

    ALLOWED_PREFIXES: ClassVar[list[str]] = ["dotnet", "cat", "ls", "find", "echo"]

    def _run(self, command: str, working_dir: str = ".") -> str:
        first_token = command.strip().split()[0]
        if first_token not in self.ALLOWED_PREFIXES:
            return (
                f"BLOCKED: '{first_token}' is not in the allowed commands list. "
                f"Allowed: {', '.join(self.ALLOWED_PREFIXES)}"
            )
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=working_dir,
                timeout=60,
            )
            parts = []
            if result.stdout:
                parts.append(f"STDOUT:\n{result.stdout.strip()}")
            if result.stderr:
                parts.append(f"STDERR:\n{result.stderr.strip()}")
            parts.append(f"EXIT CODE: {result.returncode}")
            return "\n".join(parts)
        except subprocess.TimeoutExpired:
            return "ERROR: Command timed out after 60 seconds."
        except Exception as e:
            return f"ERROR running command: {str(e)}"


# ──────────────────────────────────────────────
# Tool 5: Read Multiple Files at Once
# ──────────────────────────────────────────────

class ReadMultipleFilesTool(BaseTool):
    name: str = "read_multiple_files"
    description: str = (
        "Reads multiple files in a single call. Returns each file path followed by its content. "
        "More efficient than calling read_file repeatedly. "
        "Use when you need to read all controllers, all views, or all models at once."
    )
    args_schema: Type[BaseModel] = ReadMultipleFilesInput

    def _run(self, file_paths: list[str]) -> str:
        results = []
        for fp in file_paths:
            path = Path(fp)
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="replace")
                results.append(f"\n{'='*60}\nFILE: {fp}\n{'='*60}\n{content}")
            else:
                results.append(f"\nFILE: {fp} — NOT FOUND")
        return "\n".join(results)


# ──────────────────────────────────────────────
# Tool 6: Write Multiple Files in One Call
# ──────────────────────────────────────────────

class WriteBatchFilesTool(BaseTool):
    name: str = "write_batch_files"
    description: str = (
        "Writes ALL files for the migrated project in a single call. "
        "Pass a dict of {file_path: content} for every file. "
        "Creates parent directories automatically. "
        "PREFER this over calling write_file repeatedly — one call instead of many."
    )
    args_schema: Type[BaseModel] = WriteBatchFilesInput

    def _run(self, files: list) -> str:
        results = []
        for entry in files:
            # Handle both FileEntry instances (from CrewAI validation)
            # and plain dicts (from direct test calls or raw agent output)
            if isinstance(entry, dict):
                file_path = entry.get("path", "")
                content = entry.get("content", "")
            else:
                file_path = entry.path
                content = entry.content
            try:
                path = Path(file_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                results.append(f"SUCCESS: Written → {file_path}")
            except Exception as e:
                results.append(f"ERROR writing {file_path}: {str(e)}")
        written = sum(1 for r in results if r.startswith("SUCCESS"))
        results.append(f"\nTotal: {written}/{len(files)} files written successfully.")
        return "\n".join(results)


# ──────────────────────────────────────────────
# Convenience getters — called from agents.py
# ──────────────────────────────────────────────

def get_developer_tools() -> list:
    """Developer needs read + write + batch write + dotnet CLI to scaffold and write files."""
    return [
        ReadFileTool(),
        ReadMultipleFilesTool(),
        WriteFileTool(),
        WriteBatchFilesTool(),
        ListFilesTool(),
        RunCommandTool(),
    ]


def get_tester_tools() -> list:
    """Tester needs read + write (for test files) + dotnet test runner."""
    return [
        ReadFileTool(),
        ReadMultipleFilesTool(),
        WriteFileTool(),
        WriteBatchFilesTool(),
        ListFilesTool(),
        RunCommandTool(),
    ]


def get_critic_tools() -> list:
    """Critic is read-only — reviews code but never modifies anything."""
    return [
        ReadFileTool(),
        ReadMultipleFilesTool(),
        ListFilesTool(),
    ]
