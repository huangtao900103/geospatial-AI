"""Versioned calibrated-reflectance indices; fixed formulas, not classifications."""
import copy
import re
from backend.core.geo_services import GeoError

VERSION='2.0'
BANDS={
 'blue':{'id':'B02','name':'blue','native_resolution_m':10,'xml_band_id':'1'},
 'green':{'id':'B03','name':'green','native_resolution_m':10,'xml_band_id':'2'},
 'red':{'id':'B04','name':'red','native_resolution_m':10,'xml_band_id':'3'},
 'nir':{'id':'B08','name':'nir','native_resolution_m':10,'xml_band_id':'7'},
 'rededge':{'id':'B05','name':'rededge','native_resolution_m':20,'xml_band_id':'4'},
 'nir_narrow':{'id':'B8A','name':'nir_narrow','native_resolution_m':20,'xml_band_id':'8'},
 'swir1':{'id':'B11','name':'swir1','native_resolution_m':20,'xml_band_id':'11'},
 'swir2':{'id':'B12','name':'swir2','native_resolution_m':20,'xml_band_id':'12'},
}
# Explicit reference bands preserve the coarsest original input spacing; no SWIR
# index is promoted to a purported independent ten-metre observation.
DEFINITIONS={
 'ndvi':('NDVI','归一化植被指数','nir','red','red','https://www.usgs.gov/landsat-missions/landsat-normalized-difference-vegetation-index','植被光谱状态，不是作物分类或耕地调查。'),
 'ndwi':('NDWI','归一化水体指数','green','nir','green','https://sentinel-hub.github.io/custom-scripts/sentinel-2/ndwi/','McFeeters绿光/NIR定义；建成区、阴影与混合像元可能混淆，不是水体分类面积。'),
 'mndwi':('MNDWI','改进型归一化水体指数','green','swir1','swir1','https://doi.org/10.1080/01431160600589179','绿光/SWIR1水体增强线索；阈值需本地验证，不等于淹水或灾损认定。'),
 'ndmi':('NDMI','归一化植被水分指数','nir','swir1','swir1','https://www.usgs.gov/landsat-missions/normalized-difference-moisture-index','NIR/SWIR1植被水分光谱线索；不是土壤含水率实测或干旱等级。'),
 'ndbi':('NDBI','归一化建成区指数','swir1','nir','swir1','https://enterprise.arcgis.com/en/image/11.4/raster-analytics/band-arithmetic-function.htm','裸土、干旱地表与建成区可能混淆；不是建筑物提取或用地执法结论。'),
 'nbr':('NBR','归一化燃烧比指数','nir','swir2','swir2','https://www.usgs.gov/landsat-missions/landsat-normalized-burn-ratio','NIR/SWIR2光谱扰动线索；本平台差值始终后减前，不使用常见dNBR前减后的符号，未标定火烧/灾损等级。'),
 'evi2':('EVI2','二波段增强植被指数','nir','red','red','https://doi.org/10.1016/j.rse.2008.06.006','采用Jiang等2008年式15的2.5/2.4系数；需已定标反射率，不是叶面积指数、作物产量或生物量。'),
 'savi':('SAVI','土壤调节植被指数','nir','red','red','https://www.usgs.gov/landsat-missions/landsat-soil-adjusted-vegetation-index','固定土壤调节因子L=0.5；用于减弱稀疏植被土壤背景，不反演土壤肥力、产量或含水率。'),
 'msavi2':('MSAVI2','改进型土壤调节植被指数','nir','red','red','https://www.usgs.gov/landsat-missions/landsat-modified-soil-adjusted-vegetation-index','采用USGS所列MSAVI根号公式（常称MSAVI2）；负根号项剔除，不夹成0，不代表土壤实测属性。'),
 'gndvi':('GNDVI','绿光归一化植被指数','nir','green','green','https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/gndvi/','绿光与NIR植被光谱线索；未用当地样本标定叶绿素、氮含量或施肥量。'),
 'ndre':('NDRE','归一化红边植被指数','nir_narrow','rededge','nir_narrow','https://pro.arcgis.com/en/pro-app/latest/help/data/imagery/indices-gallery.htm','本实现明确为Sentinel-2 B8A/B05，两波段原生20m；不把Landsat无红边波段代替，也不直接给施肥处方。'),
 'bsi':('BSI','裸土光谱指数','swir1','nir','swir1','https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/barren_soil/','本实现采用SWIR1+red与NIR+blue四波段定义；裸土、建设地表、干枯植被可能混淆，不是撂荒认定。'),
}
FORMULAS={
 'evi2':{'kind':'evi2','bands':['nir','red'],'formula':'2.5*(nir-red)/(nir+2.4*red+1)'},
 'savi':{'kind':'savi','bands':['nir','red'],'formula':'1.5*(nir-red)/(nir+red+0.5)'},
 'msavi2':{'kind':'msavi2','bands':['nir','red'],'formula':'(2*nir+1-sqrt((2*nir+1)^2-8*(nir-red)))/2'},
 'gndvi':{'kind':'normalized_difference','bands':['nir','green'],'formula':'(nir-green)/(nir+green)'},
 'ndre':{'kind':'normalized_difference','bands':['nir_narrow','rededge'],'formula':'(nir_narrow-rededge)/(nir_narrow+rededge)'},
 'bsi':{'kind':'bsi','bands':['swir1','red','nir','blue'],'formula':'((swir1+red)-(nir+blue))/((swir1+red)+(nir+blue))'},
}
ALIASES={'ndwi':['归一化水体指数'],'mndwi':['改进型水体指数','改进型归一化水体指数'],'ndmi':['植被水分指数','植被含水指数'],'ndbi':['归一化建成区指数','归一化建筑指数'],'nbr':['归一化燃烧比','燃烧比指数'],'evi2':['二波段增强植被指数','两波段增强植被指数'],'savi':['土壤调节植被指数'],'msavi2':['改进型土壤调节植被指数'],'gndvi':['绿光归一化植被指数'],'ndre':['归一化红边植被指数','红边植被指数'],'bsi':['裸土光谱指数','裸土指数']}

def detect_indices(text):
    lower=text.lower();found=[]
    for id in DEFINITIONS:
        if re.search(r'(?<![a-z0-9])'+id+r'(?![a-z0-9])',lower) or any(alias in text for alias in ALIASES.get(id,[])):found.append(id)
    # The MNDWI Chinese name contains the NDWI alias.
    if 'mndwi' in found and 'ndwi' in found and not re.search(r'(?<![a-z])ndwi(?![a-z])',lower):found.remove('ndwi')
    if 'msavi2' in found and 'savi' in found and not re.search(r'(?<![a-z])savi(?![a-z])',lower):found.remove('savi')
    return found

def specification(index_id='ndvi'):
    if index_id not in DEFINITIONS:raise GeoError('INDEX_UNSUPPORTED','尚未接入此光谱指数，不能用NDVI代替',422)
    label,name,a,b,reference,url,limit=DEFINITIONS[index_id]
    definition=FORMULAS.get(index_id,{'kind':'normalized_difference','bands':[a,b],'formula':'('+a+'-'+b+')/('+a+'+'+b+')'})
    nonlinear=index_id in ('evi2','savi','msavi2');legacy=index_id not in FORMULAS
    result={'id':index_id,'name':name,'label':label,'formula':definition['formula'],'formula_kind':definition['kind'],'unit':'','positive_band':a,'negative_band':b,'band_keys':definition['bands'],'required_bands':[copy.deepcopy(BANDS[key]) for key in definition['bands']],'reference_band':BANDS[reference]['id'],'reference_key':reference,'reference_resolution_m':BANDS[reference]['native_resolution_m'],'version':'1.0' if legacy else VERSION,'source_url':url,'supported_source_ids':['pc_sentinel2_l2a'] if index_id=='ndre' else ['pc_sentinel2_l2a','pc_landsat_c2_l2'],'value_range':[None,None] if nonlinear else [-1,1],'threshold_range':[-2,2] if nonlinear else [-1,1],'change_threshold_range':[0,4] if nonlinear else [0,2],'visualization_range':[-1,1],'histogram_policy':'all_valid_values','limitations':[limit,'SCL保留4/5/6/7；无效/负反射率/公式域无效剔除，阈值面积仅为投影格网条件面积。']}
    if index_id in ('evi2','savi','msavi2','gndvi','ndre'):
        result['visualization']={'breaks':[0,.2,.5],'colors':['#b6a48d','#d1cf8a','#a3c58f','#53a97f']}
    elif index_id=='bsi':result['visualization']={'breaks':[-.2,0,.2],'colors':['#78b39b','#b1c494','#d0b68c','#ab7449']}
    return result

def unsupported_index_requested(question):
    return bool(re.search(r'(?<![a-z])(?:evi|osavi|lai|ndci|awei|msavi|gci|fvc|ndsi)(?![a-z0-9])',question.lower()) or any(w in question for w in ('叶面积指数','植被覆盖度')) or ('增强植被指数' in question and not any(v in question for v in ALIASES['evi2']) and 'evi2' not in detect_indices(question)))

def choose_index(question,index_id=None):
    if unsupported_index_requested(question):raise GeoError('INDEX_UNSUPPORTED','问题明确指定的指数尚未接入，不能用NDVI或另一个指数代替',422)
    found=detect_indices(question)
    if len(found)>1:raise GeoError('MULTIPLE_INDICES','本次执行一次处理一个指数，请分别提交已明确的指数需求',422)
    if index_id is not None:
        if not isinstance(index_id,str) or index_id not in DEFINITIONS:raise GeoError('INDEX_UNSUPPORTED','指数ID不受支持',422)
        if found and found[0]!=index_id:raise GeoError('INDEX_CONFLICT','问题中的指数与结构化选择不一致，请保留原指数或重提需求',422)
        return specification(index_id)
    return specification(found[0] if found else 'ndvi')

def available_indices(ready=True):
    return [{**specification(id),'available':ready} for id in DEFINITIONS]


def evaluate_index(index_id,values):
    """Fixed formula dispatch. Returns values and per-cell domain diagnostics.

    No generated expression or eval is accepted. Existing normalized ratios keep
    their float32 operations; nonlinear/four-band arithmetic uses float64 before
    the documented float32 result export, without clipping output values.
    """
    import numpy as np
    spec=specification(index_id);required=spec['band_keys']
    try:arrays={key:np.asarray(values[key]) for key in required}
    except KeyError:raise GeoError('MISSING_BAND','指数计算缺少所需已定标波段',422)
    shape=arrays[required[0]].shape
    if any(value.shape!=shape for value in arrays.values()):raise GeoError('INDEX_GRID_MISMATCH','所需波段未对齐同一参考格网',422)
    denominator_bad=np.zeros(shape,dtype=bool);radicand_bad=np.zeros(shape,dtype=bool)
    with np.errstate(all='ignore'):
        if spec['formula_kind']=='normalized_difference':
            a,b=arrays[spec['positive_band']],arrays[spec['negative_band']]
            denominator=a+b;denominator_bad=~(denominator>0)
            result=np.full(shape,np.nan,dtype='float32');np.divide(a-b,denominator,out=result,where=~denominator_bad)
        else:
            calibrated={key:value.astype('float64') for key,value in arrays.items()}
            if index_id=='evi2':
                a,b=calibrated['nir'],calibrated['red'];numerator=2.5*(a-b);denominator=a+2.4*b+1
            elif index_id=='savi':
                a,b=calibrated['nir'],calibrated['red'];numerator=1.5*(a-b);denominator=a+b+.5
            elif index_id=='msavi2':
                a,b=calibrated['nir'],calibrated['red'];radicand=(2*a+1)**2-8*(a-b);radicand_bad=~(radicand>=0)
                root=np.full(shape,np.nan,dtype='float64');np.sqrt(radicand,out=root,where=~radicand_bad)
                result=((2*a+1-root)/2).astype('float32')
            elif index_id=='bsi':
                a=calibrated['swir1']+calibrated['red'];b=calibrated['nir']+calibrated['blue'];numerator=a-b;denominator=a+b
            else:raise GeoError('INDEX_UNSUPPORTED','此指数没有固定计算实现',422)
            if index_id!='msavi2':
                denominator_bad=~(denominator>1e-12)
                result64=np.full(shape,np.nan,dtype='float64');np.divide(numerator,denominator,out=result64,where=~denominator_bad)
                result=result64.astype('float32')
    nonfinite=~np.isfinite(result)
    return result,{'valid':~denominator_bad&~radicand_bad&~nonfinite,'invalid_denominator':denominator_bad,'invalid_radicand':radicand_bad,'nonfinite_result':nonfinite&~denominator_bad&~radicand_bad}
