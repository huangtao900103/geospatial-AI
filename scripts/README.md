# 脚本入口

在仓库根目录执行命令。HTTP API与数据路径不因目录整理改变。启动器和检查器优先使用项目 `.venv`；没有虚拟环境时，启动器使用当前Python，检查器使用 `python3`。

| 目录 / 文件 | 职责 |
| --- | --- |
| `serve.py` | 本地服务启动，默认端口8016；参数继续传给根目录 `server.py` |
| `check.cjs` | 语法、隔离后端测试、隔离前端契约检查的统一入口 |
| `production/` | 真实遥感数据生产、区域参考成果及不可变时序发布 |
| `maintenance/` | 演示样例扫描、清理计划与按计划应用 |
| `verification/` | 前端隔离契约，以及需单独运行的实际数据 / HTTP 验证 |

## 启动

```bash
npm start
npm start -- --port 8020
.venv/bin/python scripts/serve.py --port 8016 --db data/workspace.sqlite3
```

`python server.py ...` 与 `python -m backend ...` 也可使用。运行 `scripts/serve.py --help` 仅显示服务参数。

## 日常检查

```bash
npm run check:syntax
npm run check:backend
npm run check:ui
npm run check
```

- `check:syntax` 检查手写前端、脚本和Python语法，排除第三方 `dist/vendor/`。
- `check:backend` 使用 `unittest discover -s tests`，数据与HTTP服务由测试在临时目录内创建。
- `check:ui` 执行 `verification/` 下全部 `.mjs` 契约检查；默认不连接运行中的工作空间。场景工具注册核验从临时Workspace读取实际应用目录。设置 `SCENE_TEST_URL` 可显式切换该项为只读HTTP注册核验。
- `check` 默认依次运行全部三组；也可直接组合，例如 `node scripts/check.cjs --syntax --ui`。

这些检查验证代码与契约，不等同于真实浏览器、外部服务或像元读取验收。检查证据写到忽略的 `output/` 目录。

## 生产与发布

```bash
.venv/bin/python scripts/production/produce_xiantao_series.py --help
.venv/bin/python scripts/production/produce_xiantao_spectral.py --help
.venv/bin/python scripts/production/produce_xiantao_classification.py --help
.venv/bin/python scripts/production/produce_xiantao_agreement.py --help
.venv/bin/python scripts/production/produce_xiantao_pois.py --help
.venv/bin/python scripts/production/publish_agri_series.py --help
```

这些脚本分别生产实际时序、光谱、年度分类、跨产品核查、公开地址候选，并发布已验证时序。按脚本参数选择源目录、输出目录和验证模式；实际运行可能访问提供方、读取像元或发布成果。`--help`不执行生产。

## 维护与实际验证

```bash
.venv/bin/python scripts/maintenance/cleanup_samples.py --help
```

维护脚本的扫描、计划输出和应用参数分别声明；应用已有计划时要求备份目录。文档和脚本中的普通检查命令不会自动清理工作空间。

`verification/*.py` 用于实际像元读取、已有成果复核或HTTP分析链路。它们不包含在 `npm run check` 中，部分会写入测试任务或读取外部数据，应按文件说明和输入参数单独执行。`verify_v16_indices.py`为公开像元实测入口，`verify_collection_v18.py`为影像时序集合实测入口，`verify_processing_http_v18.py`与`verify_agriculture_v16.py`为HTTP链路验收。
