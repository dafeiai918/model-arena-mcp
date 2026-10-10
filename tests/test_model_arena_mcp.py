# -*- coding: utf-8 -*-
"""model-arena-mcp 测试：协议 + Elo + 对比，用临时库和假站点"""
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import model_arena_mcp as ma  # noqa: E402

SCHEMA = """
CREATE TABLE ratings (model TEXT PRIMARY KEY, rating REAL DEFAULT 1000,
  wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, draws INTEGER DEFAULT 0);
CREATE TABLE battles (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, prompt TEXT,
  model_a TEXT, model_b TEXT, result TEXT);
"""


class _Api(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        body = json.dumps({"choices": [{"message": {
            "content": f"{payload['model']} 对这个问题的回答内容"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.server = ma.MCPServer("t", "1.0", ma.TOOLS)
        self.server._send = lambda m: self.sent.append(m)

    def _call(self, method, **kw):
        msg = {"jsonrpc": "2.0", "id": 1, "method": method}
        if kw:
            msg["params"] = kw
        self.server.handle(msg)
        return self.sent[-1]

    def test_initialize(self):
        self.assertEqual(self._call("initialize")["result"]["serverInfo"]["name"], "t")

    def test_tools_list(self):
        names = [t["name"] for t in self._call("tools/list")["result"]["tools"]]
        self.assertEqual(sorted(names),
                         ["compare_models", "leaderboard", "recommend_model", "record_vote"])

    def test_unknown_tool(self):
        self.assertEqual(self._call("tools/call", name="x", arguments={})["error"]["code"], -32602)

    def test_missing_db_iserror(self):
        out = self._call("tools/call", name="leaderboard", arguments={})
        self.assertTrue(out["result"]["isError"])
        self.assertIn("model-arena", out["result"]["content"][0]["text"])


class EloTest(unittest.TestCase):
    def test_expected_score(self):
        self.assertAlmostEqual(ma.expected_score(1000, 1000), 0.5)
        self.assertAlmostEqual(ma.expected_score(1000, 1400) + ma.expected_score(1400, 1000), 1.0)

    def test_zero_sum(self):
        a, b = ma.apply_result(1000, 1000, 1.0)
        self.assertAlmostEqual(a, 1016.0)
        self.assertAlmostEqual(b, 984.0)

    def test_upset_gains_more(self):
        low, high = ma.apply_result(1000, 1400, 1.0)
        self.assertGreater(low - 1000, 20)


class ArenaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = ThreadingHTTPServer(("127.0.0.1", 0), _Api)
        cls.port = cls.api.server_address[1]
        threading.Thread(target=cls.api.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.api.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (ma.DB_PATH, ma.CONFIG_PATH)
        ma.DB_PATH = Path(self.tmp.name) / "arena.db"
        ma.CONFIG_PATH = Path(self.tmp.name) / "config.json"
        conn = sqlite3.connect(str(ma.DB_PATH))
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()

    def tearDown(self):
        ma.DB_PATH, ma.CONFIG_PATH = self._orig
        self.tmp.cleanup()

    def test_record_vote_and_leaderboard(self):
        text = ma.tool_record_vote({"model_a": "m1", "model_b": "m2", "winner": "a"})
        self.assertIn("已记录", text)
        board = ma.tool_leaderboard({})
        self.assertIn("m1", board)
        self.assertIn("1016", board)

    def test_record_vote_draw(self):
        ma.tool_record_vote({"model_a": "m1", "model_b": "m2", "winner": "draw"})
        self.assertIn("1000", ma.tool_leaderboard({}))

    def test_record_vote_validation(self):
        self.assertIn("需要 model_a", ma.tool_record_vote({"model_a": "x", "winner": "a"}))
        self.assertIn("只能是", ma.tool_record_vote(
            {"model_a": "a", "model_b": "b", "winner": "c"}))

    def test_leaderboard_empty(self):
        self.assertIn("还是空的", ma.tool_leaderboard({}))

    def test_compare_models(self):
        text = ma.tool_compare_models({
            "prompt": "你好",
            "models": ["m1", "m2"],
            "base_url": f"http://127.0.0.1:{self.port}/v1",
            "api_key": "sk-test"})
        self.assertIn("m1", text)
        self.assertIn("m2", text)
        self.assertIn("最快", text)

    def test_compare_models_partial_failure(self):
        text = ma.tool_compare_models({
            "prompt": "你好", "models": ["m1", "m2"],
            "base_url": "http://127.0.0.1:1/v1", "api_key": "sk"})
        self.assertIn("失败", text)

    def test_compare_validation(self):
        self.assertIn("至少 2 个", ma.tool_compare_models({"prompt": "x", "models": ["a"]}))
        self.assertIn("需要 prompt", ma.tool_compare_models({"models": ["a", "b"]}))

    def test_compare_uses_config_when_missing_args(self):
        ma.CONFIG_PATH.write_text(json.dumps({
            "base_url": f"http://127.0.0.1:{self.port}/v1", "api_key": "sk-test"}),
            encoding="utf-8")
        text = ma.tool_compare_models({"prompt": "hi", "models": ["m1", "m2"]})
        self.assertIn("m1", text)

    def test_recommend_model(self):
        self.assertIn("Claude", ma.tool_recommend_model({"task": "写代码"}))
        self.assertIn("通用模型", ma.tool_recommend_model({"task": "翻译一段文字"}))
        self.assertIn("通用任务", ma.tool_recommend_model({"task": "随便什么"}))
        self.assertIn("请说明任务", ma.tool_recommend_model({}))

    def test_board_shows_battle_count(self):
        ma.tool_record_vote({"model_a": "a", "model_b": "b", "winner": "a"})
        self.assertIn("1 场对战", ma.tool_leaderboard({}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
