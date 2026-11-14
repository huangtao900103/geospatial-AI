"""Explicit business data requirements, separate from executable analysis templates."""
import copy

SCENARIOS=[
 {'id':'land_resources','name':'耕地与资源底数','questions':['选区耕作地分布在哪里、参考面积多少？','部门地块台账共有多少个要素？'],'available_applications':['cropland_delineation','land_resources','layer_summary'],'required_data':['授权行政区/业务范围边界及版本','法定调查地类与地块唯一编号','业务年度和面积统计口径'],'after_connection':'把部门地块台账与遥感参考结果关联，按授权范围组织资源核查。','boundary':'公开年度分类不能代替法定耕地调查；固定仙桃历史基准不替代所选市县。'},
 {'id':'agricultural_projects','name':'高标准农田与尾水工程','questions':['工程建在哪里、涉及哪些地块？','哪些项目需要现场核查？'],'available_applications':['layer_summary'],'required_data':['工程边界GeoJSON及项目编号','立项/建设/验收时间和状态台账','尾水设施、排口与现场监测记录'],'after_connection':'关联工程范围、建设台账与核查记录，在地图定位工程并组织业务核查。','boundary':'光谱变化不能直接认定工程验收、设施运行或污染达标。'},
 {'id':'production_disaster','name':'农情与防灾减灾','questions':['选区植被指数有哪些变化？','哪些范围需要安排核查？'],'available_applications':['vegetation_status','vegetation_change','water_clues','vegetation_moisture','weather'],'required_data':['作物季历、种植地块与地面样本','权威灾情、降雨/水位及现场调查','业务阈值、核查规则和责任区域'],'after_connection':'按作物季节与巡查规则组织光谱线索，关联灾情与现场核查。','boundary':'指数、天气模型与阈值范围不是受灾面积、损失金额或作物产量认定。'},
 {'id':'fishery_supervision','name':'渔政禁捕与养殖监管','questions':['监管水域、养殖主体和巡查点在哪里？','线索对应哪个主体、由谁核查？'],'available_applications':['layer_summary'],'required_data':['禁捕/监管水域授权边界及有效期','养殖主体、许可与设施台账','船舶/视频/巡查记录及合法可用接口'],'after_connection':'形成监管对象一张图，关联巡查线索与处置记录。','boundary':'没有授权业务数据和识别算法，不能从公开水体分类认定养殖塘或违法捕捞。'},
 {'id':'industrial_chain','name':'黄鳝产业链与冷链物流','questions':['养殖、加工、仓储、交易主体如何分布？','冷链节点数量、容量与业务流向是什么？'],'available_applications':['layer_summary'],'required_data':['主体统一编号、位置和业务类型','加工/冷链节点、容量及统计时点','授权供应链/交易/追溯接口和字段字典'],'after_connection':'关联产业主体、物流容量与授权业务记录，形成产业专题。','boundary':'遥感不能推导企业产能、交易额、单尾产地或食品安全结论。'},
 {'id':'agricultural_services','name':'社会化服务与农机作业','questions':['农机作业轨迹在哪里？','服务范围与作业台账如何关联？'],'available_applications':['layer_summary'],'required_data':['授权北斗轨迹、设备编号和时间戳','作业类型、农具参数与业务地块','轨迹质量、重复作业及面积核算规则'],'after_connection':'轨迹与作业台账上图，按核定规则组织服务统计。','boundary':'线路几何统计不等于合规作业面积；本机尚未接入实时北斗设备。'}
]

def catalog():
    return {'schema_version':'1.0','deployment_scope':'local_workspace',
            'note':'以下为业务接入需求清单；可用基础工具不表示部门业务系统已接通。',
            'items':[{**copy.deepcopy(item),'business_integration_status':'awaiting_authorized_data',
                      'status_label':'待业务数据接入'} for item in SCENARIOS]}
