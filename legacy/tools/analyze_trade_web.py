#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse


TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from output_paths import OUTPUT_ROOT


ANALYZE_SCRIPT = TOOLS_DIR / "analyze_trade.py"
WEB_STATE_DIR = OUTPUT_ROOT / "_web"
HISTORY_PATH = WEB_STATE_DIR / "history.json"
MAX_HISTORY_STORED = 100
MAX_HISTORY_RETURNED = 10
OUTPUT_ROOT_RESOLVED = OUTPUT_ROOT.resolve()


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Polymarket Trade Analyzer</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101214;
      --panel: #171b1f;
      --panel-2: #20262c;
      --text: #e8edf2;
      --muted: #9aa6b2;
      --line: #2d353d;
      --accent: #4fb3ff;
      --danger: #ff6b6b;
      --ok: #42d392;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .topbar {
      position: sticky;
      top: 0;
      z-index: 10;
      border-bottom: 1px solid var(--line);
      background: rgba(16, 18, 20, 0.96);
      backdrop-filter: blur(10px);
    }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(320px, 1.5fr) minmax(300px, 1fr) auto auto minmax(260px, 0.9fr);
      gap: 10px;
      align-items: end;
      padding: 14px 16px;
    }
    label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 5px;
    }
    input, select, button {
      width: 100%;
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      font: inherit;
    }
    input, select { padding: 0 10px; }
    input:focus, select:focus {
      outline: 2px solid rgba(79, 179, 255, 0.35);
      border-color: var(--accent);
    }
    button {
      padding: 0 14px;
      cursor: pointer;
      background: var(--panel-2);
      white-space: nowrap;
    }
    button.primary {
      border-color: #2c82bd;
      background: #145987;
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }
    .status {
      min-height: 28px;
      padding: 0 16px 12px;
      color: var(--muted);
      font-size: 13px;
    }
    .status.error { color: var(--danger); }
    .status.ok { color: var(--ok); }
    .result {
      height: calc(100vh - 116px);
      min-height: 520px;
      background: #0d0f11;
    }
    .empty {
      height: 100%;
      display: grid;
      place-items: center;
      color: var(--muted);
      text-align: center;
      padding: 24px;
    }
    iframe {
      display: none;
      width: 100%;
      height: 100%;
      border: 0;
      background: #111;
    }
    @media (max-width: 1100px) {
      .toolbar {
        grid-template-columns: 1fr 1fr;
      }
    }
    @media (max-width: 720px) {
      .toolbar {
        grid-template-columns: 1fr;
      }
      .result {
        height: calc(100vh - 300px);
      }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <form id="analyzeForm" class="toolbar">
      <div>
        <label for="market">Market</label>
        <input id="market" name="market" autocomplete="off" placeholder="Bitcoin Up or Down - July 5, 11:25PM-11:30PM ET">
      </div>
      <div>
        <label for="user">User / Proxy Wallet</label>
        <input id="user" name="user" autocomplete="off" placeholder="0xe29042f5d913dcc4015aab3455c13c58514ca33f">
      </div>
      <div>
        <label>&nbsp;</label>
        <button id="analyzeBtn" class="primary" type="submit">分析</button>
      </div>
      <div>
        <label>&nbsp;</label>
        <button id="resetBtn" type="button">重置</button>
      </div>
      <div>
        <label for="recent">近期查询</label>
        <select id="recent">
          <option value="">最近 10 条</option>
        </select>
      </div>
    </form>
    <div id="status" class="status"></div>
  </header>
  <main class="result">
    <div id="empty" class="empty">
      <div>
        <div>输入 market 和 user 后点击分析。</div>
        <div style="margin-top: 6px;">结果 HTML 会自动保存，并显示在这里。</div>
      </div>
    </div>
    <iframe id="resultFrame" title="analysis result"></iframe>
  </main>
  <script>
    const form = document.getElementById("analyzeForm");
    const marketInput = document.getElementById("market");
    const userInput = document.getElementById("user");
    const recentSelect = document.getElementById("recent");
    const analyzeBtn = document.getElementById("analyzeBtn");
    const resetBtn = document.getElementById("resetBtn");
    const statusEl = document.getElementById("status");
    const frame = document.getElementById("resultFrame");
    const empty = document.getElementById("empty");
    let historyRows = [];

    function setStatus(message, kind = "") {
      statusEl.textContent = message || "";
      statusEl.className = "status" + (kind ? " " + kind : "");
    }

    function showResult(record) {
      if (!record) return;
      marketInput.value = record.market || "";
      userInput.value = record.user || "";
      frame.src = record.result_url;
      frame.style.display = "block";
      empty.style.display = "none";
      setStatus(`已加载：${record.label}`, "ok");
    }

    function resetPage() {
      marketInput.value = "";
      userInput.value = "";
      recentSelect.value = "";
      frame.removeAttribute("src");
      frame.style.display = "none";
      empty.style.display = "grid";
      setStatus("");
    }

    async function loadHistory() {
      const res = await fetch("/api/history");
      const data = await res.json();
      historyRows = data.history || [];
      recentSelect.innerHTML = '<option value="">最近 10 条</option>';
      for (const row of historyRows) {
        const opt = document.createElement("option");
        opt.value = row.id;
        opt.textContent = row.label;
        recentSelect.appendChild(opt);
      }
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const market = marketInput.value.trim();
      const user = userInput.value.trim();
      if (!market || !user) {
        setStatus("market 和 user 都必须填写。", "error");
        return;
      }
      analyzeBtn.disabled = true;
      resetBtn.disabled = true;
      setStatus("正在分析，通常需要几秒到几十秒...");
      try {
        const res = await fetch("/api/analyze", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({market, user})
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.error || "分析失败");
        }
        await loadHistory();
        showResult(data.record);
      } catch (err) {
        setStatus(err.message || String(err), "error");
      } finally {
        analyzeBtn.disabled = false;
        resetBtn.disabled = false;
      }
    });

    recentSelect.addEventListener("change", () => {
      const row = historyRows.find((item) => item.id === recentSelect.value);
      if (row) showResult(row);
    });

    resetBtn.addEventListener("click", resetPage);
    loadHistory().catch((err) => setStatus(err.message || String(err), "error"));
  </script>
</body>
</html>
"""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _short_user(user: str) -> str:
    text = str(user or "").strip()
    if len(text) >= 12 and text.lower().startswith("0x"):
        return f"{text[:8]}...{text[-4:]}"
    return text[:18] or "unknown"


def _safe_output_path(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    try:
        path.relative_to(OUTPUT_ROOT_RESOLVED)
    except ValueError as exc:
        raise ValueError(f"path is outside output root: {path}") from exc
    return path


def _load_history() -> list[dict[str, Any]]:
    try:
        data = json.loads(HISTORY_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    rows = [row for row in data if isinstance(row, dict)]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return rows


def _save_history(rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    WEB_STATE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(rows[:MAX_HISTORY_STORED], indent=2, ensure_ascii=False))


def _label(record: dict[str, Any]) -> str:
    created = str(record.get("created_at") or "")
    market = str(record.get("market") or "market")
    user = _short_user(str(record.get("user") or ""))
    if len(market) > 64:
        market = market[:61] + "..."
    return f"{created.replace('T', ' ').replace('Z', '')} | {market} | {user}"


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": str(record["id"]),
        "created_at": str(record.get("created_at") or ""),
        "market": str(record.get("market") or ""),
        "user": str(record.get("user") or ""),
        "analysis_html": str(record.get("analysis_html") or ""),
    }
    out["label"] = _label(record)
    out["result_url"] = f"/result/{quote(out['id'])}/analysis_table.html"
    return out


def _parse_output_path(stdout: str, label: str) -> str:
    pattern = re.compile(rf"^{re.escape(label)}:\s*(.+?)\s*$", re.MULTILINE)
    match = pattern.search(stdout)
    return match.group(1).strip() if match else ""


def _parse_saved_trades_path(stdout: str) -> str:
    match = re.search(r"^Saved \d+ trades to\s+(.+?)\s*$", stdout, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _run_analysis(market: str, user: str) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(ANALYZE_SCRIPT),
        market,
        "--user",
        user,
        "--refresh",
        "--no-open",
    ]
    proc = subprocess.run(
        cmd,
        cwd=ROOT_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
        env=os.environ.copy(),
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if proc.returncode != 0:
        detail = (stderr or stdout or "unknown error").strip()
        raise RuntimeError(detail[-2000:])

    analysis_html = _parse_output_path(stdout, "Analysis table")
    chart_html = _parse_output_path(stdout, "Chart")
    fetch_meta = _parse_output_path(stdout, "Fetch metadata")
    trades_json = _parse_saved_trades_path(stdout)
    if not analysis_html:
        saved_match = re.search(r"^Analysis table:\s*(.+)$", stdout, re.MULTILINE)
        analysis_html = saved_match.group(1).strip() if saved_match else ""
    if not analysis_html:
        raise RuntimeError("analysis completed but did not print Analysis table path")

    analysis_path = _safe_output_path(analysis_html)
    if not analysis_path.exists():
        raise RuntimeError(f"analysis HTML not found: {analysis_path}")

    record = {
        "id": analysis_path.parent.name,
        "created_at": _utc_now(),
        "market": market,
        "user": user,
        "analysis_html": str(analysis_path),
        "chart_html": str(_safe_output_path(chart_html)) if chart_html else "",
        "fetch_meta": str(_safe_output_path(fetch_meta)) if fetch_meta else "",
        "trades_json": str(_safe_output_path(trades_json)) if trades_json else "",
        "stdout_tail": stdout[-4000:],
    }
    history = [row for row in _load_history() if row.get("id") != record["id"]]
    history.insert(0, record)
    _save_history(history)
    return record


class AnalyzeTradeHandler(BaseHTTPRequestHandler):
    server_version = "AnalyzeTradeWeb/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[analyze-web] {self.address_string()} - {fmt % args}\n")

    def _send_bytes(self, data: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        self._send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def _send_error_json(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._send_json({"error": message}, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/history":
            records = [_public_record(row) for row in _load_history()[:MAX_HISTORY_RETURNED]]
            self._send_json({"history": records})
            return
        result_match = re.fullmatch(r"/result/([^/]+)/analysis_table\.html", path)
        if result_match:
            record_id = unquote(result_match.group(1))
            record = next((row for row in _load_history() if row.get("id") == record_id), None)
            if not record:
                self._send_error_json("result not found", HTTPStatus.NOT_FOUND)
                return
            try:
                analysis_path = _safe_output_path(str(record.get("analysis_html") or ""))
                data = analysis_path.read_bytes()
            except Exception as exc:
                self._send_error_json(str(exc), HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(data, "text/html; charset=utf-8")
            return
        self._send_error_json("not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/analyze":
            self._send_error_json("not found", HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send_error_json("invalid JSON payload")
            return
        market = str(payload.get("market") or "").strip()
        user = str(payload.get("user") or "").strip()
        if not market or not user:
            self._send_error_json("market and user are required")
            return
        try:
            record = _run_analysis(market, user)
        except subprocess.TimeoutExpired:
            self._send_error_json("analysis timed out after 600 seconds", HTTPStatus.GATEWAY_TIMEOUT)
            return
        except Exception as exc:
            self._send_error_json(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json({"record": _public_record(record)})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local web UI for legacy/tools/analyze_trade.py")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    WEB_STATE_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), AnalyzeTradeHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Serving analyze trade UI at {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping analyze trade UI.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
