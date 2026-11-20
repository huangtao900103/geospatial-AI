"""Explicit synthetic native input for isolated tests, never production seeding."""
import json
from pathlib import Path


def seed_spectral_fixture(workspace, ident='production_browser_spectral'):
    import numpy as np
    import rasterio
    from PIL import Image
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds
    from backend.analysis.production_services import make_plan
    from backend.analysis.native_raster import VERSION
    from backend.analysis.spectral_indices import specification

    # A small bimodal index field and one unknown cell. It is deliberately
    # labelled synthetic in every record; only the processing code is real.
    grid=from_origin(720000,3355000,10,10)
    bbox=list(transform_bounds('EPSG:32649','EPSG:4326',720000,3354680,720320,3355000))
    values=np.full((32,32),.1,dtype='float32');values[:,16:]=.8;values[0,0]=np.nan
    folder=Path(workspace.production.output_dir)/ident;folder.mkdir(parents=True)
    with rasterio.open(folder/'analysis.tif','w',driver='GTiff',width=32,height=32,count=3,
                       dtype='float32',crs='EPSG:32649',transform=grid,nodata=float('nan')) as ds:
        for number,(name,array) in enumerate((('after_ndvi',values),('common_valid_mask',np.isfinite(values).astype('float32')),('aoi_mask',np.ones_like(values))),1):
            ds.write(array,number);ds.set_band_description(number,name)
    Image.new('RGBA',(32,32),(54,139,109,150)).save(folder/'overlay.png')
    plan=make_plan({'requirement':'浏览器隔离合成输入，非卫星观测，2025-11-14 NDVI',
                    'mode':'sentinel_cog','sampling':'native','goal':'ndvi','index_id':'ndvi',
                    'bbox':bbox,'periods':{'before':['2025-11-13','2025-11-13'],'after':['2025-11-14','2025-11-14']},'source_id':'pc_sentinel2_l2a'})
    result={'summary':{'index_id':'ndvi','index':specification('ndvi'),'sampling':'native',
                       'valid_count':1023,'native_pixel_count':1024,'valid_ratio':1023/1024},
            'provenance':{'source_type':'public_satellite_pixels','source_id':'pc_sentinel2_l2a',
                          'index_id':'ndvi','bbox':bbox,'input_sha256':'b'*64,'crs':'EPSG:32649',
                          'sampling':'native','algorithm_version':VERSION,
                          'fixture':True,'fixture_note':'Explicit synthetic isolated raster; not satellite observations.',
                          'source_details':{'scenes':{'after':{'datetime':'2025-11-14T02:30:00Z'}}}},
            'report':{'limitations':['隔离测试专用合成格网，不是仙桃实测或卫星观测。'],'findings':[]}}
    (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    record={'id':ident,'name':'浏览器测试专用 · 合成光谱格网','status':'succeeded',
            'created_at':'2025-11-14T03:00:00Z','plan':plan,'result':result}
    workspace.save('production_task',record)
    answer={'question_id':'question_browser_spectral','title':'浏览器测试专用 · 合成光谱源成果',
            'summary':'隔离测试专用的原生格网输入，不是实际卫星观测。',
            'metrics':[{'id':'mean_index','label':'合成格网均值','value':float(np.nanmean(values)),'unit':''}],
            'warnings':result['report']['limitations'],'map_effect':'none',
            'result_ref':{'type':'production','task_id':ident,'url':'/api/production/tasks/'+ident,'image_kind':'ndvi'},
            'downloads':[],'provenance':{'fixture':True}}
    workspace.save('assistant_question',{'id':'question_browser_spectral','question':'浏览器隔离原生光谱源成果',
                   'capability_id':'public_native_raster','status':'answered','progress':100,'message':'隔离输入已归档',
                   'context':{},'binding':{'type':'production','task_id':ident,'owned':False,'reused':True},
                   'answer':answer,'answer_snapshot':answer,'logs':[],
                   'created_at':'2025-11-14T03:00:00Z','updated_at':'2025-11-14T03:00:00Z'})
    return record
