# model-arena-mcp · 模型对比与选型 MCP

**让 AI 直接做：「这几个模型同题比一比」「写代码该用哪个」「看我的排行榜」**

零依赖 · 单文件 · stdio MCP 服务器 · 中文

---

## 它让 AI 能做什么

> 「gpt-4o-mini 和 deepseek-chat 哪个好？」
> → AI 调用 `compare_models`，同一个问题问两个模型，返回答案 + 耗时对比

> 「写代码用哪个模型合适？」
> → AI 调用 `recommend_model`，按任务给档位建议

> 「看我的排行榜」
> → AI 调用 `leaderboard`，展示你盲测出的 Elo 榜

> 「我觉得第一个更好，记下来」
> → AI 调用 `record_vote`，记进 Elo 排行榜

---

## 提供的工具

| 工具 | 作用 | 消耗 |
|---|---|---|
| `compare_models` | 同题对比 2-5 个模型 | 每个模型 1 次调用 |
| `recommend_model` | 按任务推荐档位 | 0 |
| `leaderboard` | 本地 Elo 排行 | 0 |
| `record_vote` | 记录对比结果 | 0 |

---

## 安装

```json
{
  "mcpServers": {
    "model-arena-mcp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/dafeiai918/model-arena-mcp", "model-arena-mcp"]
    }
  }
}
```

零第三方依赖，Python 3.9+。

---

## 两种用法

### 1. 即时对比（不记分）
直接说「对比一下 A 和 B 对这个问题怎么答」→ 得到并排答案 + 耗时。

### 2. 攒排行榜（记分）
对比后说「左边更好，记下来」→ Elo 更新，攒够 20 轮就是**属于你自己的**模型排行。

排行榜数据来自 [model-arena](https://github.com/dafeiai918/model-arena)，两个工具共用同一份 `~/.model-arena/arena.db`。

---

## 站点配置

`compare_models` 需要站点地址和密钥。优先用参数传入，没传时读 `~/.model-arena/config.json`：

```bash
python model_arena.py setup -u https://api.example.com/v1 -k sk-xxxx
```

---

## 选型建议覆盖的任务

写代码 / 推理 / 数学 / 翻译 / 总结 / 长文 / 看图 / 要便宜 / 要快

没匹配到时给通用性价比排行。

---

## 说明

- 对比会消耗你自己的额度
- `leaderboard` 是你个人偏好榜，不代表绝对能力
- 样本量建议 20 轮以上再下结论

---

## 相关项目

| 项目 | 用途 |
|---|---|
| [model-arena](https://github.com/dafeiai918/model-arena) | 命令行版盲测擂台 |
| [site-bench-mcp](https://github.com/dafeiai918/site-bench-mcp) | 站点质量评测 MCP |
| [price-radar-mcp](https://github.com/dafeiai918/price-radar-mcp) | 价格与性价比 MCP |

---

## License

MIT
