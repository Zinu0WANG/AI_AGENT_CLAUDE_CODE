# Memory Agent Demo

一个专门展示短期记忆、长期记忆、混合检索、记忆冲突和遗忘机制的个人助手。
它是独立子项目，不依赖或修改仓库中的 `coding_agent`。

## 记忆模型

### 短期记忆

- 最近 12 条消息组成当前工作窗口。
- 更早的内容压缩为会话摘要，只保留目标、事实、决定和未完成事项。
- 原始消息仍完整保存在 SQLite 中，压缩只影响送给模型的上下文。

### 长期记忆

| 类型 | 内容 | 示例 |
|---|---|---|
| `episodic` | 用户经历的事件 | 上周去了上海 |
| `semantic` | 稳定事实 | 用户的名字是小林 |
| `preference` | 喜好与表达风格 | 喜欢简洁的中文回答 |
| `prospective` | 计划和未完成事项 | 下周准备完成作品集 |
| `procedural` | 重复任务的处理方式 | 每次先给结论再解释 |

每条长期记忆保存来源、置信度、重要度、更新时间、访问次数和状态。
相同标准化键的新事实会替代旧事实，但旧记录继续保留为 `superseded`，便于审计。

## 混合检索

系统先按 `user_id`、状态和记忆类型过滤，然后统一计算：

```text
有 Embedding：
0.45 × 语义相似度
+ 0.25 × 词法相关度
+ 0.10 × 时间新鲜度
+ 0.10 × 重要度
+ 0.05 × 置信度
+ 0.05 × 类型匹配

无 Embedding：
0.55 × 词法相关度
+ 0.15 × 时间新鲜度
+ 0.15 × 重要度
+ 0.10 × 置信度
+ 0.05 × 类型匹配
```

中文词法相关度同时使用汉字和相邻汉字组合，不依赖 SQLite 默认中文分词。
Demo 数据量较小时直接在进程内计算余弦相似度；迁移到生产环境时可把这一层替换成
pgvector 或 Qdrant，而不改变上层服务。

## 启动

在仓库根目录执行：

```powershell
python -m pip install -r memory_agent_demo/requirements.txt
Copy-Item memory_agent_demo/.env.example memory_agent_demo/.env
python -m uvicorn memory_agent_demo.app.main:app --reload
```

打开 <http://127.0.0.1:8000>。

没有配置 API Key 时会自动进入 `offline-demo / fts-only` 模式，可以直接体验完整记忆流程。

若使用真实聊天模型，在 `memory_agent_demo/.env` 中配置：

```dotenv
ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=
MODEL_ID=claude-sonnet-4-6
```

若使用 OpenAI-compatible Embedding API，再配置：

```dotenv
EMBEDDING_API_KEY=...
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-small
```

Embedding 请求失败时自动退化为词法检索，不会中断聊天。

## 推荐演示流程

1. 输入：“我叫小林，我喜欢简洁的中文回答。”
2. 输入：“我计划下周完成 Agent 作品集。”
3. 点击“新建会话”，证明短期消息已经清空。
4. 输入：“你还记得我的名字和计划吗？”
5. 查看“本轮召回轨迹”中的语义、关键词和新鲜度分数。
6. 在长期记忆面板中归档、恢复或删除一条记忆。

离线规则只覆盖姓名、喜欢/不喜欢、计划和“请记住”表达；配置真实模型后会使用结构化
LLM 抽取器识别全部五种长期记忆。

## API

- `POST /api/chat`：聊天、召回和自动记忆写入。
- `POST /api/sessions`：创建新会话。
- `GET /api/sessions/{id}`：读取会话摘要和原始消息。
- `GET/POST /api/memories`：列出或手动创建记忆。
- `POST /api/memories/search`：查看混合检索结果及分项得分。
- `PATCH /api/memories/{id}`：编辑状态或元数据。
- `DELETE /api/memories/{id}/permanent`：永久删除。
- `POST /api/maintenance/consolidate`：执行衰减归档。

FastAPI 自动文档位于 <http://127.0.0.1:8000/docs>。

## 测试

```powershell
python -m pytest -q memory_agent_demo/tests
```

测试使用离线模型和假向量，不会访问网络，也不需要 API Key。

