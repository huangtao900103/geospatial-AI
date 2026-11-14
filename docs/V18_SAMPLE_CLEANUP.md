# V18 不真实样例清理

本轮按用户明确要求清理当前工作空间里的不真实样例。扫描仅使用SQLite `mode=ro`，不初始化Workspace，不改变运行任务。迁移只处理有直接证据的样例和依赖它们的记录；不会因为标题出现“验收”“测试”就删除真实数据。

## 已确认的清理范围

正式库只读扫描（以最终停服务后重新扫描为准）得到26条候选：7个dataset、5个analysis、4个旧app、2个fixture计划、3个fixture运行、1个基于虚构测试选区的分区统计、2个执行和2个问答。报告为`output/v18-cleanup/dry-run.json`。

- 三份“农业算法教学样本（人工构造）”虽然source_type=user，其内部source明确为人工构造教学数据，不能作为真实业务输入；五个analysis由其派生。
- 三份上传或分区验收几何在集合名、要素名或purpose中明确写“非实测/非实际业务”，移除这些样例和分区统计的依赖问答。
- “地块数据”这份24面图层通过**已审精确内容SHA256**识别，不通过宽泛名字删除。其所有要素名为“示例地块NN”、region为示例片区、重复规则网格，veg_before/veg_after为演示值；其内容SHA256为`404960e19813b85ecf078917500ce4ca4bc4c154a57fdeb212c9ce26dbc4f9a7`。
- 旧四个应用使用只能选择fixture/scenario图层的配置接口，appPreview依赖合成map_data，不具备真实dataset绑定，因此按已审ID与旧schema清理；后续真实数据绑定的应用不会匹配此规则。

## 明确保留

Natural Earth真实概化水系（尽管名字含“流程验收”）、天地图行政区参考界、用户导入的geoBoundaries参考界均保留。真实公开遥感原生任务、年度分类、真实NDVI的FVC派生、观测资产库和其他无法证明为虚构的用户数据均保留。旧16×16代表抽样任务的像素确实来自公开影像，不冒充伪造数据进行删除；其方法限制继续保留。

## 运行时不再生成样例

`map_services.py`不再列出农业、产业设施、农机、尾水治理、核查告警五类合成层。请求旧layer返回410 `SYNTHETIC_LAYER_REMOVED`，并指向真实用户数据/遥感计算。公开底图、水系及卫星目录来源保留；固定天气坐标明确为模型请求参考点而非气象站，改为默认关闭。

server已移除虚拟`xt-fixture`数据集、bootstrap/map_data默认样例和fixture计划/运行/旧应用能力，旧入口明确410。人工波段生产模式与前端教学按钮由主集成同步清理，避免迁移后重新出现样例。单元测试中的明确隔离fixture不作为生产数据对外提供。

## 应用迁移

默认只审阅，不修改：

```sh
.venv/bin/python scripts/cleanup_samples.py --output output/v18-cleanup/reviewed-plan.json
```

正式服务停止、没有活动任务后重新扫描并审阅，再应用准确计划：

```sh
.venv/bin/python scripts/cleanup_samples.py --apply-plan output/v18-cleanup/reviewed-plan.json --backup-dir output/diagnostics/v18-sample-recovery --output output/v18-cleanup/apply-result.json
```

备份目录必须是新目录且位于live data目录之外。脚本先备份完整SQLite和将删除的精确产物，再以`BEGIN IMMEDIATE`锁定并核对整个records快照，逐ID删除，核对所有保留行原始字节SHA256。文件只删除审阅清单里的路径和哈希一致的产物。数据库/产物发生变化、存在活动任务、链接或不安全路径时拒绝继续；不会尝试覆盖当前修改。备份目录700权限，私有天地图Key文件从不参与迁移或交付。

迁移后再次扫描应为0候选；如果运行期间又产生有证据的样例，需要单独新计划，旧计划不会自动扩大删除范围。

## 验证

`tests/test_sample_cleanup.py`覆盖递归依赖、真实/未知数据保留、不能按标题误删、虚拟fixture与人工band、已审内容哈希、旧app判定、活动任务/数据库漂移/篡改计划、产物备份、哈希变化和文件链接、备份目录隔离。与地图来源测试合计25项通过，日志`output/v18-cleanup/tests.txt`。随后补齐server旧接口停用的HTTP回归，原有模拟成功用例改为空态、410、无模拟记录生成及真实输入保留断言；连同GeoJSON/地图/迁移测试共57项通过，日志`output/v18-cleanup/server-removal-tests.txt`。正式apply由主代理统一备份停服务后执行；本子任务没有删除正式记录或更改Key。

## 生产工具收尾

生产能力目录只声明公开影像原生模式，不再提供人工sample_pixels、默认示例bbox或16×16新计算入口。专业前端清除旧保存模式并总是提交sentinel_cog/native，移除样例按钮和旧高级编排；真实公开代表网格只作历史查看，新任务使用原生模式。亦删除遇全市面积问题时隐式切到仙桃2021基准的旧快捷逻辑，由真实算法接口明确其业务与时相边界。24项生产/重试回归及独立Node控制器检查通过；Node检查不访问HTTP或数据库，证据`output/v18-cleanup/production-native-ui-proof.json`。
