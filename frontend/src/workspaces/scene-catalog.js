// Business entries bind only to existing executable tools; no simulated business results.
const scene=(id,name,note,data,tools)=>({id,name,note,data,tools});
export const SCENE_MODULES=[
 {id:'agriculture',name:'农业',description:'资源底数、生产服务、工程监管与农业产业链。',items:[
 scene('crop_research','作物与时序','查看影像时序，管理作物样本与变化核查。','同源质量影像、实地作物样本与独立验证',['temporal_analysis','imagery_search']),
 scene('farmland','耕地与资源底数','查看年度耕作地参考分布，关联部门地块台账。','授权地块、行政界、调查年度及统计口径',['cropland_delineation','land_resources','layer_summary']),
 scene('high_standard','高标准农田','工程范围、建设进度、验收资料与遥感线索关联。','项目边界、项目编号、建设与验收台账',['layer_summary','vegetation_change','imagery_search']),
 scene('tailwater','尾水治理','定位治理工程、排口和监测记录。','工程与排口位置、水质监测及运行台账',['layer_summary','water_clues']),
 scene('grain','粮食与重要农产品','查看种植参考、长势和季节变化线索。','作物地块、种植品种、季历与地面样本',['crop_reference_analysis','cropland_delineation','vegetation_status','temporal_analysis']),
 scene('disaster','防灾减灾','结合植被变化、水分及天气参考组织核查。','权威灾情、降雨水位与现场调查',['vegetation_change','vegetation_moisture','weather']),
 scene('fishing','长江禁捕','监管水域、巡查对象和处置记录一张图。','禁捕区边界、船舶、视频及巡查授权接口',['layer_summary']),
 scene('machinery','北斗农机监管','关联设备、作业轨迹和服务地块。','北斗轨迹、设备编号、时间戳、作业类型与核算规则',['layer_summary']),
 scene('breeding','养殖场所','定位鸡场、猪场及水产养殖主体，关联许可。','养殖场边界、主体许可、环保与巡检台账',['layer_summary','imagery_search']),
 scene('supplies','农资经销门店','查看种子、化肥等经营门店分布。','门店坐标、经营许可、品类及检查记录',['layer_summary']),
 scene('coldchain','冷链物流与加工','关联仓储、加工节点及容量台账。','节点位置、容量、统计时点及授权物流接口',['layer_summary']),
 scene('tenure','土地确权与二轮延包','展示权属地块与延包工作进度。','授权确权边界、脱敏权属和延包台账',['layer_summary']),
 scene('eel','黄鳝产业链','关联养殖、加工、交易和质量追溯。','主体编号、供应链交易、检测与追溯授权接口',['layer_summary']),
 scene('sowing','秋冬播生产','比较播种季节影像，汇总地块光谱参考。','播种地块、作物季历及地面核验样本',['vegetation_change','temporal_analysis','field_zonal_statistics'])]},
 {id:'forestry',name:'林业',description:'森林资源、林地变化与灾害巡查。',items:[
 scene('forest','森林资源','林地台账与植被覆盖参考。','林地边界、树种、调查年度与样地',['layer_summary','vegetation_status','vegetation_cover']),
 scene('forest_change','林地变化核查','排查植被变化并组织现场核验。','林地边界、许可审批及核查记录',['vegetation_change','temporal_analysis']),
 scene('forest_risk','林草灾害巡查','结合天气与影像安排巡查。','权威火情、病虫害样本及巡护台账',['weather','imagery_search','vegetation_moisture'])]},
 {id:'water',name:'水利',description:'河湖水域、防汛核查与工程管理。',items:[
 scene('rivers','河湖水域','查看水体光谱线索与河湖台账。','河湖授权边界、管理范围及水位监测',['water_clues','layer_summary']),
 scene('flood','防汛与积水核查','比较影像及天气，辅助排查积水线索。','权威雨水情、灾情和现场核验',['water_clues','weather','imagery_search']),
 scene('water_projects','水利工程','展示水库、堤防、泵站与巡检台账。','工程位置、运行状态及安全监测接口',['layer_summary'])]},
 {id:'transport',name:'交通',description:'路网设施、工程巡查与物流运输。',items:[
 scene('roads','公路与交通设施','道路、桥梁和设施台账上图。','授权路网、设施编号及养护台账',['layer_summary','imagery_search']),
 scene('road_work','工程与沿线巡查','影像对照与施工记录关联。','项目边界、施工进度与核查记录',['imagery_search','layer_summary']),
 scene('logistics','运输与物流节点','关联运输节点和授权轨迹。','节点位置、容量、车辆轨迹及授权接口',['layer_summary'])]}
];
