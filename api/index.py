"""
api/index.py

FastAPI entrypoint — local dev + Vercel showcase.

Endpoints:
  GET  /                    → Landing page (HTML)
  GET  /health              → Health check
  GET  /api/info            → Project metadata (JSON)
  POST /api/migrate         → Start a migration job (202, returns job_id instantly)
  GET  /api/stream/{job_id} → SSE log stream (real-time agent output)
  GET  /api/status/{job_id} → Poll job status + final result
  GET  /api/jobs            → List all jobs (dev)

The CrewAI pipeline is blocking and runs 5-20 minutes.
It runs in a ThreadPoolExecutor so HTTP stays responsive.
Logs are captured from sys.stdout + logging module via a queue,
then streamed to the browser via Server-Sent Events.
"""

import asyncio
import json
import logging
import queue
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

# ── Add project root to sys.path so main.py imports work ─────────────────────
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── In-memory job store ───────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}

# Thread pool — 2 concurrent migration jobs max
_executor = ThreadPoolExecutor(max_workers=2)

# Sentinel value pushed to queue when a job finishes
_STREAM_END = "__STREAM_END__"

app = FastAPI(
    title="MigrationAgenticCrew.NET",
    description="AI-powered ASP.NET Web Forms → .NET Core 8 migration agent",
    version="1.0.0",
)


# ── Log capture utilities ─────────────────────────────────────────────────────

class _QueueLogHandler(logging.Handler):
    """Feeds Python logging records into a queue.Queue."""

    def __init__(self, log_queue: queue.Queue) -> None:
        super().__init__()
        self.q = log_queue
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait(self.format(record))
        except Exception:  # noqa: BLE001
            pass


class _StdoutCapture:
    """
    Wraps sys.stdout/stderr so every write() is:
      1. forwarded to the original stream (terminal still shows output)
      2. put line-by-line into a queue.Queue for SSE streaming
    """

    def __init__(self, log_queue: queue.Queue, original) -> None:
        self.q = log_queue
        self._orig = original
        self._buf = ""

    def write(self, text: str) -> int:
        self._orig.write(text)
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            stripped = line.rstrip()
            if stripped:
                self.q.put_nowait(stripped)
        return len(text)

    def flush(self) -> None:
        self._orig.flush()
        if self._buf.strip():
            self.q.put_nowait(self._buf.rstrip())
            self._buf = ""

    def isatty(self) -> bool:
        return False

    # Forward everything else (fileno, etc.) to original
    def __getattr__(self, name: str):
        return getattr(self._orig, name)


# ── Background migration worker ───────────────────────────────────────────────

def _suppress_signal_in_thread() -> None:
    """
    CrewAI's telemetry tries to register SIGTERM/SIGINT handlers, but
    signal.signal() only works on the main thread.  Monkey-patch it to
    silently ignore ValueError when called from a worker thread so the
    'Cannot register SIGTERM handler' warning doesn't appear.
    """
    import signal as _signal
    _orig = _signal.signal

    def _safe_signal(signalnum, handler):
        try:
            return _orig(signalnum, handler)
        except ValueError:
            pass  # not in main thread — ignore silently

    _signal.signal = _safe_signal


def _run_migration_sync(job_id: str, legacy_path: str, output_path: str) -> None:
    """
    Runs in a thread-pool worker.
    Captures stdout + logging and routes every line to the job's log queue.
    """
    # Must be called before any CrewAI import to suppress signal.signal errors
    _suppress_signal_in_thread()

    job = _jobs[job_id]
    log_q: queue.Queue = job["log_queue"]
    job["status"] = "running"

    # ── Install stdout/stderr capture ───────────────────────────────────────
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    sys.stdout = _StdoutCapture(log_q, orig_stdout)
    sys.stderr = _StdoutCapture(log_q, orig_stderr)

    # ── Install logging capture ──────────────────────────────────────────────
    log_handler = _QueueLogHandler(log_q)
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)

    try:
        from main import run_migration  # noqa: PLC0415
        from checkpoint import CheckpointManager  # noqa: PLC0415
        from config.settings import load_config as _load_config  # noqa: PLC0415
        try:
            _cfg = _load_config(legacy_path_override=legacy_path, output_path_override=output_path)
            _cp_dir = _cfg.checkpoint_dir
        except Exception:
            _cp_dir = "./output/.checkpoints"
        checkpoint = CheckpointManager(checkpoint_dir=_cp_dir)
        result = run_migration(legacy_path, output_path, checkpoint)
        job["status"] = "complete"
        job["result"] = result
    except Exception as exc:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = str(exc)
        log_q.put_nowait(f"[ERROR] {exc}")
    finally:
        # ── Restore stdout/stderr + logging ─────────────────────────────────
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr
        root_logger.removeHandler(log_handler)
        job["ended_at"] = time.time()
        log_q.put_nowait(_STREAM_END)


# ── API request / response models ─────────────────────────────────────────────

class MigrateRequest(BaseModel):
    legacy_path: str = "./legacy_sample"
    output_path: Optional[str] = None   # None → auto-derive from .sln name


class JobStatus(BaseModel):
    job_id: str
    status: str          # queued | running | complete | failed
    legacy_path: str
    output_path: str
    started_at: float
    ended_at: Optional[float] = None
    elapsed_seconds: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_response(job_id: str) -> JobStatus:
    j = _jobs[job_id]
    elapsed = None
    if j.get("ended_at"):
        elapsed = round(j["ended_at"] - j["started_at"], 1)
    elif j["status"] == "running":
        elapsed = round(time.time() - j["started_at"], 1)
    return JobStatus(
        job_id=job_id,
        status=j["status"],
        legacy_path=j["legacy_path"],
        output_path=j["output_path"],
        started_at=j["started_at"],
        ended_at=j.get("ended_at"),
        elapsed_seconds=elapsed,
        result=j.get("result"),
        error=j.get("error"),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/api/migrate", response_model=JobStatus, status_code=202)
async def start_migration(req: MigrateRequest):
    """Start a migration job. Returns 202 immediately with job_id."""
    job_id = str(uuid.uuid4())

    output_path = req.output_path
    if not output_path:
        try:
            from config.settings import load_config  # noqa: PLC0415
            cfg = load_config(legacy_path_override=req.legacy_path)
            output_path = cfg.output_project_path
        except Exception:  # noqa: BLE001
            output_path = "./output/MigratedApp"

    _jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "legacy_path": req.legacy_path,
        "output_path": output_path,
        "started_at": time.time(),
        "ended_at": None,
        "result": None,
        "error": None,
        "log_queue": queue.Queue(),
    }

    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, _run_migration_sync, job_id, req.legacy_path, output_path)

    return _build_response(job_id)


@app.get("/api/stream/{job_id}")
async def stream_logs(job_id: str):
    """
    SSE endpoint. Streams log lines from the job's queue in real-time.
    Each event is a JSON object: { type, text } or { type: 'done' }.
    """
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    async def generator():
        log_q: queue.Queue = _jobs[job_id]["log_queue"]
        loop = asyncio.get_event_loop()

        # Send initial connected event
        yield f"data: {json.dumps({'type': 'connected', 'job_id': job_id})}\n\n"

        while True:
            try:
                # Wait up to 1 s for a log line (non-blocking on event loop)
                line = await loop.run_in_executor(
                    None, lambda: log_q.get(timeout=1.0)
                )
                if line == _STREAM_END:
                    status = _jobs[job_id]["status"]
                    result = _jobs[job_id].get("result") or _jobs[job_id].get("error") or ""
                    yield f"data: {json.dumps({'type': 'done', 'status': status, 'result': result})}\n\n"
                    break
                # Strip ANSI escape codes before sending
                clean = _strip_ansi(line)
                if clean:
                    yield f"data: {json.dumps({'type': 'log', 'text': clean})}\n\n"

            except queue.Empty:
                # Heartbeat keeps the connection alive; also detect stuck jobs
                status = _jobs[job_id]["status"]
                if status in ("complete", "failed"):
                    # Drain any remaining lines
                    while True:
                        try:
                            line = log_q.get_nowait()
                            if line != _STREAM_END:
                                clean = _strip_ansi(line)
                                if clean:
                                    yield f"data: {json.dumps({'type': 'log', 'text': clean})}\n\n"
                        except queue.Empty:
                            break
                    result = _jobs[job_id].get("result") or _jobs[job_id].get("error") or ""
                    yield f"data: {json.dumps({'type': 'done', 'status': status, 'result': result})}\n\n"
                    break
                yield ": heartbeat\n\n"  # SSE comment — keeps TCP alive

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


import re as _re
_ANSI_RE = _re.compile(r"\x1b\[[0-9;]*[mGKHF]")

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text).strip()


@app.get("/api/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return _build_response(job_id)


@app.get("/api/jobs")
async def list_jobs():
    return [_build_response(jid) for jid in _jobs]


@app.get("/health")
async def health():
    return {"status": "ok", "project": "MigrationAgenticCrew.NET", "active_jobs": len(_jobs)}


@app.get("/api/info")
async def info():
    return {
        "name": "MigrationAgenticCrew.NET",
        "description": "Multi-agent ASP.NET Web Forms → .NET Core 8 migration agent",
        "stack": {"language": "Python 3.12", "framework": "CrewAI 1.9.3",
                  "llm": "OpenAI GPT-4o via LiteLLM", "target": ".NET Core 8 MVC"},
        "agents": ["Developer", "Tester", "Critic", "Manager"],
        "tasks": ["Analyze", "Migrate", "Test", "Review", "Report"],
        "tests": 111,
    }


# ── Landing page ──────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>MigrationAgenticCrew.NET</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         background:#0f172a;color:#e2e8f0;min-height:100vh;
         display:flex;flex-direction:column;align-items:center;padding:48px 24px}
    .badge{background:#1e3a5f;border:1px solid #3b82f6;color:#93c5fd;
           padding:4px 14px;border-radius:999px;font-size:13px;margin-bottom:24px}
    h1{font-size:clamp(28px,5vw,52px);font-weight:800;text-align:center;
       background:linear-gradient(135deg,#60a5fa,#a78bfa,#34d399);
       -webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:16px}
    .subtitle{color:#94a3b8;font-size:18px;text-align:center;
              max-width:620px;line-height:1.6;margin-bottom:48px}

    /* ── Pipeline steps ── */
    .pipeline{display:flex;flex-wrap:wrap;justify-content:center;
              align-items:center;gap:8px;margin-bottom:48px}
    .step{background:#1e293b;border:1px solid #334155;border-radius:12px;
          padding:14px 18px;text-align:center;min-width:110px;transition:all .3s}
    .step.active{border-color:#3b82f6;background:#1e3a5f;box-shadow:0 0 16px #3b82f633}
    .step.done{border-color:#34d399;background:#0d3329}
    .step.failed{border-color:#f87171;background:#3b1a1a}
    .step .icon{font-size:22px;margin-bottom:5px}
    .step .label{font-size:12px;font-weight:600;color:#cbd5e1}
    .step .sub{font-size:10px;color:#64748b;margin-top:3px}
    .step.active .label{color:#93c5fd}
    .step.done .label{color:#34d399}
    .arrow{color:#475569;font-size:18px}

    /* ── Run panel ── */
    .run-panel{background:#1e293b;border:1px solid #334155;border-radius:16px;
               padding:32px;max-width:820px;width:100%;margin-bottom:48px}
    .run-panel h2{font-size:17px;font-weight:700;margin-bottom:20px;color:#f1f5f9}
    .fields{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}
    @media(max-width:600px){.fields{grid-template-columns:1fr}}
    .field label{display:block;font-size:12px;color:#94a3b8;margin-bottom:6px}
    .field input{width:100%;background:#0f172a;border:1px solid #334155;
                 border-radius:8px;padding:10px 14px;color:#e2e8f0;font-size:14px;outline:none}
    .field input:focus{border-color:#3b82f6}
    .run-btn{width:100%;background:#3b82f6;color:white;border:none;border-radius:10px;
             padding:12px;font-size:15px;font-weight:600;cursor:pointer;transition:background .2s}
    .run-btn:hover{background:#2563eb}
    .run-btn:disabled{background:#334155;cursor:not-allowed;color:#64748b}

    /* ── Agent status bar ── */
    .agent-bar{display:none;margin-top:18px;padding:12px 16px;background:#0f172a;
               border:1px solid #1e293b;border-radius:10px;font-size:13px}
    .agent-bar.visible{display:flex;align-items:center;gap:10px}
    .agent-dot{width:8px;height:8px;border-radius:50%;background:#3b82f6;
               animation:pulse 1.2s ease-in-out infinite}
    @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
    .agent-name{color:#93c5fd;font-weight:700}
    .agent-task{color:#94a3b8;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .elapsed{color:#475569;font-size:11px;margin-left:auto;white-space:nowrap}

    /* ── Terminal log ── */
    .terminal-wrap{display:none;margin-top:16px}
    .terminal-wrap.visible{display:block}
    .terminal-header{background:#1e293b;border:1px solid #334155;border-radius:10px 10px 0 0;
                     padding:8px 16px;display:flex;align-items:center;gap:8px}
    .term-dot{width:12px;height:12px;border-radius:50%}
    .term-header-label{font-size:12px;color:#64748b;margin-left:4px;flex:1}
    .term-clear{font-size:11px;color:#475569;cursor:pointer;padding:2px 8px;
                border:1px solid #334155;border-radius:4px;background:none;color:#94a3b8}
    .term-clear:hover{border-color:#64748b}
    .terminal{background:#030712;border:1px solid #1e293b;border-top:none;
              border-radius:0 0 10px 10px;font-family:'Cascadia Code','Fira Code',
              'Consolas',monospace;font-size:12px;padding:14px 16px;
              height:420px;overflow-y:auto;line-height:1.55}
    .log-default{color:#94a3b8}
    .log-agent{color:#f472b6;font-weight:700}
    .log-task{color:#60a5fa;font-weight:600}
    .log-thought{color:#93c5fd}
    .log-action{color:#a78bfa}
    .log-action-input{color:#7c3aed}
    .log-observation{color:#6ee7b7}
    .log-answer{color:#fbbf24;font-weight:600}
    .log-error{color:#f87171}
    .log-success{color:#34d399;font-weight:700}
    .log-section{color:#64748b}
    .log-tool{color:#d946ef}
    .log-build{color:#fb923c}
    .log-warning{color:#f59e0b}

    /* ── Result box ── */
    .result-box{display:none;margin-top:16px;padding:16px;background:#0f172a;
                border:1px solid #334155;border-radius:10px}
    .result-box.visible{display:block}
    .result-box h3{font-size:13px;font-weight:700;margin-bottom:10px;color:#34d399}
    .result-pre{white-space:pre-wrap;word-break:break-word;color:#cbd5e1;
                font-size:12px;font-family:monospace;max-height:300px;overflow-y:auto}

    /* ── Feature cards ── */
    .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
           gap:20px;max-width:900px;width:100%;margin-bottom:48px}
    .card{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:24px}
    .card h3{font-size:15px;font-weight:700;margin-bottom:12px;color:#f1f5f9}
    .card ul{list-style:none}
    .card ul li{font-size:13px;color:#94a3b8;padding:4px 0}
    .card ul li::before{content:"→ ";color:#3b82f6}
    .tech{display:flex;flex-wrap:wrap;justify-content:center;gap:10px;margin-bottom:40px}
    .tech span{background:#1e293b;border:1px solid #334155;border-radius:8px;
               padding:6px 14px;font-size:13px;color:#cbd5e1}
    footer{margin-top:48px;color:#475569;font-size:13px}
  </style>
</head>
<body>

<div class="badge">⚡ Powered by CrewAI + GPT-4o</div>
<h1>MigrationAgenticCrew.NET</h1>
<p class="subtitle">
  An autonomous multi-agent AI system that migrates legacy
  <strong style="color:#f1f5f9">ASP.NET Web Forms</strong> projects to
  <strong style="color:#f1f5f9">.NET Core 8 MVC</strong> — end-to-end, without human intervention.
</p>

<!-- ── Pipeline progress ── -->
<div class="pipeline" id="pipeline">
  <div class="step" id="s0"><div class="icon">🔍</div><div class="label">Analyze</div><div class="sub">Developer</div></div>
  <div class="arrow">→</div>
  <div class="step" id="s1"><div class="icon">⚙️</div><div class="label">Migrate</div><div class="sub">Developer</div></div>
  <div class="arrow">→</div>
  <div class="step" id="s2"><div class="icon">🧪</div><div class="label">Test</div><div class="sub">Tester</div></div>
  <div class="arrow">→</div>
  <div class="step" id="s3"><div class="icon">🔎</div><div class="label">Review</div><div class="sub">Critic</div></div>
  <div class="arrow">→</div>
  <div class="step" id="s4"><div class="icon">📋</div><div class="label">Report</div><div class="sub">Manager</div></div>
</div>

<!-- ── Run panel ── -->
<div class="run-panel">
  <h2>🚀 Run a Migration</h2>
  <div class="fields">
    <div class="field">
      <label>Legacy project path (relative to repo root)</label>
      <input id="legacyPath" type="text" value="./legacy_sample"/>
    </div>
    <div class="field">
      <label>Output path (blank = auto-derive from .sln name)</label>
      <input id="outputPath" type="text" placeholder="e.g. ./output/MyApp  (optional)"/>
    </div>
  </div>
  <button class="run-btn" id="runBtn" onclick="startMigration()">▶ Start Migration</button>

  <!-- Agent status bar -->
  <div class="agent-bar" id="agentBar">
    <div class="agent-dot" id="agentDot"></div>
    <span class="agent-name" id="agentName">—</span>
    <span class="agent-task" id="agentTask"></span>
    <span class="elapsed" id="elapsedLabel"></span>
  </div>

  <!-- Live terminal -->
  <div class="terminal-wrap" id="termWrap">
    <div class="terminal-header">
      <div class="term-dot" style="background:#ef4444"></div>
      <div class="term-dot" style="background:#f59e0b"></div>
      <div class="term-dot" style="background:#22c55e"></div>
      <span class="term-header-label" id="termLabel">Agent Output</span>
      <button class="term-clear" onclick="clearTerminal()">Clear</button>
    </div>
    <div class="terminal" id="terminal"></div>
  </div>

  <!-- Result box -->
  <div class="result-box" id="resultBox">
    <h3>📄 Migration Report</h3>
    <pre class="result-pre" id="resultPre"></pre>
  </div>
</div>

<!-- ── Feature cards ── -->
<div class="cards">
  <div class="card">
    <h3>🤖 Agentic AI Features</h3>
    <ul>
      <li>4 specialized CrewAI agents</li>
      <li>5-task sequential pipeline</li>
      <li>Auto-retry on INCOMPLETE</li>
      <li>Sliding-window rate limiter</li>
      <li>Generic discovery-based migration</li>
      <li>One-call batch file writing</li>
    </ul>
  </div>
  <div class="card">
    <h3>🧠 What Gets Migrated</h3>
    <ul>
      <li>.aspx / .aspx.cs → Controllers + Views</li>
      <li>ADO.NET → EF Core 8 DbContext</li>
      <li>Web.config → appsettings.json</li>
      <li>Global.asax → Program.cs</li>
      <li>packages.config → .csproj</li>
      <li>Html helpers → Tag helpers</li>
    </ul>
  </div>
  <div class="card">
    <h3>🛡️ Quality Gates</h3>
    <ul>
      <li>xUnit tests (7 per controller)</li>
      <li>EF Core InMemory test DB</li>
      <li>dotnet build verification</li>
      <li>Critic scores 0–100</li>
      <li>Manager COMPLETE / INCOMPLETE</li>
      <li>111 Python unit tests</li>
    </ul>
  </div>
  <div class="card">
    <h3>⚙️ Output Generated</h3>
    <ul>
      <li>.NET 8 solution (.sln)</li>
      <li>Controllers (async CRUD)</li>
      <li>Razor Views (Bootstrap 5)</li>
      <li>EF Core DbContext + Models</li>
      <li>xUnit test project</li>
      <li>MIGRATION_REPORT.md</li>
    </ul>
  </div>
</div>

<div class="tech">
  <span>Python 3.12</span><span>CrewAI 1.9.3</span><span>GPT-4o</span>
  <span>LiteLLM</span><span>pydantic-settings</span><span>uv</span>
  <span>.NET 8 SDK</span><span>xUnit</span><span>EF Core 8</span>
</div>

<footer>Built with CrewAI + OpenAI GPT-4o &nbsp;|&nbsp; ASP.NET Web Forms → .NET Core 8</footer>

<script>
// ── State ────────────────────────────────────────────────────────────────────
let _currentStep = -1;
let _sse = null;
let _elapsedTimer = null;
let _startTime = 0;
let _logCount = 0;

// ── Start ─────────────────────────────────────────────────────────────────────
async function startMigration() {
  const legacyPath = document.getElementById('legacyPath').value.trim();
  const outputPath = document.getElementById('outputPath').value.trim();
  const btn = document.getElementById('runBtn');

  resetUI();
  btn.disabled = true;
  btn.textContent = '⏳ Submitting...';
  showTerminal();
  appendLog('── Submitting migration job...', 'log-section');

  try {
    const body = { legacy_path: legacyPath };
    if (outputPath) body.output_path = outputPath;
    const res = await fetch('/api/migrate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const job = await res.json();
    appendLog(`── Job ID: ${job.job_id}`, 'log-section');
    appendLog('── Connecting to agent stream...', 'log-section');
    connectSSE(job.job_id);
  } catch (err) {
    appendLog(`[ERROR] Failed to start: ${err.message}`, 'log-error');
    btn.textContent = '▶ Try Again';
    btn.disabled = false;
  }
}

// ── SSE connection ────────────────────────────────────────────────────────────
function connectSSE(jobId) {
  if (_sse) _sse.close();
  _sse = new EventSource(`/api/stream/${jobId}`);
  _startTime = Date.now();
  startElapsedTimer();

  _sse.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }

    if (msg.type === 'connected') {
      document.getElementById('runBtn').textContent = '🤖 Agents running...';
      setAgentBar('Starting...', 'Initializing pipeline');
    } else if (msg.type === 'log') {
      processLogLine(msg.text);
    } else if (msg.type === 'done') {
      finishJob(msg.status, msg.result);
    }
  };

  _sse.onerror = () => {
    appendLog('[WARN] Stream disconnected — checking status...', 'log-warning');
    _sse.close();
    setTimeout(() => pollFallback(jobId), 3000);
  };
}

// Fallback: poll /api/status if SSE drops
async function pollFallback(jobId) {
  try {
    const res = await fetch(`/api/status/${jobId}`);
    const job = await res.json();
    if (job.status === 'complete' || job.status === 'failed') {
      finishJob(job.status, job.result || job.error || '');
    } else {
      setTimeout(() => pollFallback(jobId), 4000);
    }
  } catch {}
}

// ── Log line parser ───────────────────────────────────────────────────────────
function processLogLine(text) {
  // Detect agent transitions
  const agentMatch = text.match(/[Ww]orking [Aa]gent[:\s]+(.+)/);
  if (agentMatch) {
    const name = agentMatch[1].trim().replace(/=+/g, '').trim();
    setAgentBar(name, '');
    advancePipelineFromAgent(name);
    appendLog(`══ Agent: ${name}`, 'log-agent');
    return;
  }

  // Detect task start
  const taskMatch = text.match(/[Ss]tarting [Tt]ask[:\s]+(.+)/);
  if (taskMatch) {
    const desc = taskMatch[1].replace(/=+/g, '').trim().substring(0, 100);
    setAgentTask(desc + (taskMatch[1].length > 100 ? '…' : ''));
    appendLog(`── Task: ${desc}`, 'log-task');
    advancePipelineFromTask(desc);
    return;
  }

  // Color code by content
  const t = text.trim();
  let cls = 'log-default';

  if (/^={3,}/.test(t) || /^-{3,}/.test(t))             cls = 'log-section';
  else if (/^Thought:/i.test(t))                          cls = 'log-thought';
  else if (/^Action\s*:/i.test(t))                        cls = 'log-action';
  else if (/^Action\s*Input\s*:/i.test(t))                cls = 'log-action-input';
  else if (/^Observation:/i.test(t))                      cls = 'log-observation';
  else if (/^Final Answer:/i.test(t))                     cls = 'log-answer';
  else if (/\[?ERROR\]?|Exception|Traceback|FAILED/i.test(t)) cls = 'log-error';
  else if (/STATUS:\s*COMPLETE/i.test(t))                 cls = 'log-success';
  else if (/STATUS:\s*INCOMPLETE/i.test(t))               cls = 'log-warning';
  else if (/dotnet build|dotnet restore|dotnet new/i.test(t)) cls = 'log-build';
  else if (/Tool:|tool_name|read_file|write_file|list_files|run_command/i.test(t)) cls = 'log-tool';
  else if (/\[WARNING\]|WARN/i.test(t))                   cls = 'log-warning';
  else if (/Score\s*:|score:/i.test(t))                   cls = 'log-answer';
  else if (/✅|COMPLETE|Success/i.test(t))                cls = 'log-success';

  appendLog(t, cls);
}

// ── Pipeline step tracking ────────────────────────────────────────────────────
const _agentStepMap = {
  'developer': [0, 1],  // Developer can be on Analyze OR Migrate
  'tester':    [2],
  'critic':    [3],
  'manager':   [4],
};
let _developerCallCount = 0;

function advancePipelineFromAgent(name) {
  const key = name.toLowerCase();
  if (key.includes('developer')) {
    _developerCallCount++;
    // First developer call = Analyze; second = Migrate
    setStep(_developerCallCount <= 1 ? 0 : 1);
  } else if (key.includes('tester'))  setStep(2);
  else if (key.includes('critic'))    setStep(3);
  else if (key.includes('manager'))   setStep(4);
}

function advancePipelineFromTask(desc) {
  const d = desc.toLowerCase();
  if (/analyz|discover|legacy.*files|inspect/.test(d))   maybeSetStep(0);
  else if (/migrat|convert|creat.*controller|write.*file/.test(d)) maybeSetStep(1);
  else if (/test|xunit|verify.*build/.test(d))            maybeSetStep(2);
  else if (/review|critic|score|quality/.test(d))         maybeSetStep(3);
  else if (/report|final.*status|complete/.test(d))       maybeSetStep(4);
}

function setStep(n) {
  if (n <= _currentStep) return;
  if (_currentStep >= 0) {
    document.getElementById(`s${_currentStep}`).className = 'step done';
  }
  _currentStep = n;
  document.getElementById(`s${n}`).className = 'step active';
}

function maybeSetStep(n) {
  if (n > _currentStep) setStep(n);
}

// ── DOM helpers ───────────────────────────────────────────────────────────────
function appendLog(text, cls) {
  const term = document.getElementById('terminal');
  const line = document.createElement('div');
  line.className = cls || 'log-default';
  line.textContent = text;
  term.appendChild(line);
  term.scrollTop = term.scrollHeight;
  _logCount++;
  document.getElementById('termLabel').textContent = `Agent Output  (${_logCount} lines)`;
}

function clearTerminal() {
  document.getElementById('terminal').innerHTML = '';
  _logCount = 0;
  document.getElementById('termLabel').textContent = 'Agent Output';
}

function showTerminal() {
  document.getElementById('termWrap').className = 'terminal-wrap visible';
}

function setAgentBar(name, task) {
  const bar = document.getElementById('agentBar');
  bar.className = 'agent-bar visible';
  if (name) document.getElementById('agentName').textContent = name;
  if (task !== undefined) document.getElementById('agentTask').textContent = task;
}

function setAgentTask(task) {
  document.getElementById('agentTask').textContent = task;
}

function startElapsedTimer() {
  if (_elapsedTimer) clearInterval(_elapsedTimer);
  _elapsedTimer = setInterval(() => {
    const s = Math.floor((Date.now() - _startTime) / 1000);
    const m = Math.floor(s / 60);
    const ss = String(s % 60).padStart(2, '0');
    document.getElementById('elapsedLabel').textContent = `${m}:${ss} elapsed`;
  }, 1000);
}

function stopElapsedTimer() {
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
}

function finishJob(status, result) {
  if (_sse) { _sse.close(); _sse = null; }
  stopElapsedTimer();

  const btn = document.getElementById('runBtn');

  if (status === 'complete') {
    // Mark all remaining steps done
    for (let i = _currentStep; i <= 4; i++) {
      document.getElementById(`s${i}`).className = 'step done';
    }
    appendLog('══ STATUS: COMPLETE ✅', 'log-success');
    document.getElementById('agentDot').style.animation = 'none';
    document.getElementById('agentDot').style.background = '#34d399';
    setAgentBar('Done', 'Migration complete');
    // Show result
    if (result) {
      const box = document.getElementById('resultBox');
      box.className = 'result-box visible';
      document.getElementById('resultPre').textContent = result;
    }
    btn.textContent = '▶ Run Another Migration';
  } else {
    appendLog('══ STATUS: FAILED ❌', 'log-error');
    document.getElementById(`s${_currentStep}`).className = 'step failed';
    document.getElementById('agentDot').style.animation = 'none';
    document.getElementById('agentDot').style.background = '#f87171';
    setAgentBar('Failed', result || 'Check log for details');
    btn.textContent = '▶ Try Again';
  }
  btn.disabled = false;
}

function resetUI() {
  _currentStep = -1;
  _developerCallCount = 0;
  _logCount = 0;
  for (let i = 0; i <= 4; i++) document.getElementById(`s${i}`).className = 'step';
  document.getElementById('agentBar').className = 'agent-bar';
  document.getElementById('agentDot').style.animation = '';
  document.getElementById('agentDot').style.background = '';
  document.getElementById('terminal').innerHTML = '';
  document.getElementById('termLabel').textContent = 'Agent Output';
  document.getElementById('resultBox').className = 'result-box';
  document.getElementById('resultPre').textContent = '';
  stopElapsedTimer();
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_PAGE
