# 麦麦自主规划插件

让 AI Bot 拥有自己的生活日程，自动生成符合人设的每日活动安排。

## 功能特性

- **智能日程生成** - 基于 Bot 人设通过 LLM 自动生成每日计划
- **定时自动生成** - 每天指定时间自动生成新日程（默认 00:30）
- **自然对话融入** - 在对话中自然提到当前在做什么
- **多种展示格式** - 支持文字和图片两种日程查看方式
- **SQLite存储** - 高性能数据库存储，支持快速查询
- **LRU缓存** - 智能缓存机制，减少重复计算

## 安装

### 依赖要求

确保安装了以下Python包：

```bash
pip install Pillow toml
```

### 字体要求

图片生成功能需要中文字体支持。插件会自动查找以下字体：
- `/usr/share/fonts/truetype/wqy/wqy-microhei.ttc` (推荐)
- `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`
- 其他系统字体

在Ubuntu/Debian上安装字体：
```bash
sudo apt-get install fonts-wqy-microhei
```

## 快速开始

### 自动生成日程

对 Bot 说：
```
"帮我生成今天的日程"
```

### 查看日程

```bash
/plan status   # 详细文字格式
/plan list     # 图片格式
```

### 管理日程

```bash
/plan clear              # 清理旧日程
/plan delete <ID或序号>   # 删除指定日程
/plan help               # 查看帮助
```

## 配置说明

配置文件：`config.toml` (v3.0.0)

```toml
[plugin]
enabled = true  # 是否启用插件

[autonomous_planning]
cleanup_interval = 3600       # 清理间隔（秒）
cleanup_old_goals_days = 30   # 保留历史记录天数

[autonomous_planning.schedule]
# 日程功能开关
inject_schedule = true        # 对话时自然提到当前活动
auto_generate = true          # 询问日程时自动检查并生成

# 生成质量控制
use_multi_round = true        # 启用多轮生成机制
max_rounds = 2                # 最多尝试轮数（1-3）
quality_threshold = 0.85      # 质量阈值（0.80-0.90）

# 生成参数
min_activities = 8            # 最少活动数量
max_activities = 15           # 最多活动数量
min_description_length = 15   # 描述最小长度
max_description_length = 50   # 描述最大长度
max_tokens = 8192             # 最大token数
generation_timeout = 180.0    # 生成超时时间（秒）

# 缓存配置
cache_ttl = 300               # 缓存TTL（秒）
cache_max_size = 100          # 缓存最大条目数

# 定时自动生成
auto_schedule_enabled = true  # 启用定时自动生成
auto_schedule_time = "00:30"  # 每天生成时间（HH:MM）
timezone = "Asia/Shanghai"    # 时区设置

# 权限配置
admin_users = []              # 管理员QQ号列表，留空则所有人可用

# 自定义模型（可选）
[autonomous_planning.schedule.custom_model]
enabled = false
model_name = ""
api_base = ""
api_key = ""
provider = ""
temperature = 0.7
```

### 配置项说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enabled` | true | 插件总开关 |
| `cleanup_interval` | 3600 | 清理检查间隔（秒） |
| `cleanup_old_goals_days` | 30 | 保留历史天数 |
| `inject_schedule` | true | 对话时提到当前活动 |
| `auto_generate` | true | 自动检查并生成日程 |
| `use_multi_round` | true | 多轮生成优化 |
| `max_rounds` | 2 | 最多尝试轮数 |
| `quality_threshold` | 0.85 | 质量阈值 |
| `min_activities` | 8 | 最少活动数 |
| `max_activities` | 15 | 最多活动数 |
| `max_tokens` | 8192 | 最大token数 |
| `generation_timeout` | 180 | 生成超时（秒） |
| `cache_ttl` | 300 | 缓存有效期（秒） |
| `cache_max_size` | 100 | 缓存最大条目 |
| `auto_schedule_enabled` | true | 定时生成开关 |
| `auto_schedule_time` | "00:30" | 定时生成时间 |
| `timezone` | "Asia/Shanghai" | 时区 |
| `admin_users` | [] | 管理员列表 |

## 文件结构

```
autonomous_planning_plugin/
├── plugin.py              # 插件主文件（入口）
├── tools.py               # LLM工具组件
├── handlers.py            # 事件处理器
├── commands.py            # 命令处理器
├── cache.py               # LRU缓存模块
├── database.py            # SQLite数据库模块
├── config_manager.py      # 配置管理模块
├── _manifest.json         # 插件清单
├── config.toml            # 配置文件
├── planner/               # 核心规划模块
│   ├── goal_manager.py    # 目标管理器
│   ├── schedule_generator.py  # 日程生成器
│   └── auto_scheduler.py  # 定时调度器
├── utils/                 # 工具模块
│   ├── time_utils.py      # 时间处理工具
│   └── schedule_image_generator.py  # 图片生成器
├── assets/                # 图片资源
└── data/                  # 数据存储
    └── goals.db           # SQLite数据库
```

## 工作原理

```
用户请求/定时触发 → 检查今日是否已有日程 → LLM基于人设生成 → 保存到SQLite → 注入对话
```

**日程注入效果：**
- 用户："在干嘛？"
- Bot："这会儿正吃午饭呢"

## 常见问题

**Q: 如何修改已生成的日程？**
A: 使用 `/plan clear` 清理后重新生成

**Q: 为什么看不到日程？**
A: 运行 `/plan status` 检查，或手动让 Bot 生成

**Q: 如何禁用日程注入？**
A: 设置 `inject_schedule = false`

**Q: 如何修改定时生成时间？**
A: 修改 `auto_schedule_time` 配置项

**Q: 生成速度太慢怎么办？**
A: 设置 `use_multi_round = false` 和降低 `quality_threshold`

**Q: 如何限制只有管理员能使用命令？**
A: 设置 `admin_users = ["QQ号1", "QQ号2"]`

**Q: 如何使用自定义模型？**
A: 在 `[autonomous_planning.schedule.custom_model]` 中配置

## 版本历史

### v3.0.0 (2024-11)
- 代码架构重构：拆分为 tools/handlers/commands 模块
- 存储优化：从JSON迁移到SQLite数据库
- 新增LRU缓存机制
- 新增管理员权限控制
- 新增自定义模型支持
- 配置项扩展至26项
- 性能大幅提升

### v2.2.0
- 多轮生成机制
- 定时自动生成
- 图片日程展示

## License

AGPL-3.0
