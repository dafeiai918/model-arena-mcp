#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""model-arena-mcp · 模型对比与选型 MCP
让 AI 直接做：「这几个模型同题对比」「写代码该用哪个」「看本地排行」
零依赖 stdio MCP 服务器（与 model-arena 共用排行榜数据库）
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

__version__ = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"
DB_PATH = Path.home() / ".model-arena" / "arena.db"
CONFIG_PATH = Path.home() / ".model-arena" / "config.json"
K_FACTOR = 32
DEFAULT_RATING = 1000.0

# 按任务类型的选型经验（模型名关键词 → 适合的任务）
TASK_HINTS = {
    "代码": ("claude", "写代码优先考虑 Claude 系；其次 gpt / deepseek-coder / qwen-coder"),
    "编程": ("claude", "写代码优先考虑 Claude 系；其次 gpt / deepseek-coder / qwen-coder"),
    "翻译": ("gpt-4o", "翻译用 gpt-4o / qwen-plus 这类通用模型即可，不必上最贵的"),
    "总结": ("gpt-4o-mini", "总结类任务用 mini 档就够，省钱效果明显"),
    "长文": ("claude", "长文本理解优先 Claude / gemini 系（上下文窗口大）"),
    "数学": ("deepseek", "推理数学优先 deepseek-reasoner / o 系列"),
    "推理": ("deepseek", "推理优先 deepseek-reasoner / o 系列"),
    "看图": ("qwen-vl", "图像理解需要 VL 系列模型：qwen-vl / gpt-4o / gemini"),
    "便宜": ("qwen-turbo", "最便宜的可用档：qwen-turbo / gemini-flash / gpt-4o-mini"),
    "快": ("qwen-turbo", "要快选小模型：qwen-turbo / gpt-4o-mini"),
}


# ================= MCP 内核 =================

class MCPServer:
    def __init__(self, name: str, version: str, tools: list):
        self.name, self.version, self.tools = name, version, tools

    def _send(self, msg: dict) -> None:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _result(self, id_, result) -> None:
        self._send({"jsonrpc": "2.0", "id": id_, "result": result})

    def _error(self, id_, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})

    def handle(self, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        method, id_ = msg.get("method"), msg.get("id")
        if method == "initialize":
            self._result(id_, {"protocolVersion": PROTOCOL_VERSION,
                               "capabilities": {"tools": {"listChanged": False}},
                               "serverInfo": {"name": self.name, "version": self.version}})
        elif method in ("notifications/initialized", "initialized", "notifications/cancelled"):
            return
        elif method == "ping":
            self._result(id_, {})
        elif method == "tools/list":
            self._result(id_, {"tools": [
                {"name": t["name"], "description": t["description"],
                 "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}}}
                for t in self.tools]})
        elif method == "tools/call":
            params = msg.get("params") or {}
            tool = next((t for t in self.tools if t["name"] == params.get("name")), None)
            if tool is None:
                self._error(id_, -32602, f"未知工具: {params.get('name')}")
                return
            try:
                out = tool["handler"](params.get("arguments") or {})
                self._result(id_, {"content": [{"type": "text", "text": str(out)}], "isError": False})
            except Exception as exc:
                self._result(id_, {"content": [{"type": "text",
                                                "text": f"错误: {type(exc).__name__}: {exc}"}],
                                   "isError": True})
        elif id_ is not None:
            self._error(id_, -32601, f"不支持的方法: {method}")

    def serve(self) -> None:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            for item in (msg if isinstance(msg, list) else [msg]):
                self.handle(item)


# ================= 数据层 =================

def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"排行榜数据库不存在：{DB_PATH}\n"
            "先用 model-arena 打几轮：model-arena battle \"问题\"")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_site_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def expected_score(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def apply_result(ra: float, rb: float, score_a: float, k: float = K_FACTOR):
    ea = expected_score(ra, rb)
    return ra + k * (score_a - ea), rb + k * ((1 - score_a) - (1 - ea))


def record_battle(conn: sqlite3.Connection, prompt: str, model_a: str, model_b: str,
                  result: str) -> tuple[float, float]:
    def rating_of(model: str) -> float:
        row = conn.execute("SELECT rating FROM ratings WHERE model=?", (model,)).fetchone()
        if row:
            return float(row[0])
        conn.execute("INSERT OR IGNORE INTO ratings (model, rating) VALUES (?, ?)",
                     (model, DEFAULT_RATING))
        return DEFAULT_RATING

    ra, rb = rating_of(model_a), rating_of(model_b)
    score_a = {"a": 1.0, "b": 0.0, "draw": 0.5}[result]
    new_a, new_b = apply_result(ra, rb, score_a)
    conn.execute("UPDATE ratings SET rating=? WHERE model=?", (new_a, model_a))
    conn.execute("UPDATE ratings SET rating=? WHERE model=?", (new_b, model_b))
    if result == "a":
        conn.execute("UPDATE ratings SET wins=wins+1 WHERE model=?", (model_a,))
        conn.execute("UPDATE ratings SET losses=losses+1 WHERE model=?", (model_b,))
    elif result == "b":
        conn.execute("UPDATE ratings SET wins=wins+1 WHERE model=?", (model_b,))
        conn.execute("UPDATE ratings SET losses=losses+1 WHERE model=?", (model_a,))
    else:
        conn.execute("UPDATE ratings SET draws=draws+1 WHERE model=?", (model_a,))
        conn.execute("UPDATE ratings SET draws=draws+1 WHERE model=?", (model_b,))
    conn.execute("INSERT INTO battles (ts, prompt, model_a, model_b, result) VALUES (?,?,?,?,?)",
                 (time.time(), prompt[:200], model_a, model_b, result))
    conn.commit()
    return new_a, new_b


def ask_model(base_url: str, api_key: str, model: str, prompt: str,
              timeout: int = 90) -> tuple[str, str]:
    root = (base_url or "").strip().rstrip("/").removesuffix("/v1")
    if not root:
        return "", "缺少 base_url"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 800}
    req = urllib.request.Request(
        f"{root}/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "User-Agent": f"model-arena-mcp/{__version__}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return (data["choices"][0]["message"].get("content") or "").strip(), ""
    except urllib.error.HTTPError as exc:
        exc.close()
        return "", f"HTTP {exc.code}"
    except Exception as exc:
        return "", type(exc).__name__


# ================= 工具 =================

def tool_leaderboard(args: dict) -> str:
    conn = connect()
    rows = conn.execute("SELECT model, rating, wins, losses, draws FROM ratings "
                        "ORDER BY rating DESC").fetchall()
    total = conn.execute("SELECT COUNT(*) FROM battles").fetchone()[0]
    conn.close()
    if not rows:
        return "排行榜还是空的。用 model-arena 打几轮，或用 record_vote 记录结果。"
    lines = [f"本地模型排行榜（共 {total} 场对战）：", ""]
    for idx, row in enumerate(rows, 1):
        lines.append(f"{idx}. {row['model']}  {row['rating']:.0f}  "
                     f"({row['wins']}胜 {row['losses']}负 {row['draws']}平)")
    lines += ["", "说明：这是你自己投票得出的偏好榜，不代表绝对能力。样本少时波动大。"]
    return "\n".join(lines)


def tool_compare_models(args: dict) -> str:
    base_url = (args.get("base_url") or "").strip()
    api_key = (args.get("api_key") or "").strip()
    models = args.get("models") or []
    prompt = (args.get("prompt") or "").strip()
    if not isinstance(models, list) or len(models) < 2:
        return "需要 models 数组，至少 2 个模型名。"
    if not prompt:
        return "需要 prompt（要对比的问题）。"
    if not base_url or not api_key:
        config = load_site_config()
        base_url = base_url or config.get("base_url", "")
        api_key = api_key or config.get("api_key", "")
    if not base_url or not api_key:
        return "需要 base_url 和 api_key（或先用 model-arena setup 配置好）。"
    models = [str(m) for m in models][:5]

    def one(model: str):
        started = time.perf_counter()
        text, err = ask_model(base_url, api_key, model, prompt)
        return model, text, err, round(time.perf_counter() - started, 2)

    with ThreadPoolExecutor(max_workers=len(models)) as pool:
        results = list(pool.map(one, models))

    lines = [f"问题：{prompt[:80]}", ""]
    ok = [r for r in results if not r[2]]
    for model, text, err, secs in results:
        if err:
            lines.append(f"【{model}】失败：{err}")
        else:
            lines.append(f"【{model}】{secs}s")
            lines.append(text[:900] + ("…" if len(text) > 900 else ""))
        lines.append("")
    if len(ok) >= 2:
        fastest = min(ok, key=lambda r: r[3])
        longest = max(ok, key=lambda r: len(r[1]))
        lines.append(f"最快：{fastest[0]}（{fastest[3]}s）")
        lines.append(f"回答最长：{longest[0]}（{len(longest[1])} 字）")
        lines.append("")
        lines.append("想记录你的偏好：让我调用 record_vote 记录这一轮结果。")
    return "\n".join(lines)


def tool_recommend_model(args: dict) -> str:
    task = (args.get("task") or "").strip()
    if not task:
        return ("请说明任务类型，如「写代码」「翻译」「长文总结」「数学推理」"
                "「要最便宜的」。")
    low = task.lower()
    lines = [f"针对任务「{task}」的选型建议：", ""]
    matched = None
    for key, (hint_model, advice) in TASK_HINTS.items():
        if key in low or key in task:
            matched = (hint_model, advice)
            break
    if matched:
        lines.append(f"- {matched[1]}")
        lines.append(f"- 建议优先试：名字含「{matched[0]}」的模型")
    else:
        lines.append("- 通用任务：gpt-4o-mini / qwen-plus / deepseek-chat 这个档位通常够用")
        lines.append("- 复杂推理或代码：claude / deepseek-reasoner / gpt-4 档位")

    lines += ["", "结合你的实际情况：", "1. 先用 compare_models 拿几个候选同题对比",
              "2. 觉得差不多就用 cheapest 的那个",
              "3. 用 record_vote 把偏好记下来，攒出你自己的排行榜"]
    return "\n".join(lines)


def tool_record_vote(args: dict) -> str:
    model_a = (args.get("model_a") or "").strip()
    model_b = (args.get("model_b") or "").strip()
    winner = (args.get("winner") or "").strip().lower()
    if not model_a or not model_b:
        return "需要 model_a 和 model_b。"
    if winner not in ("a", "b", "draw"):
        return "winner 只能是 a / b / draw。"
    prompt = (args.get("prompt") or "(未记录问题)").strip()
    conn = connect()
    new_a, new_b = record_battle(conn, prompt, model_a, model_b, winner)
    conn.close()
    return (f"已记录：{model_a} vs {model_b} → {winner}\n"
            f"新分数：{model_a} {new_a:.0f} / {model_b} {new_b:.0f}")


TOOLS = [
    {"name": "leaderboard",
     "description": "查看本机模型盲测排行榜。用户问「哪个模型好」「我测出来的排行」时使用。",
     "inputSchema": {"type": "object", "properties": {}},
     "handler": tool_leaderboard},
    {"name": "compare_models",
     "description": "用同一个问题同时问多个模型，返回各自答案和耗时做对比。"
                    "用户说「这几个模型哪个好」「对比一下」时使用。会消耗额度。",
     "inputSchema": {"type": "object", "properties": {
         "prompt": {"type": "string", "description": "要问的问题"},
         "models": {"type": "array", "items": {"type": "string"},
                    "description": "模型名数组，2-5 个"},
         "base_url": {"type": "string"}, "api_key": {"type": "string"}},
         "required": ["prompt", "models"]},
     "handler": tool_compare_models},
    {"name": "recommend_model",
     "description": "按任务类型推荐模型档位。用户问「写代码用哪个模型」"
                    "「哪个便宜」时使用。",
     "inputSchema": {"type": "object", "properties": {
         "task": {"type": "string", "description": "任务描述"}},
         "required": ["task"]},
     "handler": tool_recommend_model},
    {"name": "record_vote",
     "description": "记录一轮对比结果到 Elo 排行榜。用户说「A 更好」「记下来」时使用。",
     "inputSchema": {"type": "object", "properties": {
         "model_a": {"type": "string"}, "model_b": {"type": "string"},
         "winner": {"type": "string", "enum": ["a", "b", "draw"]},
         "prompt": {"type": "string"}},
         "required": ["model_a", "model_b", "winner"]},
     "handler": tool_record_vote},
]


def main() -> int:
    if os.name == "nt":
        try:
            sys.stdin.reconfigure(encoding="utf-8")
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    MCPServer("model-arena-mcp", __version__, TOOLS).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
