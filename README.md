# FinanceCrew - 多智能体金融投研平台

多智能体驱动的 A股/港股/美股 投研平台：**对话式智能体 + 多智能体深度研报 + 实时行情可视化 + 投资组合管理 + 策略回测**。

## 核心功能

### 智能体
- **智能对话**：LangGraph ReAct 智能体，自主调用 9 种工具（行情/K线/财务/龙虎榜/新闻/行业对比/情绪面/估值/投研流水线），基于真实数据回答
- **多智能体投研**：5 位分析师（宏观/基本面/技术面/情绪面/资金面）独立研判 -> 多空辩论 -> 共识评分 -> 风控审查 -> 交易计划
- **记忆反哺**：用户画像与偏好注入所有分析师 prompt，影响评分方向和仓位建议

### 行情与数据
- **三市场覆盖**：A股/港股/美股实时行情、K线（日K+分时）、技术指标（MACD/KDJ/BOLL）
- **自选股**：侧栏 watchlist + 行情卡片星标按钮，三处同步
- **热门股票**：每日动态排序（涨幅前6），不固定列表
- **情绪面分析**：东财人气榜排名趋势 + 雪球关注度 + 量价资金动能 + 综合情绪评分
- **DCF估值**：三阶段现金流折现模型，季度数据自动年化，计算内在价值与偏离度

### 投资管理
- **投资组合**：持仓追踪（买入加权成本/卖出减仓）、实时盈亏、交易历史
- **策略回测**：3种策略（MA均线交叉/网格交易/买入持有），计算超额收益/最大回撤/胜率/权益曲线
- **行业对比**：同行 PE/PB/涨跌幅对比 + LLM 自动生成同行 + DB 持久化

### 预警系统
- **价格预警**：4种类型（价格突破/跌破、涨跌幅超限）
- **技术指标预警**：MA5金叉/死叉MA20、放量突破（量比）
- **实时通知**：30秒轮询 + 弹窗通知 + 可重新激活（re-arm）

### 安全
- **per-user LLM Key**：每个用户独立 API Key，AES-256-GCM 加密存储，前端永远脱敏
- **登录安全**：频率限制（5次失败锁定15分钟）、密码 PBKDF2-SHA256 哈希
- **反爬限流**：全局请求频率限制（60次/分钟），CORS 白名单
- **认证保护**：所有敏感 API 需登录（分析报告/LLM配置/投资组合/预警等）

### UI/UX
- **极简风格**：直角/细线/大留白/克制配色（参考 Linear/Bloomberg Terminal）
- **暗色/亮色主题**：一键切换
- **个人中心**：模型配置 + 用户画像 + 修改密码，三栏导航
- **PDF导出**：投研报告打印优化（分页控制、导航隐藏）
- **Docker部署**：多阶段构建，一行启动

技术栈：LangChain + LangGraph + FastAPI + React 19/Vite/TypeScript + SQLite + 腾讯行情/akshare 数据。

## 快速开始

### 1. 启动后端

```bash
cd backend
uv venv .venv
uv pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2. 构建前端

```bash
cd frontend
npm install
npm run build
```

### 3. 使用

打开 http://localhost:8000 ，注册账号登录，在"个人中心 > 模型配置"填写大模型 API Key，然后进入"智能对话"直接提问（如：分析一下 600519）。

### 4. Docker 部署

```bash
docker build -t financecrew .
docker run -p 8000:8000 -v financecrew-data:/app/backend/data financecrew
```

## 项目结构

```
backend/
  app/
    main.py          # FastAPI 入口（认证/投研/对话/行情/组合/回测/预警/估值 API + 前端托管）
    config.py        # LLM 配置管理（SQLite 持久化）
    llm.py           # LangChain ChatOpenAI 客户端（支持 per-user 配置）
    auth.py          # JWT 认证 + 用户画像 + per-user LLM Key 加密 + 频率限制
    chat.py          # 对话智能体（LangGraph ReAct + 会话存储 + SSE 流式）
    tools.py         # 智能体工具集（行情/财务/龙虎榜/新闻/行业/情绪/估值/投研）
    cache.py         # SQLite 数据缓存层（TTL 过期）
    alert.py         # 价格预警系统（CRUD + 技术指标 + 批量查询）
    valuation.py     # DCF 现金流折现估值模型
    portfolio.py     # 投资组合管理（持仓/买卖/盈亏/交易历史）
    backtest.py      # 策略回测（MA交叉/网格/持有）
    llm_compare.py   # 多 LLM 模型对比
    graph/           # LangGraph 投研流水线（state/nodes/builder）
    agents/          # 分析师/风控/交易员角色
    data/            # 数据层（腾讯行情/K线 + akshare 财务/龙虎榜/新闻/情绪）
    memory.py        # 分析历史
  test/              # pytest 单元测试（14项，覆盖 fetcher + alert）
frontend/
  src/
    App.tsx          # 主界面（8个标签页 + 登录守卫 + 预警铃铛）
    ChatPage.tsx     # 智能对话页（流式回复 + 热门轮播 + 行情卡片）
    QuoteCard.tsx    # 行情卡片（K线图 + 指标 + 星标按钮）
    KLineChart.tsx   # SVG 蜡烛图（日K/分时/MACD/KDJ/BOLL/全屏）
    QuotePage.tsx    # 行情页（搜索 + 对比 + 热门）
    PortfolioPage.tsx# 投资组合（持仓表 + 盈亏KPI + 交易记录）
    BacktestPage.tsx # 策略回测（权益曲线SVG + 交易记录）
    ProfilePage.tsx  # 个人中心（模型配置 + 用户画像 + 修改密码）
    AlertBell.tsx    # 全局预警通知（铃铛 + 轮询 + 弹窗 + CRUD）
    LoginPage.tsx    # 登录/注册
.github/workflows/
  ci.yml             # CI（后端 pytest + 前端 build + Docker build）
```

## API 一览

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/auth/register, /login, /me | POST/GET | 注册/登录/当前用户（频率限制） |
| /api/auth/profile | GET/PUT | 用户画像（风险偏好/自选股） |
| /api/auth/change-password | POST | 修改密码（需旧密码验证） |
| /api/auth/llm-config | GET/PUT | per-user LLM 配置（Key 加密存储） |
| /api/chat, /api/chat/stream | POST | 对话（ReAct 智能体 + SSE 流式） |
| /api/chat/session(s) | POST/GET/DELETE | 会话管理 |
| /api/analysis, /api/analysis/stream | POST | 多智能体投研分析（SSE 流式） |
| /api/quote/{symbol} | GET | 实时行情 + K线 + 技术指标 |
| /api/search/{q} | GET | 股票搜索（A股/港股/美股） |
| /api/hot | GET | 每日热门股票（涨幅排序） |
| /api/news/{symbol} | GET | 个股新闻 |
| /api/industry/{symbol} | GET | 行业对比（同行 PE/PB） |
| /api/sentiment/{symbol} | GET | 社交情绪面数据 |
| /api/dcf/{symbol} | GET | DCF 估值 |
| /api/portfolio | GET | 投资组合（持仓 + 实时盈亏） |
| /api/portfolio/buy, /sell | POST | 买入/卖出记录 |
| /api/portfolio/transactions | GET | 交易历史 |
| /api/backtest/{symbol} | GET | 策略回测（3种策略） |
| /api/alerts | GET/POST/DELETE | 价格预警 CRUD |
| /api/alerts/check | POST | 预警触发检查（30秒轮询） |
| /api/alerts/{id}/reactivate | POST | 重新激活已触发预警 |
| /api/llm-compare | POST | 多 LLM 模型对比 |
| /api/history | GET/DELETE | 投研分析历史 |

## 免责声明

本项目为投资研究辅助工具，输出由 AI 智能体自动生成，仅供参考，不构成任何投资建议。市场有风险，投资需谨慎，盈亏自负。
