# FinanceCrew - 金融智能体投研平台

多智能体驱动的 A 股投研平台：**对话式智能体 + 多智能体深度研报 + 实时行情可视化**。

## 核心功能

- **智能对话**：LangGraph ReAct 智能体，自主调用工具（实时行情/K线/财务/龙虎榜/新闻/投研流水线），基于真实数据回答；消息内嵌 K 线图与行情卡片
- **投研分析**：5 位分析师（宏观/基本面/技术面/情绪面/资金面）独立研判 -> 多空辩论 -> 共识评分 -> 风控审查（一票否决）-> 交易计划
- **用户系统**：注册/登录（JWT）、用户画像（风险偏好、自选股）
- **模型可配置**：任意 OpenAI 兼容接口（DeepSeek/OpenAI/通义/Ollama），前端配置
- **数据缓存**：行情/K线/财务等 SQLite 缓存（TTL 过期），重复查询不重复请求外部接口

技术栈：LangChain + LangGraph（状态图编排、Send 并行分析师、ReAct 对话）+ FastAPI + React/Vite/TS + SQLite + 腾讯行情/akshare 数据。

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

打开 http://localhost:8000 ，注册账号登录，在"模型配置"页填写大模型 API Key，然后进入"智能对话"直接提问（如：分析一下 600519）。

## 项目结构

```
backend/
  app/
    main.py        # FastAPI 入口（认证/投研/对话/行情 API + 前端托管）
    config.py      # LLM 可配置系统（SQLite 持久化）
    llm.py         # LangChain ChatOpenAI 客户端（OpenAI 兼容）
    auth.py        # JWT 认证 + 用户画像（风险偏好/自选股）
    chat.py        # 对话智能体（LangGraph ReAct + 会话存储）
    tools.py       # 智能体工具集（行情/财务/龙虎榜/新闻/投研）
    cache.py       # SQLite 数据缓存层（TTL 过期）
    graph/         # LangGraph 投研流水线（state/nodes/builder）
    agents/        # 分析师/风控/交易员角色
    data/          # 数据层（腾讯行情/K线 + akshare 财务/龙虎榜/新闻）
    memory.py      # 分析历史
  data/            # 运行时数据库（已 gitignore）
frontend/
  src/
    App.tsx        # 主界面（对话/分析/历史/配置 + 登录守卫）
    ChatPage.tsx   # 智能对话页
    QuoteCard.tsx  # 行情卡片（K线图 + 指标，消息内嵌）
    KLineChart.tsx # SVG 蜡烛图组件
    LoginPage.tsx  # 登录/注册
```

## API

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/auth/register, /login, /me | POST/GET | 注册/登录/当前用户 |
| /api/auth/profile | GET/PUT | 用户画像（风险偏好/自选股） |
| /api/config | GET/PUT | LLM 配置（Key 脱敏） |
| /api/chat | POST | 对话（ReAct 智能体） |
| /api/chat/session(s) | POST/GET | 新建/列出会话 |
| /api/chat/{id}/messages | GET | 会话消息 |
| /api/analysis | POST | 多智能体投研分析 |
| /api/quote/{symbol} | GET | 实时行情 + K线（前端画图） |
| /api/history | GET | 分析历史 |

## 免责声明

本项目为投资研究辅助工具，输出由 AI 智能体自动生成，仅供参考，不构成任何投资建议。市场有风险，投资需谨慎。

## 文档

- [产品说明文档](PRODUCT_SPEC.md) - 产品定位、架构、路线图
