# llama.cpp API 启动器

基于 Python + Tkinter 的本地大模型服务管理工具。图形化操作 `llama-server`（llama.cpp 的 HTTP API 服务），无需命令行即可完成模型加载、参数配置、服务启停与版本更新。仅依赖 Python 标准库。

## 功能

- **服务启停**：选择 `.gguf` 模型与参数方案，一键启动 / 强制停止 `llama-server`，实时查看日志。
- **版本管理**：从 GitHub（`ggml-org/llama.cpp` releases）拉取官方预编译包，按平台筛选并下载解压替换，完成版本更新。
- **多方案配置**：每个 JSON 文件含 `cpu` / `gpu` / `mix` 三套方案，可随时切换。

## 快速开始

依赖：Python 3（仅标准库，无需第三方包）。

```bash
python main.py
```

1. 在界面中设置 **llama.cpp 目录**（含 `llama-server.exe`）与 **模型目录**。
2. 点击 **方案集** 从 `configs/` 选择适合本机的方案文件。
3. 选择模型与方案（`cpu` / `gpu` / `mix`），点击 **▶ 启动**。

配置自动保存到程序目录下的 `llama_launcher_config.json`。窗口图标读取 `assets/icon.ico`（可选）。

## 目录结构

```
llama API launcher/
├── main.py                 # 统一主入口
├── app.py                  # 主图形界面（LlamaLauncherApp）
├── version_dialog.py       # 版本管理对话框（VersionManagerDialog）
├── README.md
├── assets/                 # 图标等资源（可选）
│   └── icon.ico
├── configs/                # 内置方案集
│   └── 默认方案.json       # 默认参数集（i7 + RTX4060 + 16GB DDR5，端口 12345）
├── llama_launcher_config.json  # 运行时生成
└── core/                   # 核心逻辑模块
    ├── paths.py            # 路径检测、可执行文件查找、模型枚举
    ├── profiles.py         # JSON 方案的加载/保存/枚举
    ├── launcher.py         # 命令构建 build_command + 子进程封装 ServerRunner
    └── version_manager.py  # GitHub 发布解析、下载、解压替换
```

## 方案集格式

每个 JSON 文件顶层为方案名到方案字典的映射，常见方案名 `cpu` / `gpu` / `mix` 对应纯 CPU、纯 GPU、CPU+GPU 混合三种部署模式。

| 字段 | 类型 | 命令行 | 说明 |
|---|---|---|---|
| `context_size` | int | `-c` | 上下文长度（0 或空则省略） |
| `parallel` | int | `-np` | 并行解码数 |
| `batch_size` | int | `-b` | prompt 批处理大小 |
| `ubatch_size` | int | `-ub` | 物理批处理大小 |
| `gpu_layers` | int | `-ngl` | 卸载到 GPU 的层数，`-1` 为全部 |
| `threads` | int | `-t` | CPU 线程数 |
| `pooling` | string | `--pooling` | 嵌入池化方式（如 `mean`） |
| `chat_template` | string | `--chat-template` | 聊天模板 |
| `draft_model` | string | `-md` | 草稿模型路径，相对路径基于主模型目录 |
| `grammar_file` | string | `--grammar-file` | GBNF 语法文件路径 |
| `extra_args` | string | （透传） | 额外命令行参数，按 `shlex` 拆分 |
| `flash_attn` | bool | `-fa on` | Flash Attention |
| `cont_batching` | bool | `--cont-batching` | 连续批处理 |
| `mlock` | bool | `--mlock` | 锁定内存避免换页 |
| `no_mmap` | bool | `--no-mmap` | 不使用 mmap 加载模型 |
| `embedding` | bool | `--embedding` | 嵌入模式 |
| `reranking` | bool | `--reranking` | 重排序模式 |
| `jinja` | bool | `--jinja` | 使用 Jinja2 模板 |
| `verbose` | bool | `-v` | 详细日志 |

> 监听地址 `--host` 与端口 `--port` 不在方案文件中，由界面「监听地址」「端口」输入框单独设置（默认 `127.0.0.1:12345`，端口范围 1-65535），随主配置保存到 `llama_launcher_config.json`。

### 默认方案（i7 + RTX4060 + 16GB DDR5）

针对该硬件优化：

| 方案 | context_size | threads | gpu_layers | 适用场景 |
|------|--------------|---------|------------|---------|
| `cpu` | 8192 | 12 | 0   | 纯 CPU 推理，无显存占用 |
| `gpu` | 8192 | 0  | -1  | 全层卸载到 8GB 显存 |
| `mix` | 8192 | 6  | 15  | 部分层卸载，留出显存余量 |

8GB 显存下 `context_size=8192`、`batch_size=512` 较为稳妥；16GB 物理内存下 `mlock=false`（不锁定内存）。端口在界面单独设置。

## 设计原则

- **极简**：仅 Python 标准库，无需打包或虚拟环境。
- **模块化**：`core/` 承载纯逻辑，`app.py` / `version_dialog.py` 仅做 GUI 编排，`main.py` 为统一入口。
- **配置即数据**：所有可调参数集中在 `configs/*.json`，代码不内嵌硬编码参数。
- **跨平台**：路径用 `pathlib`，命令拆分用 `shlex` 按平台切分，Windows 下用 `CREATE_NO_WINDOW` 隐藏控制台。
