# V9 一图问答与真实执行

## 两轮专家决议

第一轮以“问题→实际取数与计算→质量与答案→配准地图”为客户主线。已支持的问题满足条件后自动执行，目录发现与选源方案不得作为像元分析完成。客户仍只看一张图，不被要求选择波段、算法或数据源。

第二轮冻结地图两点框选及业务澄清：缺范围时保留原问题与时相，圈选确认立即新提交继续执行；运行任务使用不可变范围，重选只影响新问题。超过执行尺度的范围明确重选，不自动缩小、不切人工样本。提交使用request_id和冻结body幂等重试，历史按question.id与execution_ref只读恢复。

## 客户操作

- 全市分类面积、土地构成等问题直接输入，后台自动运行或复用同版真实成果。答案保留2021产品与2017参考市界的短行。
- 有界公开影像分析可以先点击地图“选择范围”，在地图点选两个对角、命名并确认；手机同样两次点选，无坐标表单。
- 若先提问而缺范围，答案卡给“在地图选范围，自动继续”。确认后保留原问题、原时相，立即提交新澄清记录。
- composer范围chip显示名称与近似尺度，可重选/清除。历史任务的冻结范围单独显示；清除或重选不会更改运行快照。
- 真实阶段显示调度步骤和下游处理步骤。每步可展开实际输入、产出、时间；工具版本/调用参数/任务编号折叠。
- 成功答案显示质量、原生像元数、真实观测日期（后端有字段时），配准影像自动加入地图；GeoTIFF/报告等通过真实下载地址提供。

## 接线

### 问答与幂等

`POST /api/assistant/questions` 仍是客户唯一业务提交入口。请求含`question`、可选`capability_id`、`request_id`与`context`。

- 地图选区：`context.bbox=[west,south,east,north]`。
- 澄清：完整原问题重发（文字补充则拼接补充内容），保留`period/periods/dataset_id`，加`context.clarification_of=原question.id`。不把无成果needs_data记录当`reference_question_id`。
- 普通完整新问题独立调度；只有纯来源/质量/下载类指代追问或显式快捷问法才附成功记录reference；带明确新日期、新时间或新计算动作的问题不自动引用旧成果。
- 网络未收到回执时，localStorage保留冻结body及request_id；“查询并重试同一次提交”使用同body同ID，直到收到记录或用户明确发起新问题。

### 真实执行观察

`question.execution_ref`或`answer.execution_ref`指向`GET /api/executions/:id`。前端不直接调执行器创建孤立任务，保持问答记录、结构化答案与成果绑定。

`job.steps`展示真实调度；`job.child_steps`展示实际下游处理；`input_summary/output_summary/started_at/finished_at`优先中文呈现。进度仅来自API，不插入前端假进度。processing时轮询问答与当前执行记录，切记录或刷新恢复对应执行ID。

`GET /api/executions/capabilities`提供真实工具状态与`limits.max_bbox_span_deg`。框选尺度读取该字段，未知时交后端最终校验，前端不硬编码旧16×16或默认裁切值。

### 地图

`MapWorkspace.beginAreaSelection()`返回确认范围或取消；选区矩形、起点与名称面板为临时UI，不替代业务成果图层。确认callback更新MapCopilot范围chip，取消保留旧范围。

生产成果读取`answer.result_ref.type=production`对应`GET /api/production/tasks/:id`。`result.image.url/bounds`通过Leaflet imageOverlay与真实WGS84范围配准，`bounds`转换为`[[south,west],[north,east]]`；PNG已由后端生成EPSG3857。配套`result.geojson`为AOI轮廓，不冒充像元矢量网格。`result.image.legend`原样提供颜色与标签，透明区明确为无效/云。旧任务无image时保持既有GeoJSON兼容。

全市WorldCover结果仍按question.id与image_kind保留classes/cropland模式。来源/下载类map_effect=none不触发缩放，不覆盖已有结果。

## 实现文件

- `dist/map-copilot.js`：客户输入、范围chip、业务澄清、幂等恢复、执行步骤与质量、历史绑定。
- `dist/map-workspace.js`：两点框选、命名确认/取消、原生生产栅格overlay与后端图例。
- `dist/app.js`：范围/能力callback和上下文接线，既有专业工具保留。
- `dist/style.css`：范围选择面板、移动交互、执行步骤/质量卡样式。
- `dist/index.html`及模块版本更新到`20261113-v9`。

## 验证记录

ES module语法检查通过。Node模板/请求验证通过：范围与request_id、澄清保留periods、不引用无成果needs_data、真实child_steps输入产出、HTML及下载URL转义；两点框选/命名/确认/超限与取消；超时同payload复用请求ID、更改范围新建ID；带日期新分析不误绑旧来源。

独立浏览器 `geoai-v9-front`、8017隔离DB完成真实回归，未操作根代理会话。桌面1440×1000、手机390×844、来回调整尺寸无横向溢出。该独立会话已关闭。

实际链路：

- `question_153b7cb9d71e42`：无范围的两期下降问题回到needs_data，只要求地图范围。
- 真UI两点框选、命名并确认后自动继续：`question_5614ce8a1c3442` → `execution_4a52f2b7648e40` → `production_46c83229f1624c`。原文、2025年10/11月和parent链保留；实际选区约0.68×0.66千米。
- 新任务真实完成4479原生参考格网像元、4479共同有效、594命中下降大于0.1、5.94公顷投影网格面积。实际观测2025-10-28 → 2025-11-27。数值来自API，未硬编码。地图自动显示配准PNG，六个实际处理步骤及行列、波段、来源和时间可展开。
- 来源追问 `question_85dd1a7df1f34a`、下载追问 `question_52188e9761f149` 精确绑定同一production任务，map_effect=none，视角不变。
- 完整新问题 `question_c9103573ae7448` 不带reference，独立得到全市2021土地构成与classes影像。
- 历史选回原native问题并刷新，question/execution与图层绑定保持不变。专业原生结果上图及刷新恢复也通过。
- 保存原生问法得到 `shortcut_979130bf744a40`，context保留真实bbox。点击生成新 `question_14caf27b04e444` / `execution_4dc744b34d504c`，复用同真实native任务、重新生成有来源答案，无旧reference混入。
- 下载按钮展开真实analysis.tif、overlay.png、result.json地址。未把AOI边界GeoJSON当作像元分类输出。
- 独立会话临时中止Esri请求验证tileerror：主地图显示底图未加载、重试与备用入口，分析overlay保留。撤销测试route并重试后至少8张真实瓦片成功，错误banner隐藏。测试route已撤销；模拟故障的网络错误不计为应用脚本错误。首次真实天气服务曾返回502，未填示例成功值；最终刷新console为0错误/0警告。

成功答案自动滚动到标题，手机首屏可读实际日期与定量主卡。阈值/范围问题优先显示selected_area_ha，附“阈值命中原生参考格网投影面积，非作物/地块面积”。真实scene日期读取source_details.scenes。底图失败提示独立于图层抽屉，超过12秒无成功瓦片只提示响应较慢，不把慢请求假称失败。NASA仅显式切换，不冒充当前高分影像。

用户GeoJSON算法成果通过result_ref.type=analysis读取/api/analysis/:id的geojson，独立加入地图。天气与目录本轮map_effect=none。常用问法回放传shortcut.context，仅来源/下载问法才引用source_question_id，普通分析不引用旧结果。

证据位于 `output/playwright/`：

- `v9-front-chain-evidence.json`、`v9-front-question-native.json`、`v9-front-shortcuts.json`、`v9-front-shortcut-replay.json`。
- `v9-front-select-desktop.png`、`v9-front-select-mobile.png`、`v9-front-running-mobile.png`。
- `v9-front-basemap-recovered.png`（真实底图与分析overlay）、`v9-front-answer-mobile.png`。
- `v9-front-basemap-failure.png`（测试route中止后的明确失败态）。

本链路鼠标实际选区与根代理精确验证窗[113.30,30.30,113.31,30.31]不同，所以本链路5.94公顷与根精确窗14.72公顷不可混作同一区域结果。
