# 股票量化回测系统（Quant Platform）V2.0

按 [项目计划书](./股票量化回测系统_项目计划书\(1\).docx) 落地。

## 当前进度

| 里程碑 | 模块 | 状态 |
|------|------|------|
| M1 | 数据获取系统 + 统一数据服务层 | ✅ 已完成 |
| M2 | 自然语言选股 + LLM 对接 + 回测引擎 | ✅ 已完成 |
| M3 | 回测记录系统 + 模拟持仓系统 + 一键部署 | ✅ 已完成 |
| M4 | 事件总线 + 邮件提醒 + Web 界面 | ✅ 已完成（V1.0） |

## 项目结构

```
.
├── pytest.ini                     # pytest 配置（strict-markers + markers 注册）
├── config/
│   └── settings.yaml              # 全局配置（数据源、LLM、调度任务等）
├── data/                          # 运行时数据（gitignored）
│   ├── sqlite/quant.db
│   └── hdf5/market.h5
├── logs/                          # 运行日志
├── scripts/
│   ├── init_db.py                 # 初始化数据库
│   ├── update_data.py             # 手动触发数据同步
│   ├── run_scheduler.py           # 启动后台调度器
│   ├── run_selector.py            # 选股 CLI
│   ├── run_backtest.py            # 回测 CLI
│   ├── list_backtests.py          # 回测记录列表
│   ├── compare_backtests.py       # 多回测对比
│   ├── deploy.py                  # 一键部署：回测 → 模拟实例
│   ├── run_simulator.py           # 模拟持仓主循环
│   ├── run_web.py                 # 启动 Web 服务
│   └── smoke_test.py              # 端到端冒烟（fake 数据，无网络）
├── src/quant_platform/
│   ├── data_acquisition/          # M1：实时股票数据获取系统
│   │   ├── sources/
│   │   │   ├── base.py            # DataSourceBase 抽象基类
│   │   │   ├── akshare_source.py  # AKShare（默认，免费）
│   │   │   ├── tushare_source.py  # Tushare（可选，需 token）
│   │   │   └── eastmoney_source.py# 东方财富（兜底）
│   │   └── cleaner.py             # 复权/停牌/缺失值处理
│   ├── data_service/              # M1：统一数据服务层
│   │   ├── storage.py             # SQLite + HDF5
│   │   ├── unified_api.py         # UnifiedDataService
│   │   └── scheduler.py           # APScheduler
│   ├── llm/                       # M2：LLM 对接层
│   │   ├── base.py                # LLMClient 抽象基类
│   │   ├── openai_compatible.py   # OpenAI/DeepSeek/通义千问/Ollama
│   │   ├── prompt.py              # Prompt 模板
│   │   └── parser.py              # JSON 输出解析 + 重试
│   ├── selector/                  # M2：选股系统
│   │   ├── schema.py              # Condition / SelectorSpec
│   │   ├── engine.py              # 选股引擎
│   │   ├── templates.py           # 预置策略模板
│   │   ├── history.py             # 选股历史记录
│   │   └── service.py             # SelectorService 高层接口
│   ├── backtest/                  # M2：回测系统
│   │   ├── strategy.py            # StrategyConfig
│   │   ├── position.py            # Position / Trade
│   │   ├── allocator.py           # 4 种资金分配模型
│   │   ├── engine.py              # 回测主引擎
│   │   ├── metrics.py             # 绩效指标
│   │   └── records.py             # M3：回测记录（存/查/对比/部署）
│   ├── simulator/                 # M3：模拟持仓系统
│   │   ├── state.py               # 状态持久化（持仓/现金/成交/快照）
│   │   ├── executor.py            # 模拟撮合（PaperExecutor）
│   │   └── engine.py              # 实时主引擎（轮询/调仓/止盈止损）
│   └── utils/                     # 配置/日志/交易日历/异常
├── tests/                         # 测试套件（113 用例 + 6 network + 2 slow + 冒烟脚本）
│   ├── conftest.py                # 共享 fixtures：autouse reset、fake data service、network 探测
│   ├── test_data_acquisition.py   # 数据源契约 / 清洗 / 存储 / 降级
│   ├── test_selector.py           # 选股引擎 / 模板 / LLM 解析
│   ├── test_backtest.py           # 回测引擎 / 硬止损 / 资金分配
│   ├── test_simulator.py          # 模拟持仓 / 状态持久化 / 部署
│   ├── test_eventbus.py           # 事件总线 / 通配符 / 线程安全
│   ├── test_notify.py             # 邮件 / 模板 / mock SMTP
│   ├── test_web.py                # Web 路由 / 模板 / JSON API
│   ├── test_benchmark.py          # 正确性 + 性能基准（@pytest.mark.slow）
│   └── test_integration_network.py# 真实数据源集成（@pytest.mark.network）
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 初始化数据库与数据目录
python scripts/init_db.py

# 3. （可选）在 config/settings.yaml 中配置 Tushare token
#    tushare.enabled: true
#    tushare.token: "your_token"

# 4. 同步股票列表
python scripts/update_data.py --task stock_list

# 5. 同步某只股票的历史 K 线
python scripts/update_data.py --task history --code 600519

# 6. 拉取实时行情
python scripts/update_data.py --task realtime --code 600519,000001

# 7. 启动后台调度器（实时轮询 + 每日全量校验）
python scripts/run_scheduler.py
```

## 编程式使用

```python
from datetime import date, timedelta
from quant_platform.data_service.unified_api import UnifiedDataService

service = UnifiedDataService()

# 历史 K 线（前复权）
end = date.today()
start = end - timedelta(days=365)
df = service.get_history_data("600519", start=start, end=end)
print(df.tail())

# 实时行情
realtime = service.get_realtime_data(["600519", "000001"])
print(realtime)

# 股票列表
stocks = service.get_stock_list()
print(stocks.head())

# 财务数据
fin = service.get_financial_data("600519")
print(fin.tail())
```

## 数据源降级策略

按 `config/settings.yaml` 中 `data_service.source_priority` 的顺序逐个尝试：
- `akshare`（默认） → `tushare`（如启用） → `eastmoney`
- 第一个成功的源结果被使用并落盘到本地
- 所有源失败时，本地缓存仍可使用
- 数据源健康状态记录在 SQLite `data_source` 表

## 单元测试

```bash
PYTHONPATH=src pytest tests/ -v
```

**113 个用例**（默认跳过 6 个 network + 2 个 slow），覆盖：数据源契约、清洗逻辑、交易日历、SQLite/HDF5 存储、统一服务降级、条件匹配/序列化、LLM JSON 解析、资金分配模型、绩效指标、回测引擎（含硬止损触发）、事件总线、邮件通知、Web 路由。

更详细的测试策略（测试金字塔 / 冒烟 / 集成 / 性能基准）见下节。

## 测试策略（测试金字塔）

按速度 / 依赖从下到上组织，越底层越快、越稳定、越能跑：

| 层级 | 形式 | 文件 | 数量 | 速度 | 依赖 |
|------|------|------|------|------|------|
| **L1 单元测试** | pytest | `tests/test_*.py`（不含 network） | 113 | < 10s | 无 |
| **L2 端到端冒烟** | CLI 脚本 | `scripts/smoke_test.py` | 22 项断言 | < 3s | 无（fake 数据） |
| **L3 真实数据集成** | pytest `-m network` | `tests/test_integration_network.py` | 6（默认 skip） | 慢 | 公网 |
| **L4 性能基准** | `-m slow` 过滤 | `tests/test_benchmark.py::TestPerformance` | 2 | 数秒 | 无 |
| **L5 正确性基准** | `-k benchmark` 或全部跑 | `tests/test_benchmark.py::TestAllocator/Metrics/BacktestInvariants` | 12 | < 2s | 无 |
| **L6 手工** | Web / CLI | 浏览器或终端 | — | 人工 | 视场景 |

> `pytest tests/` 默认收集 119 个 = 99 单元 + 14 基准 + 6 network，跑 113（passes），skip 6（network）。L4 是 L1 的子集。

### 1. 一键跑全部

```bash
# 默认：99 单元 + 14 基准（L4+L5） = 113 passed；skip 6 个 network
PYTHONPATH=src pytest tests/ -v

# 只跑纯单元（不含 L4 性能基准，跳过 network）
PYTHONPATH=src pytest -m "not network and not slow" tests/

# 跑 L3 真实数据集成（需联网）
PYTHONPATH=src pytest -m network tests/

# 跑 L4 性能基准
PYTHONPATH=src pytest -m slow tests/

# 跑 L2 端到端冒烟（独立脚本，fake 数据）
python scripts/smoke_test.py
```

### 2. 端到端冒烟测试（`scripts/smoke_test.py`）

不依赖任何外网，用 fake 数据把整条链路打通：

```
[1] 选股引擎
[2] 回测引擎
[3] 回测记录 + 事件总线
[4] 一键部署
[5] 模拟引擎（tick + 快照）
[6] 邮件通知（mock SMTP）
[7] Web 路由（4 页面 + JSON API）
```

CI / 沙箱中**第一步**先跑这个，3 秒内就能发现"全链路哪里断掉了"。

### 3. 真实数据集成（`@pytest.mark.network`）

默认 **skip**。需要联网时手动启用：

```bash
PYTHONPATH=src pytest -m network tests/test_integration_network.py -v
```

- `test_akshare_list_stocks`  — 验证 AKShare 股票列表（>1000 只）
- `test_akshare_history`      — 验证 600519 一年日 K 线（>=200 根）
- `test_akshare_realtime`     — 验证实时行情字段
- `test_eastmoney_history`    — 验证东方财富兜底源
- `test_unified_service_end_to_end` — 同步 → 读本地缓存
- `test_backtest_with_real_data`   — 真实 K 线驱动回测

任何调用失败都会 `pytest.skip()`，不会让 CI 阻塞（避免"晚上跑 CI 撞上游限流"）。

### 4. 性能基准（`@pytest.mark.slow` / `@pytest.mark.benchmark`）

防止代码退化导致回测变慢：

```bash
PYTHONPATH=src pytest -m slow -v -s
```

- `test_backtest_50_stocks_1y_under_10s` — 50 只 × 1 年日线回测 < 10s
- `test_selector_5k_stocks_under_1s`     — 5000 只 × 5 条件选股 < 1s

阈值预留 3-5x buffer；机器变慢时再放宽。

### 5. 正确性基准（不需 marker，永远跑）

用**手算预期值**校验逻辑是否被改坏：

- **分配器**：等权 / 固定金额 / 评分权重（调和级数）/ 凯利公式
- **指标**：总收益、最大回撤、年化、夏普、胜率、平均持仓天数
- **回测不变量**：单调上涨 → 期末 > 初始；不交易 → 期末 = 初始；硬止损 → 触发

当回测逻辑重构时，这些断言能立即发现回归。

### 6. 自定义 Marker

| Marker | 含义 | 启用方式 |
|--------|------|----------|
| `@pytest.mark.network` | 真实数据源集成测试 | `-m network` |
| `@pytest.mark.slow` | 性能/基准测试（>1s） | `-m slow` |
| `@pytest.mark.benchmark` | 性能基准（带 `print` 报告耗时） | `-m slow` |

由 `pytest.ini` 统一注册（`--strict-markers`），CI 跑 `pytest` 默认只跑 L1+L2+L5。

### 7. 推荐开发流程

```bash
# 1) 改完代码先跑冒烟（3 秒）
python scripts/smoke_test.py

# 2) 单元 + 正确性基准
PYTHONPATH=src pytest tests/ -v

# 3) 改回测/分配/指标逻辑，必须看 L5 正确性基准是否还过
PYTHONPATH=src pytest tests/test_benchmark.py -v

# 4) 提交前（如可联网）跑一次真实数据
PYTHONPATH=src pytest -m network tests/test_integration_network.py -v

# 5) 性能回归（可选，>1s）
PYTHONPATH=src pytest -m slow -v -s
```

### 8. CI 最小配置（GitHub Actions 示例）

```yaml
- name: 冒烟（必跑）
  run: python scripts/smoke_test.py

- name: 单元 + 正确性基准（必跑）
  run: PYTHONPATH=src pytest tests/ -v

- name: 性能基准（仅 main / 定时任务）
  if: github.ref == 'refs/heads/main'
  run: PYTHONPATH=src pytest -m slow -v -s

- name: 真实数据集成（仅 main / 定时任务）
  if: github.ref == 'refs/heads/main'
  run: PYTHONPATH=src pytest -m network tests/ -v
```

## 启动 Web 界面

```bash
# 默认监听 127.0.0.1:5000
python scripts/run_web.py

# 自定义
python scripts/run_web.py --host 0.0.0.0 --port 8080
```

Web 包含 4 个页面：
- **仪表盘** `/` — 统计卡片 + 最近回测 + 模拟实例
- **回测管理** `/backtests/` — 列表 + 详情（绩效卡 + 权益曲线 + 交易明细）
- **模拟持仓** `/simulator/` — 实例列表 + 详情（实时权益走势 + 当前持仓 + 成交记录）
- **选股** `/selector/` — 预置模板 + 实时查询结果

回测详情页可一键「部署」为模拟实例（POST `/simulator/api/deploy/<id>`）。

## 模拟持仓（部署 + 运行）

```bash
# 1) 列出回测记录
python scripts/list_backtests.py

# 2) 对比多个回测
python scripts/compare_backtests.py 1 2 3

# 3) 一键部署：把回测 #1 部署为模拟实例
python scripts/deploy.py 1

# 4) 启动模拟持仓（按 --poll 秒轮询）
python scripts/run_simulator.py 1 --poll 3
```

部署后：
- 模拟实例的初始资金 = 回测的 final_equity
- 回测记录自动标记为「已部署」
- 每次 tick 实时盯市 + 止盈止损 + 按 freq 调仓
- 状态、成交、快照全部持久化到 SQLite，崩溃可恢复

## 选股与回测快速上手

```bash
# 1) 列出预置选股模板
python scripts/run_selector.py --list-templates

# 2) 用模板选股
python scripts/run_selector.py --template low_valuation

# 3) 用自然语言选股（需要先在 config/settings.yaml 配置 llm.api_key）
python scripts/run_selector.py --nl "PE 小于 20 且 ROE 大于 10% 的股票"

# 4) 用模板回测
python scripts/run_backtest.py --template low_valuation \
    --start 2024-01-01 --end 2024-12-31 --capital 1000000

# 5) 用自然语言回测
python scripts/run_backtest.py --nl "PE<20 ROE>10%" \
    --start 2024-01-01 --end 2024-12-31 --output result.json

# 6) 编程式回测
PYTHONPATH=src python -c "
from datetime import date
from quant_platform.backtest.engine import BacktestEngine
from quant_platform.backtest.strategy import StrategyConfig
from quant_platform.selector.templates import get_template
cfg = StrategyConfig(
    name='demo', start_date=date(2024,1,1), end_date=date(2024,12,31),
    initial_capital=1_000_000, selector=get_template('low_valuation'),
)
print(BacktestEngine().run(cfg).metrics.to_dict())
"
```

## 预置选股模板

| Key | 名称 | 条件 | 排序 | 说明 |
| --- | --- | --- | --- | --- |
| `low_valuation` | 低估值策略 | `pe_ttm<20 AND pb<3 AND roe>10` | PE 升序 | 经典价值投资 |
| `high_growth` | 高增长策略 | `revenue_growth>20 AND net_profit_growth>20 AND roe>15` | 净利润增速降序 | 业绩驱动 |
| `ma_bull` | 均线多头排列 | `close > ma_20 AND ma_20 > ma_60`（**跨字段**） | 涨幅降序 | 趋势跟随 |
| `volume_break` | 放量突破 | `turnover_rate>5 AND change_pct>3` | 涨幅降序 | 短线动量 |

> 注意：`ma_bull` 需要特征表里有 `ma_20` / `ma_60` 字段。`SelectorService._do_build_features`
> 会自动从历史 K 线计算 MA5/MA10/MA20/MA60，K 线缺失则留 NaN（视为不命中）。

## 跨字段比较（Cross-Field）

`Condition` 支持 `compare_field` 做"行内跨列"比较，用于表达均线多头、MACD 与零轴等
技术条件。两种写法等价：

```python
# 写法 1：显式 compare_field
Condition("close", ">", 0, compare_field="ma_20")

# 写法 2：value 写字段名字符串（LLM 友好，自动转 compare_field）
Condition.from_dict({"field": "close", "operator": ">", "value": "ma_20"})
```

LLM 输出 JSON 时直接用字符串 `value` 即可：

```json
{
  "conditions": [
    {"field": "close", "operator": ">", "value": "ma_20"},
    {"field": "ma_20", "operator": ">", "value": "ma_60"}
  ],
  "logic": "AND"
}
```

限制：跨字段不支持 `between`；任一侧为 NaN 视为不命中。

## 选股结果为空时：放宽建议

`SelectorEngine.suggest_relaxations(spec, features)` 在 `result.empty` 时给出最多 3 条建议：

- **drop**：去掉单条条件，看其他条件能命中多少
- **loosen**：放宽单条数值的阈值（10/25/50/100% 步长），看该条件单独能命中多少

Web 路由 `/selector/api/run` 命中为空时自动返回 `suggestions` 字段并展示在 UI 上。

