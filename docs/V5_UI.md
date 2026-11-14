# V5 遥感生产体验与接线

## 交互设计

主导航的“遥感生产”与地图右侧“发起分析”共用一个 `ProductionWorkspace` 实例。用户仅需描述需求、明确数据模式，点击“规划并运行”。POST 自动建立不可变计划并交给后端执行。可展开审阅步骤，不额外增加确认门槛。

默认是明确标识的人工二维红光/近红外样本；公开 Sentinel-2 COG 是另一显式模式，按 capabilities 显示可用性。失败不静默回退样本。公开模式提供已验证的例句“比较仙桃市2025年10月和11月的NDVI变化，定位指数下降区域并上图”。

高级参数折叠，包含区域、bbox、目标、阈值、云量和两期日期窗。留空不发送覆盖值，采用后端提取结果。旧“智能工作流”保留在“高级编排”入口，标识为合成示例流程。

## API

- `GET /api/production/capabilities`：可用模式、目标和服务边界。
- `POST /api/production/tasks`：`{requirement,mode,region?,bbox?,goal?,threshold?,cloud?,periods?}`，自动计划与执行。
- `GET /api/production/tasks` 与 `GET /api/production/tasks/:id`：任务列表、不可变 plan、nodes、真实状态、日志与成果。
- `POST /api/production/tasks/:id/cancel {}`：取消任务。
- `POST /api/production/tasks/:id/retry {}`：创建独立重试任务，原记录保留。

`plan` 摘要显示区域、bbox、请求时相、分析目标与模式。`blocking_gaps/gaps` 明示尚未具备的业务条件。节点优先展示后端 `input_summary/output_summary` 中文摘要与 `started_at/finished_at`，并可展开原始 inputs/outputs/engine 配置。

轮询每 1.1 秒读取任务列表，仅存在 queued/running 时请求；无前端假进度、假完成延时。任务与 localStorage 选中 ID 使刷新和切页能恢复。任务状态错误保留已有任务，不伪造替代成果。

## 地图接线

`ProductionWorkspace.onMap(task)` 将 `task.result.geojson` 与显式元数据交给已有 `MapWorkspace`：

- overlay ID：`production:<task.id>`；每个任务独立，不覆盖其他数据。
- 元数据：来源类型、mode、production_task_id、production_goal、threshold、valid_ratio。
- NDVI 按数值分段着色，无效像元灰色；变化模式按任务阈值区分下降、较稳定、上升。
- Leaflet `L.geoJSON` 与真实影像底图共坐标系统，自动适应成果范围。
- 要素详情展示前后期指数、差值、有效性等原始属性，并提供“查看本次分析”回到任务。
- 成果中心收录 production 引用，可从持久化任务重新读取并叠加。

任务报告使用后端 `report.findings/limitations`，来源详情保留 `provenance`。导出仅 GeoJSON 和来源/报告元数据，不导出影像底图。

## 文件

- `dist/production-workspace.js`：需求、计划、任务状态、历史、报告与导出。
- `dist/app.js`：模块实例、路由、旧工作流高级模式、地图成果桥接。
- `dist/map-workspace.js`：地图助手宿主、生产成果图层及返回任务入口。
- `dist/style.css`：桌面生产双栏、地图紧凑布局及移动端单列。
- `dist/index.html`：V5 缓存版本。

## 专家讨论结论

第一轮与架构师、遥感专家明确“提需求→自动组织→过程可见→成果上图”单链，禁止将目录检索或属性 NDVI 冒充公开像元生产。

第二轮确认单 POST 自动计划与执行、输入模式显式选择、不可变快照、真实失败与独立重试。明确人工 8×8 样本不是实际卫星时相；公开 COG 的小窗口重投影为代表采样网格，SCL 与有效率需如实报告。指数变化不等于作物、播种面积或灾损认定。

## 验证

已对三份 ES module 执行 `node --input-type=module --check`。真实浏览器验收由根代理进行，包含生产页/地图右抽屉往返、真实像元任务、节点展开、上图、报告及移动端。

## 最终一致性处理

- 历史任务与地图“查看本次分析”统一通过 `selectTask(task,{seedDraft:true})`：持久化所选任务 ID，复制原始需求、模式、bbox、时相、目标与实际阈值作为新任务草稿。原计划不修改。
- 继承的参数即使折叠也随下一次提交发送；“新任务”清除继承参数。输入说明明确当前是草稿、右侧是所选任务。
- 高级阈值与云量初始为空，只在用户确实填值时发送，避免仅展开面板就覆盖自然语言中的数字。
- `plan.selection.description` 与 `summary.selection.description` 原样显示为“本次筛选条件 / 实际筛选”；阈值来源（文本提取、高级指定、服务默认）单独标明。
- 地图要素详情展示 `properties.selected`，可追溯是否命中后端实际比较条件；着色仅表达指数/差值，不替代条件判定。
