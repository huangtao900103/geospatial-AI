# 农业业务工作台

入口：左侧 **农业业务**，或 `/#/map/operations`。地图、选区、上传图层和原有遥感专题保持共用；新流程通过实际业务接口持久化，不预置地块、监测值或灾情样例。

## 本阶段可执行流程

1. 在“台账”建立种植、畜牧、水产、高标准农田或示范创建台账，填写来源和年度。
2. 选择已有用户图层，或上传较大的WGS84 GeoJSON，指定稳定的业务编码字段，可选名称、状态字段。地块、工程和示范区须提供Polygon/MultiPolygon面边界。
3. 分页查询、按名称/编码及状态筛选，定位对象或将当前页上图。项目和示范创建可更新状态、进度与说明；每次更新保留审计记录和版本。
4. 在“防灾核查”登记人工研判或业务材料转录的事件，填写来源、时间并在地图明确选择范围；可通过接口提交真实Polygon/MultiPolygon范围。
5. 选择参与核查的台账，执行空间相交分析。服务先用RTree检索候选，再以Shapely检查实际几何；孔洞内对象、外包框误命中及仅边界接触分别处理。
6. 对命中对象分批派单，填写责任单位/人员与完成日期。任务按“待接单→处理中→待审核→已归档”办理；审核也可退回补充。提交回执须填写核查结果、说明，可附最多8个HTTPS证据链接。
7. 导出事件JSON档案，包含事件来源、台账和对象版本快照、分析方法、任务回执、审核及审计记录。

“生产指导”支持管理人员编制草稿、审核发布和执行回执。审核发布时冻结内容与目标对象版本，并生成执行任务，共用上述任务办理流程。当前没有自动生成施肥剂量或农药处方。

## 数据规模与一致性

- 业务对象独立保存于`agri_registries`、`agri_objects`及RTree空间索引，随工作空间SQLite数据库持久化。原有遥感记录和成果文件维持原路径。
- 一份台账可由多个批次组成，累计对象数不受原上传图层500要素限制；每批最多500对象，HTTP请求体仍限2MiB。浏览器交互导入上限32MiB，自动按最多300对象及约1.2MiB分批；更大文件需分批接口或后续专业导入作业。
- 对象查询每页最多200条，前端每页50条。`bbox`过滤返回空间索引候选，不把它表述为真实面相交结果。
- 稳定业务编码在同一台账内唯一。重复批次按内容指纹复用；冲突或同批重复编码整批回滚，保留之前已经成功的批次。暂不支持自动合并、覆盖或删除已有地块。
- 导入期间冻结台账、字段映射和操作人，防止界面切换改变正在写入的目标。
- 对象更新及任务流转要求`expected_revision`。影响分析保存事件、台账和对象快照；台账变化后，旧结果不能直接继续派单，须重新分析。
- 单次范围核查最多5000个空间候选，超限明确报错并要求分区；不会截取前5000个冒充完整结果。每次派单最多200对象，可逐批办理。指导发布目标须为1至200对象的明确台账。

## 当前边界

九项需求在概览中分别标注可建档、可执行本机流程、待接入或预留。农机和冷库目前可登记资源对象，实时北斗、道路时间服务圈、冷库库存/预约/出入库订单尚未实现。追溯为预留；无人机航线、设备和直播尚未接通。

灾种字段覆盖洪涝、干旱、冰冻雨雪、大风、台风及其他核查。本阶段执行的是指定范围与业务对象的空间核查，不包含上述灾种的数值预测、遥感灾损模型、自动预警订阅或行政认定。空间相交对象数不等于受灾对象数；未输出伪造的受灾面积或损失金额。

气象站、权威预警、墒情、塘口、视频、北斗及仓储接口均在概览中明确标为待接入。转录材料由操作者填写来源；平台不据此声称已经核验为官方预警。证据链接不由服务端抓取，也未自动核实内容。

目前仍为本机单工作空间。操作人、责任人和审核人用于留痕，不构成组织身份认证或权限隔离；多部门生产部署仍需接入统一身份、角色授权及部署级审计。

## 接口

接口前缀为`/api/agri-operations`，修改请求沿用本机服务的同源校验。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /catalog` | 九模块、对象类别、灾种、接入状态与限额 |
| `GET/POST /registries` | 台账列表/创建；创建字段`name,kind,source,year,actor` |
| `POST /registries/:id/import` | 用户图层或分批GeoJSON入库；须指定`code_field,batch_id,expected_revision,actor` |
| `GET /objects` | `registry_id,q,status,bbox,limit,offset`分页查询；bbox为`west,south,east,north` |
| `PATCH /objects/:id` | `status,note,progress,expected_revision,actor`更新档案 |
| `GET/POST /events` | 事件列表/登记；人工或转录来源、来源时间及面范围必填 |
| `POST /events/:id/analyze` | `registry_ids,actor`执行空间核查并冻结快照 |
| `GET /impacts/:id/geojson` | 全部实际相交对象的版本固定GeoJSON，包含来源元数据 |
| `GET /impacts/:id` | 恢复空间核查结果及原始对象版本 |
| `POST /impacts/:id/dispatch` | `object_ids,assignee,due_date,actor`按对象派单 |
| `GET /tasks` | 核查与指导执行任务 |
| `POST /tasks/:id/transition` | `status,note,expected_revision,actor`；提交时增加`finding,evidence_urls` |
| `GET/POST /guidance` | 指导列表/草稿；`title,content,registry_id,actor` |
| `POST /guidance/:id/publish` | 审核发布；`expected_revision,actor,assignee,due_date` |
| `GET /audit/:entity_id` | 单对象、事件、任务或指导的审计快照 |
| `GET /events/:id/export` | 事件、空间结果、任务及审计JSON档案 |

## 验证

`tests/test_agriculture_operations.py`验证真实空间运算、跨批次入库、分页、重复防护、任务状态、冻结版本及HTTP同源边界。`frontend/e2e/agriculture-operations.spec.ts`在临时工作空间验证批量入库、对象上图、事件核查到归档、指导发布、版本冲突与移动端。测试对象和事件均为明确的隔离输入，不写入正式业务库。

几何谓词参考[Shapely官方说明](https://shapely.readthedocs.io/en/stable/predicates.html)，索引实现参考[SQLite RTree官方说明](https://www.sqlite.org/rtree.html)。当前实现为SQLite空间索引，未将其宣称为已部署PostGIS或完成全市容量验收。
