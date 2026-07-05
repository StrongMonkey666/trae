# 股票量化回测系统（Quant Platform）V2.0

按 [项目计划书](./股票量化回测系统_项目计划书\(1\).docx) 落地。

## 当前进度

| 里程碑 | 模块 | 状态 |
|------|------|------|
| M1 | 数据获取系统 + 统一数据服务层 | ✅ 已完成 |
| M2 | 自然语言选股 + 回测引擎 | ⏳ 待开发 |
| M3 | 模拟持仓系统 + 一键部署 | ⏳ 待开发 |
| M4 | 事件总线 + 邮件提醒 + Web 界面 | ⏳ 待开发 |

## 项目结构

```
.
├── config/
│   └── settings.yaml              # 全局配置（数据源优先级、调度任务等）
├── data/                          # 运行时数据（gitignored）
│   ├── sqlite/quant.db
│   └── hdf5/market.h5
├── logs/                          # 运行日志
├── scripts/
│   ├── init_db.py                 # 初始化数据库
│   ├── update_data.py             # 手动触发同步
│   └── run_scheduler.py           # 启动后台调度器
├── src/quant_platform/
│   ├── data_acquisition/          # 实时股票数据获取系统
│   │   ├── sources/
│   │   │   ├── base.py            # DataSourceBase 抽象基类
│   │   │   ├── akshare_source.py  # AKShare（默认，免费）
│   │   │   ├── tushare_source.py  # Tushare（可选，需 token）
│   │   │   └── eastmoney_source.py# 东方财富（兜底）
│   │   └── cleaner.py             # 复权/停牌/缺失值处理
│   ├── data_service/              # 统一数据服务层
│   │   ├── storage.py             # SQLite + HDF5
│   │   ├── unified_api.py         # UnifiedDataService
│   │   └── scheduler.py           # APScheduler
│   └── utils/                     # 配置/日志/交易日历/异常
└── tests/                         # 单元测试
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

12 个用例覆盖：数据源契约、清洗逻辑、交易日历、SQLite/HDF5 存储、统一服务降级与失败处理。
