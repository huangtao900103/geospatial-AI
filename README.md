# 时空智能工作台

面向农业、林业、水利、交通等业务的一张图工作台。用户在地图上选择范围、上传业务数据或提出分析需求，平台通过已登记的数据源和算法完成计算，呈现定量结果、地图及可复核的下载文件。

当前版本：`0.18.0`。本项目提供本机单工作空间服务，默认入口为 **<http://127.0.0.1:8016/>**。现行说明从本文件和[文档导航](docs/README.md)开始；历次设计、案例与验收快照保留在[历史档案](docs/archive/)。

## 快速启动

需要 Python 3.9 或兼容环境，以及 Node.js 22.12及以上版本和npm。在项目根目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-raster.txt
npm ci
npm start
```

以上为 macOS/Linux 命令。`scripts/serve.py` 会优先使用项目 `.venv`，服务只绑定 `127.0.0.1`。前端使用 **Vue 3 + TypeScript + Pinia + Vite**。`npm start`先类型检查和构建，再启动Python服务；`dist/`是构建输出，不提交Git。依赖由`package-lock.json`固定，用`npm ci`安装。

其他启动方式：

```sh
npm start -- --port 8020
npm run build  # 直接启动Python前先构建前端
.venv/bin/python server.py --port 8016 --db data/workspace.sqlite3
.venv/bin/python -m backend --port 8016 --db data/workspace.sqlite3
```

启动后检查服务：

```sh
curl -fsS http://127.0.0.1:8016/api/health
```

健康接口反映本机服务状态；外部数据、地图服务和模型的实际可用性，以各自状态和具体任务的调用证据为准。

**克隆仓库不会自带本机业务数据库、已生产影像或私有配置。** `data/`、`output/`、`.venv/` 和浏览器验证缓存不进入版本库。新环境可恢复自己的数据备份，或通过上传、按需分析及主动运行生产脚本创建成果。

## 项目目录

```text
geospatial-AI/
├── server.py                  # 兼容启动入口，实际服务在 backend/server.py
├── backend/                   # Python后端
│   ├── server.py              # HTTP路由、工作空间装配和服务生命周期
│   ├── paths.py               # 统一项目根路径，避免目录迁移影响资源定位
│   ├── core/                  # 空间基础、地图、搜索、存储、核查和成果下载
│   ├── catalog/               # 来源目录、公开目录适配和算法输入契约
│   ├── integrations/          # 天地图代理、本机模型桥接与受控规划
│   ├── analysis/              # 原生栅格、光谱指数、分类和时序处理
│   ├── agriculture/           # 农业分析、作物产品、区域成果与样本研究
│   └── workflows/             # 专题、问答、执行任务、台账和答案组装
├── frontend/                  # Vue + TypeScript前端源码与Vite配置
│   ├── src/components/        # Vue页面、上传、弹窗与功能组件
│   ├── src/stores/            # Pinia共享状态
│   ├── src/services/          # HTTP请求、运行时响应校验与导航
│   ├── src/adapters/          # 地图和专业模块的生命周期及类型边界
│   ├── src/workspaces/        # 保留的Leaflet与专业业务模块
│   ├── e2e/                  # 隔离工作空间的真实浏览器回归
│   └── public/vendor/         # 本地Leaflet资源及许可
├── dist/                      # Vite生成的部署文件（Git忽略）
├── data_sources/              # 版本化数据来源清单、公开参考市界
├── map_sources/               # 版本化地名索引与公开参考水系
├── scripts/
│   ├── serve.py               # 选择运行环境并启动服务
│   ├── check.cjs              # 统一检查入口
│   ├── production/            # 主动执行的数据生产和发布脚本
│   ├── maintenance/           # 维护与清理工具
│   └── verification/          # 隔离契约检查与单独调用的专项验证
├── tests/                     # 后端隔离测试与共享夹具
├── docs/
│   ├── README.md              # 文档导航
│   ├── reference/             # 当前API、架构和产品说明
│   ├── guides/                # 配置、场景和农业使用说明
│   └── archive/               # 历次设计、评审、案例和验收记录
├── examples/                  # 业务需求文本参考
├── data/                      # 本机数据库、成果和私有配置（Git忽略）
└── output/                    # 日志、诊断、截图和验证证据（Git忽略）
```

代码定位见[后端导航](backend/README.md)，工具用法见[脚本导航](scripts/README.md)，测试职责见[测试导航](tests/README.md)。前端开发说明见[前端导航](frontend/README.md)。请修改`frontend/`源码；`dist/`可通过`npm run build`重建。

## 使用方式与当前能力

主导航围绕“地图、专题分析、任务与成果、数据”组织，共用当前地图。

1. 搜索地点、地址、坐标或已完成成果，明确选择分析范围；地点导航点不等于行政边界。
2. 上传业务图层、选择专题，或在“新建分析 / 查询分析”提出需求，明确年度、时段及必要条件。
3. 查看运行状态、定量结果和数据支持范围；点击要素查看属性，开启“核查像元”读取原生栅格值。
4. 下载GIS、统计及来源文件，在任务历史中恢复成果或保存常用问法。

| 能力 | 当前实现与边界 |
| --- | --- |
| 地图与搜索 | 底图、中文注记、坐标定位、本地数据和成果检索；天地图支持地名、逆地理和行政参考范围。底图不提供指数计算所需的原生光谱波段。 |
| 数据上传 | WGS84 GeoJSON点、线、面及多面上图，保留属性并支持空间统计。最多500要素、5000坐标点、请求体2MiB；不直接导入Excel、Shapefile或GeoTIFF。 |
| 年度分类参考 | WorldCover2021、IO Annual LULC2024耕作地提取及GlobalRice20 2024水稻参考分析，保留类别、年份、面积方法和未知像元。连通图斑不等于确权地块。 |
| 光谱与影像处理 | Sentinel-2、Landsat 8/9原生波段计算，单期指数、两期差值、时序均值/中位数、OLS趋势、Otsu阈值及连通分区。质量掩膜、定标和实际日期随结果保留。 |
| 农业与研究 | 显式端元FVC、用户地块分区统计、NDVI/NDMI时序、样本特征与训练门槛、变化候选及人工核查。尚未发布经过本地独立验证的水稻/油菜监督分类。 |
| 行业台账 | 农业、林业、水利、交通共22项台账场景，可绑定用户数据、筛选分组、统计、上图和下载；场景存在不代表监管系统已接入。 |
| 来源与执行 | 共用来源目录、多源STAC发现、观测/资产版本入库、算法契约匹配、受控问答和版本固定下载。目录命中不等于已读取像元或完成分析。 |

当前有18项分析专题，由 `GET /api/applications` 返回；台账场景定义在 `frontend/src/workspaces/scene-catalog.js`，绑定和历史运行通过 `GET /api/scene-workflows` 读取。光谱指数共12项：NDVI、NDWI、MNDWI、NDMI、NDBI、NBR、EVI2、SAVI、MSAVI2、GNDVI、NDRE、BSI；Landsat 8/9不支持NDRE。

交互光谱选区单边不超过 `0.05°`，最多 `1,048,576` 个参考格网像元；交互时序最多183天、6个独立观测日期，保持同一传感器系列。GlobalRice20行政参考范围最多2000万原生像元，超限需使用子区。具体限制以专题表单和接口契约为准。

## 前端开发

```sh
# 终端一：Python API（8016）
npm run dev:server
# 终端二：Vue开发服务器（5173，代理/api到8016）
npm run dev
```

生产入口仍是8016。页面入口、导航、共享状态、上传、对话框、我的数据、来源目录、任务与成果、矢量分析和系统集成采用Vue组件与TypeScript。已验证的Leaflet、影像检索及复杂遥感表单保留JavaScript模块，由组件适配器管理挂载、事件与轮询；新增通用界面使用Vue组件。

资源列表独立刷新，失败或返回格式异常时保留已有内容、显示上次读取时间并提供单项重试。真实的空列表与读取失败分别处理；请求次序保护避免旧列表覆盖新上传的数据。API边界执行运行时校验，不将TypeScript声明视为服务响应已经正确。

## 数据来源与按需调用

文章整理的农业来源登记在[共用来源清单](data_sources/agriculture_reference_candidates.json)，通过 `GET /api/data-sources` 进入运行时目录，不依赖某个地市或旧工作空间的接入草案。

来源目录分别保存覆盖、声明年份、已接通年份、许可和接入障碍。**查看目录只读取元数据；提交分析后才读取所需范围的数据。** 相同输入的已归档成果经版本校验后可复用，网络读取与归档复用分别记录。

| 来源 | 接入程度 |
| --- | --- |
| Planetary Computer | Sentinel-2 L2A、Landsat C2 L2目录及已适配原生像元分析；Sentinel-1 RTC、DEM GLO30目录。 |
| Earth Search、Copernicus Data Space | 已适配部分公开目录和资产元数据；像元授权、定标和算法适配单独核实。 |
| WorldCover2021、IO Annual LULC2024 | 按明确年度读取分类产品、提取耕作地参考。 |
| GlobalRice20 | 通过ESA ECDC接通2024年原生COG，支持按选区或市县参考范围统计、上图、像元核查及下载；2015年尚未接通。 |
| EARice10、CN_Wheat10、LGISer-CFD | 已登记覆盖、年份和用途，仍受访问、账户、许可或读取适配条件限制，不标为可分析。 |
| 天地图及公开矢量参考 | 地图、地名、行政参考范围；公开地名索引、市界和概化水系保留来源与适用范围。 |
| Open-Meteo | 指定位置的天气模型与预报，不作为现场气象站实测。 |

公开分类产品用于历史参考，不代替当期调查、官方面积或独立地面样本；有效覆盖率不等于分类准确率。算法在本项目已适配的本机服务中执行，尚未直接接入Google Earth Engine或OpenGE云端算力。

## 配置、数据与部署边界

- **工作空间**：默认数据库是 `data/workspace.sqlite3`；`--db` 可选择另一工作空间。成果和服务配置按该数据库所在目录组织，不随代码包移动。
- **地图配置**：通过“数据 → 地图服务设置”管理天地图服务端Key。配置保存在工作空间的 `integrations/tianditu.json`，不把Key写入README、Git或浏览器链接。详见[天地图配置](docs/guides/TIANDITU_SETUP.md)。
- **模型桥接**：当前提供本机Codex CLI测试接入，工作空间的 `integrations/model.json` 控制启用与超时等设置，复用本机登录状态。它生成受控计划，服务仍校验工具、来源、范围和版本，不执行任意代码或Shell。
- **备份与复用**：迁移本机成果时同时保留数据库、成果文件、来源说明和内容指纹；恢复业务资料与恢复私有配置分别按授权处理。只复制代码不会复制本机结果。
- **部署范围**：当前为本机单工作空间服务，尚不具备公网生产所需的组织认证、多租户隔离或分布式作业调度。

## 开发与检查

```sh
npm run check             # 前端语法、后端隔离测试、前端隔离契约
npm run check:syntax      # 语法与编译检查
npm run check:backend     # 后端测试
npm run check:ui          # 构建、类型检查、状态/API测试及专业模块契约
npx playwright install chromium  # 浏览器回归首次安装
npm run test:e2e          # 构建并运行隔离浏览器回归，固定端口8096
```

浏览器回归在系统临时目录创建数据库与明确标注的合成格网，实际运行本机算法、上图、像元点查和文件下载；不使用本机密钥，也不验证外部影像服务。8096被占用时测试直接失败，不复用已有服务。GitHub Actions已配置代码检查与浏览器回归；运行证据在`output/playwright/`。

默认检查不生产正式地市数据，不清理工作空间。需要联网或已有正式成果的专项验证应按[脚本说明](scripts/README.md)单独调用。生产工具可先查看参数：

```sh
.venv/bin/python scripts/production/produce_xiantao_spectral.py --help
.venv/bin/python scripts/production/produce_xiantao_series.py --help
.venv/bin/python scripts/production/publish_agri_series.py --help
```

服务端模块使用明确的 `backend.<领域>.<模块>` 导入；项目根目录 `server.py` 保留启动兼容，不承担业务实现。新增算法同时声明输入、质量、格网、未知值、输出与来源边界，并通过相应隔离测试。

## 文档

- [文档导航](docs/README.md)：区分现行说明、使用指南与历史记录。
- [当前架构](docs/reference/ARCHITECTURE.md)、[当前API](docs/reference/API.md)、[产品能力](docs/reference/PRODUCT.md)。
- [场景模块](docs/guides/SCENE_MODULES.md)、[作物与时序](docs/guides/AGRICULTURE_RESEARCH.md)、[仙桃农业规则与案例](docs/guides/XIANTAO_AGRICULTURE_RULES.md)。
- [整理前README与本机案例快照](docs/archive/README_BEFORE_ORGANIZATION_20261117.md)：历史数字、观测日期和阶段验证记录，不作为新环境的默认能力承诺。
