# 方案1：交易后反思学习闭环（TradingAgents 式）

> 参考：`D:\top\data\repos\TradingAgents\tradingagents\agents\utils\memory.py`（`TradingMemoryLog`）+ `graph/reflection.py`（`Reflector`）+ `graph/trading_graph.py` 的 `_resolve_pending_entries / propagate`。

---

## 一、源码调研结论（TradingAgents 怎么做的）

### 1.1 核心类与文件

| 文件 | 核心类/函数 | 职责 |
|---|---|---|
| `agents/utils/memory.py` | `TradingMemoryLog` | 追加式 markdown 决策日志（存储+检索+注入） |
| `graph/reflection.py` | `Reflector` | 单次 LLM 反思调用，产出 2-4 句经验 |
| `graph/trading_graph.py` | `_fetch_returns / _resolve_pending_entries / propagate / store_decision` | 流程编排：拉收益→反思→回写 |

### 1.2 完整流程（两阶段）

**Phase A — 决策时（propagate 末尾）**：
```
propagate(ticker, trade_date)
  ├─ past_context = memory_log.get_past_context(ticker)   # 读历史经验注入
  ├─ init_state = { ..., past_context }                    # 注入初始 state
  ├─ final_state = graph.invoke(init_state)
  └─ memory_log.store_decision(ticker, trade_date, final_decision)  # 写 pending 条目
```

**Phase B — 下次同 ticker 分析时（propagate 开头）**：
```
propagate(ticker, trade_date)
  ├─ _resolve_pending_entries(ticker)
  │    ├─ pending = memory_log.get_pending_entries() filtered by ticker
  │    ├─ for entry in pending:
  │    │    raw, alpha, days = _fetch_returns(ticker, entry.date, holding=5, benchmark)
  │    │    if raw is None: continue   # 价格还没到，下次再试
  │    │    reflection = reflector.reflect_on_final_decision(decision, raw, alpha)
  │    │    updates.append({...reflection})
  │    └─ memory_log.batch_update_with_outcomes(updates)   # 原子批量回写
  └─ ... 继续本次分析
```

**触发时机**：不是定时任务，而是"下次分析同一 ticker 时惰性触发"。没有该 ticker 的 pending 就跳过。

### 1.3 存储格式（Markdown 文件，非数据库）

每条 entry 用 HTML 注释分隔符 `<!-- ENTRY_END -->`：
```
[2025-08-01 | AAPL | Buy | +12.3% | +5.1% alpha | 5d]

DECISION:
（完整 final_trade_decision 文本）

REFLECTION:
（2-4 句 LLM 反思）
<!-- ENTRY_END -->
```
pending 态：`[2025-08-01 | AAPL | Buy | pending]`。回写时把 `pending` 换成 `+12.3% | +5.1% alpha | 5d` 并追加 `REFLECTION:` 段。原子写：temp file + `os.replace()`。

### 1.4 "决策对不对"的判断标准（关键！）

TradingAgents 用 **双重指标**：
1. **raw_return**：`股票 5 日实际涨跌幅`
2. **alpha_return**：`raw_return - 基准指数同期涨跌幅`（默认 SPY，日股 ^N225，可配置）

- holding_days 默认 **5 个交易日**（约一周），`_fetch_returns` 给 `end = start + holding_days + 7` 缓冲周末。
- `actual_days = min(holding_days, len(stock)-1, len(bench)-1)` —— 价格不够就等下次。
- **判断逻辑交给 LLM**：不做硬编码阈值（如 score>0 才算对），而是把 raw/alpha 数字塞进 prompt 让模型自己定性"方向对不对"。这比绝对收益更稳健——熊市跑赢基准也算 alpha 正。

### 1.5 检索与注入机制（`get_past_context`）

```python
def get_past_context(self, ticker, n_same=5, n_cross=3):
    # 只取已 resolved 的条目（跳过 pending）
    entries = [e for e in load_entries() if not e.pending]
    # 分两类：同 ticker 最近 5 条（完整）+ 跨 ticker 最近 3 条（仅反思）
    for e in reversed(entries):
        if e.ticker == ticker and len(same) < 5: same.append(e)
        elif e.ticker != ticker and len(cross) < 3: cross.append(e)
    # 拼成 prompt 片段
    parts = []
    if same:
        parts.append(f"Past analyses of {ticker} (most recent first):")
        parts.extend(format_full(e) for e in same)      # 完整 DECISION + REFLECTION
    if cross:
        parts.append("Recent cross-ticker lessons:")
        parts.extend(format_reflection_only(e) for e in cross)  # 只反思句
    return "\n\n".join(parts)
```
**注入点**：拼进 `create_initial_state` 的 `past_context`，最终进入 PM（投资组合经理）的 system prompt。

### 1.6 反思 Prompt 设计（精炼典范）

```python
# graph/reflection.py — log_reflection_prompt
"You are a trading analyst reviewing your own past decision now that the outcome is known.
Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).
Cover in order:
1. Was the directional call correct? (cite the alpha figure)
2. Which part of the investment thesis held or failed?
3. One concrete lesson to apply to the next similar analysis.
Be specific and terse. Your output will be stored verbatim in a decision log
and re-read by future analysts, so every word must earn its place."
```
**设计要点**：(1) 限字数防膨胀；(2) 强制结构（方向→论据→教训）；(3) "every word must earn its place" 的语气约束；(4) 存原文不二次加工。

---

## 二、FinanceCrew 适配设计

### 2.0 架构差异与适配策略

| 维度 | TradingAgents | FinanceCrew 现状 | 适配决策 |
|---|---|---|---|
| 存储 | markdown 文件 | SQLite（`analyses` 表存 result JSON） | **改用 SQLite 新表**（结构化、可查询、多用户隔离） |
| 多用户 | 单用户 | 多用户（user_id） | 反思记忆表加 `user_id`，用户间隔离 |
| 市场 | 美股为主（yfinance） | A股为主+港股美股（腾讯行情） | 持有期收益用现有 `get_history`（A股前复权），基准用沪深300而非SPY |
| 触发 | 下次同 ticker 分析时 | 同上 | **沿用惰性触发**（最简、无需 cron） |
| holding_days | 固定 5 | —— | **可配置，默认 5 交易日**，A股 T+1 故最小 5 |

### 2.1 数据库表设计（新增 1 表）

```sql
-- 反思记忆表：每次分析的"决策快照 + 待结算/已结算"记录
CREATE TABLE IF NOT EXISTS reflection_memos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,           -- 多用户隔离
    ticker          TEXT NOT NULL,              -- 标准代码（600519 / hk00700 / usAAPL）
    analysis_id     INTEGER NOT NULL,           -- 关联 analyses.id
    trade_date      TEXT NOT NULL,              -- 决策日（YYYY-MM-DD），取 analysis.created_at 的日期部分
    decision_score  REAL NOT NULL,              -- consensus_score（-10~+10），用于方向判定
    action          TEXT NOT NULL,              -- trade_plan.action（买入/卖出/观望/回避）
    decision_text   TEXT NOT NULL,              -- consensus_verdict（完整决策文本，供反思）
    -- 结算字段（Phase B 回填）
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending / resolved / skipped
    raw_return      REAL,                       -- 持有期实际涨跌幅（小数，如 0.052）
    alpha_return    REAL,                       -- 相对基准的超额（小数）
    benchmark       TEXT,                       -- 使用的基准（沪深300 / 恒生 / SPX）
    holding_days    INTEGER,                    -- 实际结算交易日数
    resolved_at     TEXT,                       -- 结算时间戳
    reflection      TEXT,                       -- LLM 反思（2-4 句）
    direction_correct INTEGER,                  -- 0/1，方向是否判对（便于统计命中率）
    FOREIGN KEY (analysis_id) REFERENCES analyses(id)
);
CREATE INDEX IF NOT EXISTS idx_reflection_pending ON reflection_memos(user_id, ticker, status);
CREATE INDEX IF NOT EXISTS idx_reflection_lookup ON reflection_memos(user_id, ticker, status, trade_date);
```

**为什么不用 markdown 文件**：
1. FinanceCrew 已是 SQLite 多用户系统，文件存储无法做 user 隔离、无法 JOIN 查询、无法前端分页。
2. 结构化字段（`raw_return`/`alpha_return`/`direction_correct`）便于后续做"历史命中率统计""按分析师角色拆分胜率"。
3. TradingAgents 的 markdown 方案是为 CLI 单用户设计的，不值得照搬。

### 2.2 完整流程图（何时触发反思）

```
┌─────────────────────────────────────────────────────────────────┐
│  用户请求分析 ticker=600519                                      │
│  POST /api/analysis/stream                                      │
└──────────────┬──────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────┐   新增节点（可选独立）
│ ① resolve_pending_reflections(ticker)     │   位于 collect_data 之前
│   - 查 reflection_memos WHERE user_id     │
│     AND ticker=? AND status='pending'     │
│   - 对每条 pending：                       │
│     fetch_returns() → 若价格够(≥5交易日)   │
│       → call_llm_reflect() → UPDATE 行     │
│     若价格不够 → 跳过（下次再试）            │
└──────────────┬───────────────────────────┘
               │ (不阻塞主流程，异常吞掉记日志)
               ▼
┌──────────────────────────────────────────┐
│ ② collect_data                            │   现有节点
│   - 额外读取：past_reflections =          │   新增注入
│     get_past_reflections(user_id, ticker) │
│   - 写入 ctx["past_reflections"]          │
└──────────────┬───────────────────────────┘
               ▼
┌──────────────────────────────────────────┐
│ ③ run_analyst × 5（并行）                  │   分析师 prompt 注入
│   每个 analyst 的 system_prompt 末尾追加：  │   past_reflections 片段
│   "【历史反思经验】\n{past_reflections}"   │
└──────────────┬───────────────────────────┘
               ▼
   ... debate → consensus → risk → trader ...
               │
               ▼
┌──────────────────────────────────────────┐
│ ④ finalize                                │   现有节点末尾新增
│   - save_analysis(...) 如常                │
│   - 新增：store_decision_memo(            │
│       user_id, ticker, analysis_id,       │
│       trade_date=今天,                    │
│       decision_score, action,             │
│       decision_text=consensus_verdict)    │
│     INSERT 一行 status='pending'           │
└──────────────────────────────────────────┘

（下一次分析同 ticker 时，回到 ① 结算上一条）
```

**关键设计决策**：
- **结算在分析开头（惰性）而非定时任务**：与 TradingAgents 一致。好处——无需 cron、无需外部调度、用户不活跃时不浪费 LLM 调用。坏处——用户再不碰该 ticker 则永不结算（可接受，那些记忆本来也没人看）。
- **`resolve_pending_reflections` 异常隔离**：反思失败绝不阻塞用户当前分析。包 try/except，失败记日志，pending 留待下次。
- **持有期收益用现有 `get_history`**：A股前复权已处理；港股/美股走 yfinance 路径。

### 2.3 收益计算（A股基准适配）

```python
# 新文件 backend/app/reflection/returns.py
from datetime import datetime, timedelta
from ..data import datalayer

def fetch_returns(ticker: str, trade_date: str, holding_days: int = 5) -> dict | None:
    """计算 ticker 自 trade_date 起 holding_days 交易日的 raw/alpha 收益。
    
    基准选择：
    - A股(6位数字)：沪深300(代码 000300)
    - 港股(hk开头)：恒生指数(hkHSI)
    - 美股(us开头)：标普500(usSPX)
    价格不够（未到 holding_days）返回 None，调用方跳过。
    """
    benchmark = _resolve_benchmark(ticker)  # 000300 / hkHSI / usSPX
    start = datetime.strptime(trade_date, "%Y-%m-%d")
    end_str = (start + timedelta(days=holding_days + 10)).strftime("%Y-%m-%d")  # +10 缓冲
    
    hist = datalayer.get_history(ticker, days=holding_days + 15)
    bench = datalayer.get_history(benchmark, days=holding_days + 15)
    if hist is None or bench is None or len(hist) < 2 or len(bench) < 2:
        return None
    
    # 过滤到 [trade_date, end] 区间
    stock_in = hist[(hist["date"] >= trade_date) & (hist["date"] <= end_str)]
    bench_in = bench[(bench["date"] >= trade_date) & (bench["date"] <= end_str)]
    if len(stock_in) < 2 or len(bench_in) < 2:
        return None  # 价格还没到
    
    actual = min(holding_days, len(stock_in) - 1, len(bench_in) - 1)
    raw = (stock_in["close"].iloc[actual] - stock_in["close"].iloc[0]) / stock_in["close"].iloc[0]
    bench_ret = (bench_in["close"].iloc[actual] - bench_in["close"].iloc[0]) / bench_in["close"].iloc[0]
    alpha = raw - bench_ret
    
    # 方向判定：decision_score>0 看多，raw>0 即方向对
    return {
        "raw_return": float(raw),
        "alpha_return": float(alpha),
        "benchmark": benchmark,
        "holding_days": actual,
    }

def _resolve_benchmark(ticker: str) -> str:
    if ticker.isdigit() and len(ticker) == 6:
        return "000300"      # 沪深300
    if ticker.startswith("hk"):
        return "hkHSI"       # 恒生
    if ticker.startswith("us"):
        return "usSPX"       # 标普500
    return "000300"
```

### 2.4 LLM 反思 Prompt 设计（中文、A股语境）

```python
# 新文件 backend/app/reflection/reflector.py
REFLECTION_SYSTEM_PROMPT = """你是一位交易分析师，正在复盘自己过去的一次决策，现在结果已经揭晓。
请用 2-4 句纯文本（不要用项目符号、不要标题、不要 markdown 格式）总结反思。

按顺序覆盖：
1. 方向判断是否正确？（引用 alpha 数据：相对沪深300/恒生/标普500 的超额收益）
2. 决策逻辑中哪部分成立、哪部分失效？
3. 一条可复用到下次类似分析的具体教训。

要求：具体、简洁。你的输出会被原样存入决策日志，供未来的分析师阅读，每个字都要有价值。"""

REFLECTION_USER_TEMPLATE = """基准: {benchmark}
持有期: {holding_days} 个交易日
实际涨跌幅: {raw_return:+.1%}
相对基准超额收益(alpha): {alpha_return:+.1%}

当时决策:
{decision_text}"""

# 方向判定（不用 LLM，确定性规则）
def judge_direction(decision_score: float, raw_return: float) -> int:
    """decision_score: -10~+10; raw_return: 小数。返回 1=方向正确, 0=错误。"""
    if decision_score > 0 and raw_return > 0:   # 看多且涨
        return 1
    if decision_score < 0 and raw_return < 0:   # 看空且跌
        return 1
    if abs(decision_score) < 1:                 # 中性，不算对错
        return 1  # 或定义为新类别 'neutral'
    return 0
```

### 2.5 经验注入机制（怎么用上历史经验）

在 `collect_data` 节点末尾追加：
```python
# graph/nodes.py — collect_data 内新增
ctx["past_reflections"] = get_past_reflections(user_id, ticker) if user_id else ""
```

```python
# 新文件 backend/app/reflection/store.py
def get_past_reflections(user_id: int, ticker: str, n_same: int = 5, n_cross: int = 3) -> str:
    """检索历史反思，拼成 prompt 片段。
    
    策略（移植 TradingAgents get_past_context）：
    - 同 ticker 最近 5 条：完整 decision + reflection
    - 跨 ticker 最近 3 条：仅 reflection（跨标的教训）
    """
    from ..memory import _connect  # 复用现有连接
    with _connect() as conn:
        same = conn.execute(
            "SELECT * FROM reflection_memos WHERE user_id=? AND ticker=? AND status='resolved' "
            "ORDER BY trade_date DESC LIMIT ?", (user_id, ticker, n_same)
        ).fetchall()
        cross = conn.execute(
            "SELECT * FROM reflection_memos WHERE user_id=? AND ticker<>? AND status='resolved' "
            "ORDER BY resolved_at DESC LIMIT ?", (user_id, ticker, n_cross)
        ).fetchall()
    
    if not same and not cross:
        return ""
    
    parts = []
    if same:
        parts.append(f"【{ticker} 历史决策复盘】（最近{len(same)}次）：")
        for r in same:
            parts.append(
                f"[{r['trade_date']} | 评分{r['decision_score']:.1f} | "
                f"实际{r['raw_return']:+.1%} | alpha{r['alpha_return']:+.1%}]\n"
                f"当时决策: {r['decision_text'][:200]}\n"
                f"反思: {r['reflection']}"
            )
    if cross:
        parts.append("【跨标的通用教训】（最近）：")
        for r in cross:
            parts.append(f"- {r['ticker']}({r['trade_date']}): {r['reflection']}")
    return "\n\n".join(parts)
```

**注入点**（`agents/base.py` 的 `_call_structured`）：
```python
def _call_structured(self, user_prompt, context):
    # 现有: 注入 user_memories
    if context:
        user_prompt += self._memory_block(context)
        user_prompt += self._reflection_block(context)  # 新增
    ...

def _reflection_block(self, context) -> str:
    past = context.get("past_reflections") or ""
    if not past:
        return ""
    return f"\n\n【历史反思经验】\n以下是系统对该标的及跨标的的历史决策复盘，分析时请参考其中被验证过的规律，避免重复同类错误：\n{past}\n"
```

### 2.6 前端展示

**新增组件**：`frontend/src/ReflectionPanel.tsx`（嵌入 HistoryPage 详情视图）。

展示内容：
1. **该标的的历史命中率**：环形图（方向正确率 = sum(direction_correct)/count）
2. **历史反思列表**：时间轴卡片，每张显示 `[日期 | 评分 | 实际涨跌 | alpha] + 反思文本`
3. **待结算条目**：标记 `pending`（灰色），显示"等待 N 个交易日结算"

API：
```typescript
// types.ts 新增
export interface ReflectionMemo {
  id: number
  ticker: string
  trade_date: string
  decision_score: number
  action: string
  status: 'pending' | 'resolved' | 'skipped'
  raw_return: number | null
  alpha_return: number | null
  holding_days: number | null
  reflection: string | null
  direction_correct: number | null
}

export interface ReflectionStats {
  ticker: string
  total: number
  resolved: number
  pending: number
  hit_rate: number          // 方向命中率
  avg_alpha: number         // 平均 alpha
}
```

### 2.7 文件清单

#### 新增文件（5 个）
| 文件 | 职责 | 行数估计 |
|---|---|---|
| `backend/app/reflection/__init__.py` | 模块导出 | 10 |
| `backend/app/reflection/returns.py` | 收益计算（`fetch_returns`） | 60 |
| `backend/app/reflection/reflector.py` | LLM 反思调用 + Prompt | 50 |
| `backend/app/reflection/store.py` | DB 读写（`store_decision_memo`/`get_past_reflections`/`resolve_pending`） | 120 |
| `frontend/src/ReflectionPanel.tsx` | 前端反思展示组件 | 150 |

#### 修改文件（6 个）
| 文件 | 改动 |
|---|---|
| `backend/app/auth.py` `_init_db()` | 新增 `reflection_memos` 表 + 2 个索引 |
| `backend/app/graph/state.py` | `AgentState` 加 `past_reflections: str` 字段 |
| `backend/app/graph/nodes.py` `collect_data` | 末尾读 `past_reflections` 写入 ctx |
| `backend/app/graph/nodes.py` `finalize` | 末尾调 `store_decision_memo` |
| `backend/app/graph/builder.py` | `collect_data` 前插入 `resolve_pending_reflections` 节点（或并入 collect_data 开头） |
| `backend/app/agents/base.py` | `_call_structured` 加 `_reflection_block` 注入 |
| `backend/app/main.py` | 新增 `GET /api/reflection/{ticker}` 和 `GET /api/reflection/stats/{ticker}` |
| `frontend/src/types.ts` | 加 `ReflectionMemo` / `ReflectionStats` 类型 |
| `frontend/src/HistoryPage.tsx` | 详情视图嵌入 `<ReflectionPanel>` |

### 2.8 时间估计

| 阶段 | 工时 |
|---|---|
| DB schema + store.py | 0.5 天 |
| returns.py + reflector.py | 0.5 天 |
| graph 集成（3 节点改造） | 0.5 天 |
| prompt 调参与验证 | 0.5 天 |
| 前端 ReflectionPanel + API | 1 天 |
| 测试（mock 历史数据走通全流程） | 0.5 天 |
| **合计** | **3.5 天** |

---

# 方案2：分析师 Agent 化（自主工具调用循环）

> 参考：TradingAgents 的 `graph/setup.py` + `graph/conditional_logic.py`（LangGraph 条件边+ToolNode 循环）；ai-hedge-fund 的 `signals/llm_agent.py`（预喂 snapshot，无工具循环）。

---

## 一、源码调研结论（两种实现对比）

### 1.1 TradingAgents 的工具循环（LangGraph 原生）

**不是** `create_react_agent`，而是**手写条件边 + ToolNode**：

```python
# graph/setup.py — 每个分析师三件套
workflow.add_node(spec.agent_node, analyst_factory())    # LLM 节点
workflow.add_node(spec.clear_node, create_msg_delete())  # 清消息节点
workflow.add_node(spec.tool_node, tool_nodes[spec.key])  # ToolNode（LangGraph 内置）

# 关键：条件边实现 ReAct 循环
workflow.add_conditional_edges(
    current_analyst,                                    # 从分析师节点出发
    conditional_logic.should_continue_market,           # 路由函数
    [current_tools, current_clear],                     # 两个去向
)
workflow.add_edge(current_tools, current_analyst)       # 工具结果回到分析师
```

```python
# graph/conditional_logic.py — 路由逻辑（极简）
def should_continue_market(self, state):
    last_message = state["messages"][-1]
    if last_message.tool_calls:        # LLM 想调工具
        return "tools_market"          # → 去工具节点
    return "Msg Clear Market"          # → 工具调完，清消息，进下一个分析师
```

**分析师节点内部**（`agents/analysts/market_analyst.py`）：
```python
def market_analyst_node(state):
    tools = [get_stock_data, get_indicators, get_verified_market_snapshot]
    prompt = ChatPromptTemplate.from_messages([...])
    chain = prompt | llm.bind_tools(tools)    # 绑定工具到 LLM
    result = chain.invoke(state["messages"])
    report = result.content if len(result.tool_calls) == 0 else ""  # 无工具调用=出报告
    return {"messages": [result], "market_report": report}
```

**循环机制**：LLM 输出有 `tool_calls` → 走 ToolNode 执行 → 结果回 LLM → 再判断 → 直到 LLM 不再要工具 → 出报告。

### 1.2 ai-hedge-fund 的实现（无工具循环）

```python
# signals/llm_agent.py — LLMAgent 基类
class LLMAgent(AlphaModel):
    def predict(self, ticker, date, data_client):
        snapshot = self.build_snapshot(ticker, date, data_client)  # 预先构建数据快照
        system = self.get_system_prompt()
        user = self.build_user_prompt(snapshot)     # 快照渲染成文本
        response = self._llm.complete(system, user) # 单次 LLM 调用，无工具
        parsed = self._parse(response)
        return self._to_signal(...)
```
**特点**：数据层在 `build_snapshot` 一次性拉齐（point-in-time 防未来函数），LLM 只做一次推理。**这就是 FinanceCrew 当前的模式**（`collect_data` 预喂 → 分析师 `_call_structured`）。

### 1.3 两种实现优劣对比

| 维度 | TradingAgents（工具循环） | ai-hedge-fund（预喂快照） |
|---|---|---|
| 数据获取 | LLM **自主决定**调哪些工具、什么顺序、调几次 | 代码**预先**拉全量数据塞进 prompt |
| 适应性 | 能针对异常情况深挖（如某指标异常→多查几天） | 固定数据集，无法动态扩展 |
| Token 成本 | 高（多轮工具调用，每轮带历史消息） | 低（单次调用） |
| 延迟 | 高（3-8 轮往返） | 低（1 次） |
| 可控性 | 低（LLM 可能乱调工具、死循环） | 高（确定性数据流） |
| 实现复杂度 | 高（条件边、ToolNode、消息清理、防死循环） | 低 |
| 适合场景 | 研究/探索性分析、数据稀疏需补充 | 批量、标准化、生产环境 |

**结论**：FinanceCrew 当前是生产系统（多用户、SSE 流式、延迟敏感），**不宜全面切到工具循环**。应采用**混合模式**：保留预喂快照作为基线，新增"Agentic 模式"作为可选增强。

### 1.4 FinanceCrew 现状

```
collect_data（预喂全部数据到 ctx）
   ↓ fan_out (Send API 并行)
run_analyst × 5
   每个 analyst.analyze(ctx) → 从 ctx 取数据 → 拼 prompt → 单次 LLM 结构化输出
   ↓ aggregate
debate → consensus → risk → trader → finalize
```
**问题**：(1) 分析师被动消费数据，无法主动追问；(2) 所有分析师拿同样的 ctx，无法按需取数；(3) 数据缺失时（如港股无龙虎榜）分析师只能写"数据不可用"，无法主动找替代数据源。

---

## 二、目标架构设计

### 2.1 当前 vs 目标架构对比

```
【当前：数据预喂模式】
collect_data ──> [analyst.analyze(ctx)] ×5  ──> aggregate
                  ↑ 被动消费 ctx，单次 LLM

【目标：混合模式（保留预喂 + 新增 Agentic）】
                          ┌─ mode="standard" ─> 现有预喂路径（默认，快）
collect_data ──> router ──┤
                          └─ mode="agentic" ──> [agentic_analyst ×5] ──> aggregate
                                                ↑ 每个分析师自带工具循环
                                                  LLM 自主调 tool 深挖数据
                                                  受 max_iterations 上限保护
```

**路由**：`collect_data` 后根据 `state["mode"]`（默认 `"standard"`）决定走哪条。两种模式产出的都是 `AnalystView`，下游完全兼容。

### 2.2 工具调用循环实现方案（手写，不引框架）

**选型决策：手写轻量循环，不用 `create_react_agent`**。理由：
1. FinanceCrew 已用 LangGraph，但分析师内部的工具循环用 LangGraph 子图过重（要定义子 StateGraph）。
2. `create_react_agent` 是 LangChain 封装，黑盒、难定制 max_iterations 和 fallback。
3. 手写循环 < 40 行，完全可控，易测试。

```python
# 新文件 backend/app/agents/agentic_base.py
from typing import Any
from langchain_core.tools import BaseTool

class AgenticAnalyst(Agent):  # 继承现有 Agent，复用 _call_structured fallback
    """带工具调用循环的分析师基类。
    
    循环：LLM 决策 → 调工具 → 结果回灌 → 再决策，直到 LLM 给最终结论或达上限。
    """
    tools: list[BaseTool] = []
    max_iterations: int = 6      # 防死循环硬上限
    
    def analyze(self, context: dict[str, Any]) -> AnalystView:
        model = self.llm._build_model()
        if model is None:
            return self._fallback_mock(context)  # 无 key 走 mock
        
        bound = model.bind_tools(self.tools)
        messages = self._build_initial_messages(context)
        
        for i in range(self.max_iterations):
            resp = bound.invoke(messages)
            messages.append(resp)
            
            if not resp.tool_calls:    # LLM 给最终答案了
                return self._parse_final(resp.content, context)
            
            # 执行工具调用
            for tc in resp.tool_calls:
                tool_result = self._exec_tool(tc)
                messages.append(tool_result)  # ToolMessage
        
        # 达上限仍未完成：用已有信息强制出结论
        return self._force_conclude(messages, context)
    
    def _exec_tool(self, tool_call) -> Any:
        """执行单个工具调用，返回 ToolMessage。异常时返回错误信息不中断。"""
        from langchain_core.messages import ToolMessage
        try:
            tool = next((t for t in self.tools if t.name == tool_call["name"]), None)
            if not tool:
                return ToolMessage(content=f"未知工具: {tool_call['name']}", ...)
            result = tool.invoke(tool_call["args"])
            return ToolMessage(content=str(result), tool_call_id=tool_call["id"])
        except Exception as e:
            return ToolMessage(content=f"工具执行失败: {e}", tool_call_id=tool_call["id"])
    
    def _force_conclude(self, messages, context) -> AnalystView:
        """达迭代上限：让 LLM 基于已有信息出结构化结论。"""
        prompt = "已达工具调用上限，请基于已获取的信息给出最终分析结论。" + SCORE_HINT
        return self._call_structured(prompt + "\n" + self._summarize_tool_results(messages), context)
```

### 2.3 每个分析师的工具集定义

```python
# backend/app/agents/agentic_analysts.py（新文件）
class AgenticMacroAnalyst(AgenticAnalyst):
    role = "macro"
    title = "宏观分析师（Agentic）"
    tools = [
        macro_tools.get_index_quote,        # 大盘指数（上证/深证/创业板）
        macro_tools.get_north_flow,         # 北向资金
        macro_tools.get_sector_flow,        # 行业资金流
        macro_tools.get_margin_data,        # 融资融券余额
        macro_tools.web_search_macro,       # 政策/宏观新闻搜索
    ]

class AgenticFundamentalAnalyst(AgenticAnalyst):
    role = "fundamental"
    title = "基本面分析师（Agentic）"
    tools = [
        fin_tools.get_financials,           # 财务摘要（现有）
        fin_tools.get_industry_compare,     # 行业横向对比
        fin_tools.get_valuation_dcf,        # DCF 估值
        fin_tools.get_financial_history,    # 多期财务历史（需新增）
    ]

class AgenticTechnicalAnalyst(AgenticAnalyst):
    role = "technical"
    title = "技术面分析师（Agentic）"
    tools = [
        tech_tools.get_kline,               # K线（现有）
        tech_tools.get_indicators,          # 计算技术指标（RSI/MACD/BOLL）
        tech_tools.get_support_resistance,  # 支撑压力位（需新增）
        tech_tools.get_volume_analysis,     # 量价分析
    ]

class AgenticSentimentAnalyst(AgenticAnalyst):
    role = "sentiment"
    title = "情绪面分析师（Agentic）"
    tools = [
        sent_tools.get_news,                # 个股新闻
        sent_tools.get_social_sentiment,    # 东财人气榜+雪球
        sent_tools.get_market_news,         # 实时快讯
        sent_tools.web_search_event,        # 事件搜索
    ]

class AgenticCapitalAnalyst(AgenticAnalyst):
    role = "capital"
    title = "资金面分析师（Agentic）"
    tools = [
        cap_tools.get_lhb,                  # 龙虎榜（现有）
        cap_tools.get_main_force,           # 主力资金流
        cap_tools.get_institution_holdings, # 机构持仓（需新增）
    ]
```

**工具实现**：直接包装现有 `datalayer` 函数为 `@tool`，大部分已在 `tools.py` 里（`get_quote`/`get_kline`/`get_financials` 等）。需新增的是分析师专属的细粒度工具（如 `get_indicators` 单独算 RSI）。

### 2.4 与反思学习系统打通

**双向闭环**：
1. **读（注入）**：`AgenticAnalyst._build_initial_messages` 把 `context["past_reflections"]` 注入 system prompt（同方案1）。
2. **写（产出可被反思）**：Agentic 模式产出的 `AnalystView` 与标准模式结构完全一致，`finalize` 无差别调 `store_decision_memo`。

**增强点**：Agentic 分析师可在工具循环中**主动调取历史反思**：
```python
# 注册一个反思查询工具
@tool
def get_past_decisions(ticker: str) -> str:
    """查询该标的历史决策记录与反思教训。"""
    # 调 reflection.store.get_past_reflections
    ...
```
这样 LLM 在分析时能自主决定是否参考历史（而非被动注入）。

### 2.5 向后兼容（现有模式保留）

**策略：双模式共存，默认 standard，用户可选 agentic**。

```python
# graph/state.py 新增
class AgentState(TypedDict, total=False):
    mode: str  # "standard"（默认）| "agentic"

# graph/builder.py 修改
def build_graph():
    g = StateGraph(AgentState)
    g.add_node("collect_data", collect_data)
    g.add_node("run_analyst", run_analyst)              # 现有
    g.add_node("run_agentic_analyst", run_agentic_analyst)  # 新增
    g.add_node("aggregate_views", aggregate_views)
    ...
    g.add_edge(START, "collect_data")
    # 条件路由：按 mode 分流
    g.add_conditional_edges(
        "collect_data",
        route_by_mode,  # 新增路由函数
        {"standard": "run_analyst", "agentic": "run_agentic_analyst"},
    )
    g.add_edge("run_analyst", "aggregate_views")
    g.add_edge("run_agentic_analyst", "aggregate_views")
    ...
```

**API 层**：
```python
# models.py AnalysisRequest 新增字段
class AnalysisRequest(BaseModel):
    ticker: str
    topic: Optional[str] = None
    mode: str = "standard"  # "standard" | "agentic"
```

**前端**：分析页加一个开关「深度分析模式（Agentic，较慢但更深入）」。

### 2.6 流式展示增强

Agentic 模式的工具调用过程可通过 SSE 推给前端，让用户看到分析师"在思考、在查数据"：
```python
# SSE 新增事件类型
{"type": "tool_call", "analyst": "technical", "tool": "get_kline", "args": {...}}
{"type": "tool_result", "tool": "get_kline", "summary": "获取120日K线..."}
```

### 2.7 文件清单

#### 新增文件（4 个）
| 文件 | 职责 | 行数估计 |
|---|---|---|
| `backend/app/agents/agentic_base.py` | `AgenticAnalyst` 基类（工具循环） | 120 |
| `backend/app/agents/agentic_analysts.py` | 5 个 Agentic 分析师子类 + 工具集绑定 | 150 |
| `backend/app/agents/analyst_tools.py` | 分析师专属工具（包装 datalayer，细粒度） | 200 |
| `frontend/src/AgenticTrace.tsx`（可选） | 工具调用过程展示组件 | 100 |

#### 修改文件（5 个）
| 文件 | 改动 |
|---|---|
| `backend/app/graph/state.py` | `AgentState` 加 `mode: str` |
| `backend/app/graph/builder.py` | 加 `run_agentic_analyst` 节点 + `route_by_mode` 条件边 |
| `backend/app/graph/nodes.py` | 加 `run_agentic_analyst` / `fan_out_agentic` / `route_by_mode` |
| `backend/app/models.py` | `AnalysisRequest` 加 `mode` 字段 |
| `backend/app/main.py` | `create_analysis` / `stream_analysis` 透传 `mode`；SSE 加 `tool_call` 事件 |
| `frontend/src/types.ts` | `AnalysisRequest` 加 `mode` |
| `frontend/src/AnalyzePage.tsx` | 加模式开关 toggle |

### 2.8 时间估计

| 阶段 | 工时 |
|---|---|
| `AgenticAnalyst` 基类 + 工具循环 | 1 天 |
| 5 个分析师工具集定义 + 工具实现 | 1.5 天 |
| graph 双模式路由集成 | 0.5 天 |
| SSE 工具调用事件推送 | 0.5 天 |
| 前端模式开关 + Trace 展示 | 1 天 |
| 测试（工具循环稳定性、死循环防护、fallback） | 1 天 |
| **合计** | **5.5 天** |

---

## 三、两方案联动与实施顺序

```
建议顺序：先方案1（反思学习），再方案2（Agent化）
            │
            ├─ 方案1 独立可用（3.5天），立即产生价值
            │   ↓
            ├─ 方案2 在方案1基础上（2.5天），Agentic 分析师能主动查历史反思
            │   ↓
            └─ 合计 6 天（有重叠），可并行前端开发
```

**联动点**：
1. 方案2 的 `AgenticAnalyst` 注入 `past_reflections`（方案1 产出）
2. 方案2 可注册 `get_past_decisions` 工具，让 LLM 主动查历史
3. 方案1 的反思 prompt 可引用"当时用的是 standard 还是 agentic 模式"，帮助判断哪种模式更准

---

## 四、风险与对策

| 风险 | 对策 |
|---|---|
| A股 holding_days 收益数据延迟（停牌、节假日） | `fetch_returns` 价格不够返回 None，pending 留下次；超 30 天未结算标记 `skipped` |
| LLM 反思质量不稳定 | 限 2-4 句 + 结构化 prompt；存原文不二次加工；方向判定用确定性规则不用 LLM |
| Agentic 死循环/乱调工具 | `max_iterations=6` 硬上限；单工具异常不中断；达上限 `_force_conclude` |
| Agentic 模式 token 成本飙升 | 默认 standard；Agentic 作为可选；监控单次分析 token 消耗，超阈值告警 |
| 港股/美股无 A股数据源 | 工具按市场返回"不支持"提示；反思基准自动切换（恒生/标普） |
