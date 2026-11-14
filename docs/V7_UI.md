# V7 客户一张图问答体验

## 客户入口

默认界面只有一张图。主导航“一张图 / 常用能力 / 分析记录”仅切换同一地图助手的页签，不跳转独立工具页。

客户输入一个问题即可：问题 → 真实后台状态 → 结构化答案 → 自动对应地图成果。无需选择数据、算法或技术参数。

旧全市核算、小窗分析、实验室、应用工坊、数据和集成等能力保留在“专业工具”显式入口。通过sessionStorage记录专业模式；默认直接打开旧 `#area/#workflow/#lab` 书签会回到客户一张图，只有显式进入专业工具后才开放旧页。

客户模式忽略历史样本图层开关偏好，不默认显示合成农业点或NDVI示例；无问答成果时图例提示等待结果。技术GeoJSON导出/数据导入不作为客户出口，统一使用答案卡提供的真实下载。

## 统一问答 API

`MapCopilot` 调用：

- `GET /api/assistant/capabilities`，读取固定可用能力与自然语言例句。
- `POST /api/assistant/questions {question,capability_id?,context?}`。
- `GET /api/assistant/questions` 与 `/:id`。
- `POST /:id/cancel {}` 与 `/:id/retry {}`。
- `GET /api/assistant/shortcuts`；保存为常用问法时POST `{name,question,source_question_id}`。

问句最长2000字。常用能力走同一问答API，不在前端固定数值。保存的快捷问法仅保留原句与来源qid，再次点击重新取得有来源的答案。

状态使用真实 `processing/answered/needs_data/unsupported/failed/cancelled`。每1.2秒仅在有processing记录时读取列表，无假进度、无静态回答。缺条件使用自然语言提示；不支持年份提供明确“查看2021历史基准”的新提问，绝不自动替换年份。

## 答案与历史

主卡直接渲染后端 `answer.metrics/summary/chart/downloads`。LC答案显著保留“2021公开分类 / 2017参考市界 / 非现状调查”；覆盖率不当作识别精度。

技术执行记录、版本和方法仅折叠展示。来源质量与下载回答 `map_effect=none`，保留地图与镜头不动。

历史唯一键为 `question.id`，而不是底层计算task。多种问题可复用同一真实计算结果，但各自答案快照、image_kind与历史绑定独立。

只有有效已回答且绑定真实task的问题默认作为后续引用。无成果的unsupported/failed不引用；needs_data后用户补充的业务文本与原问拼接后提交。明确历史基准独立发问；快捷问法引用自身source_question_id，不误用当前选中记录。

## 地图绑定

`app.js` 从答案 `result_ref.type/task_id` 读取实际landcover/production任务，生成 `assistantSelection:{question,task,fit,token}`。

`MapWorkspace.applyAssistantResult()`：

- 图层键 `assistant:<question.id>`，来源task固定；新问答不改写旧历史层。
- 新选择默认隐藏旧问答图层，但保留图层记录以便恢复。
- LC `image_kind=cropland` 使用overlay.png；`classes` 使用classes.png，已存在图层也更新对应URL。
- classes图例显示全部非零类别（包括永久水体和湿地），可折行。
- 地图点选问答成果回到同一地图助手与该question，不进入旧area/workflow页。
- `map_effect=none` 不新增图层或重复缩放；来源/下载回答仍保留真实引用。
- Leaflet实例、视角与数据图层保留，不因答案或页签切换重建地图。

## 移动端

地图助手采用独立滚动答案区与固定问题输入。可随时收起查看地图，底部“提问 / 查看答案”恢复入口；常用能力和历史仍在同一抽屉切换。没有技术表单占满客户屏幕。

## 文件与验证

新增 `dist/map-copilot.js`。客户路由/专业模式接线在app.js；地图问答宿主、结果绑定与图例在map-workspace.js；所有样式在style.css，缓存tag为V7。

已执行三份ES module语法检查。使用8017真实问答结果做模板断言：实际构成面积、2021/2017、永久水体、湿地、能力列表及qid历史均渲染通过。根代理执行最终真实桌面/手机浏览器验收。
