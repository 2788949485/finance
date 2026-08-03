# FinanceCrew - 金融智能体团队

多智能体驱动的 A 股投研与交易决策框架。

FinanceCrew 模拟一个完整的投研团队（宏观分析师、基本面分析师、技术面分析师、情绪面分析师、资金面分析师、风控经理、交易员），通过多角色独立分析、多空辩论与共识机制，将"机构级投研流程"自动化，输出可解释、可回溯、带风控约束的交易决策。

技术栈：LangChain 智能体 + FastAPI 后端 + React 前端 + 腾讯行情/akshare 数据 + SQLite 存储。大模型支持任意 OpenAI 兼容接口（DeepSeek、OpenAI、通义千问、Moonshot、本地 Ollama），用户可在前端自行配置。

## 快速开始

### 1. 启动后端

```bash
cd backend
uv venv .venv                     # 首次：创建虚拟环境
uv pip install -r requirements.txt  # 首次：安装依赖
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2. 构建前端（可选，开发模式可用 vite dev）

```bash
cd frontend
npm install        # 首次
npm run build      # 构建到 dist，由后端自动托管
```

### 3. 访问

打开 http://localhost:8000 ，进入"模型配置"页填写大模型 API Key（支持任意 OpenAI 兼容服务），然后在"投研分析"页输入 A 股代码（如 600519）开始分析。

## 项目结构

```
backend/
  app/
    main.py        # FastAPI 入口（REST API + 前端静态托管）
    config.py      # LLM 可配置系统（SQLite 持久化）
    llm.py         # LangChain ChatOpenAI 客户端（OpenAI 兼容）
    pipeline.py    # 编排流水线：研究->辩论->共识->风控->交易计划
    agents/
      base.py      # 智能体基类（ChatPromptTemplate + 结构化输出）
      analysts.py  # 五位分析师（宏观/基本面/技术面/情绪面/资金面）
      risk.py      # 风控经理（一票否决权）
      trader.py    # 交易员（执行计划）
    data/
      fetcher.py   # 数据层（腾讯行情/历史K线 + akshare 财务/龙虎榜/新闻）
    memory.py      # 分析历史（SQLite）
  data/            # 运行时数据（SQLite 数据库，已 gitignore）
frontend/
  src/App.tsx      # 前端（投研分析/历史记录/模型配置）
```

## API

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/health | GET | 健康检查 |
| /api/config | GET/PUT | 读取/保存 LLM 配置（Key 脱敏） |
| /api/providers | GET | 服务商预设列表 |
| /api/analysis | POST | 运行投研分析 {ticker, topic?} |
| /api/history | GET | 分析历史 |

## 免责声明

本项目为投资研究辅助工具，输出由 AI 智能体自动生成，仅供参考，不构成任何投资建议。市场有风险，投资需谨慎。

## 文档

- [产品说明文档](PRODUCT_SPEC.md) - 产品定位、架构、路线图
