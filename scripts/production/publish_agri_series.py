#!/usr/bin/env python3
"""Publish a validated immutable series and actual map products; never overwrite."""
import argparse
import copy
import json
import shutil
import sys
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.agriculture.agri_research_services import sha_file,canonical
from backend.agriculture.agriculture_atlas import AgricultureAtlas
from backend.agriculture.city_spectral import LEGENDS

def write(path,value):path.write_bytes(canonical(value))

def publish(source,data):
    raw=json.loads((source/'result.json').read_text())
    if raw.get('validation',{}).get('status')!='passed' or not raw.get('quality_comparison'):raise ValueError('Final independently validated quality comparison required')
    for entry in raw['artifacts']:
        path=source/entry['file']
        if path.is_symlink() or source.resolve() not in path.resolve().parents or sha_file(path)!=entry['sha256']:raise ValueError('Artifact mismatch')
    ident=raw['id'];destination=data/'agri-research'/'series'/ident
    if destination.exists():raise ValueError('Published series already exists; choose a new version ID')
    destination.parent.mkdir(exist_ok=True,parents=True)
    stage=destination.parent/('.stage-'+uuid.uuid4().hex)
    stage.mkdir()
    for entry in raw['artifacts']:
        target=stage/entry['file'];target.parent.mkdir(exist_ok=True,parents=True);shutil.copy2(source/entry['file'],target)
    shutil.copy2(source/'result.json',stage/'result.json')
    boundary=json.loads((source/'boundary.geojson').read_text())
    atlas_root=data/'agriculture-atlas';atlas=AgricultureAtlas(atlas_root)
    publication={'version':'agri-series-publication-1.0','raw_series_sha256':sha_file(source/'result.json'),'observations':{},'changes':{}}
    created=[]
    for kind,items in [('observations',raw['observations']),('changes',raw['changes'])]:
        for index,item in enumerate(items):
            change=kind=='changes'
            if change and not item.get('comparison_eligible'):continue
            if not change and not item.get('ndvi_valid_pixel_count'):continue
            key=item.get('id','change-'+str(index+1)) if change else item['scene_id']
            dates=[item['before_date'],item['after_date']] if change else [item['date']]
            product_id='xiantao-l8-'+('delta-'+'-'.join(d.replace('-','') for d in dates) if change else 'ndvi-'+dates[0].replace('-',''))
            target=atlas_root/product_id
            if target.exists():raise ValueError('Product already exists: '+product_id)
            temporary=atlas_root/('.stage-'+uuid.uuid4().hex);temporary.mkdir(parents=True)
            for name,relative in [('analysis.tif',item['native_file']),('overlay.png',item['overlay_file']),('boundary.geojson','boundary.geojson')]:shutil.copy2(source/relative,temporary/name)
            write(temporary/'observation.json',item)
            ratio=item['common_valid_ratio'] if change else item['ndvi_valid_ratio']
            mean=item['mean_delta_ndvi'] if change else item['ndvi_mean']
            title='仙桃NDVI变化 · '+'至'.join(dates) if change else '仙桃长势观测 · '+dates[0]
            summary={'index_id':'ndvi','operation':'after_minus_before' if change else 'single_observation','actual_dates':item.get('actual_dates',[item.get('datetime',dates[0])]),'valid_ratio':ratio,'mean_index':mean,'quality_target_met':bool(item.get('comparison_eligible')),'native_crs':raw['grid']['crs'],'resolution_m':30,'boundary_year':2017}
            if change:summary.update(mean_delta=mean,common_valid_pixel_count=item['common_valid_pixel_count'],day_gap=item['day_gap'])
            if not item.get('comparison_eligible'):summary['quality_warning']='低覆盖观测，仅供查看实际数据；未纳入质量合格共同范围曲线。'
            legend=copy.deepcopy(LEGENDS['change' if change else 'ndvi'])
            product={'id':product_id,'title':title,'status':'ready','version':'agri-series-map-1.0','created_at':raw['provenance']['created_at'],'product_type':'city_spectral_reference','scene_ids':['crop_research','sowing','grain'],'scope_label':'仙桃全市 · 2017公开参考市界','period_label':' → '.join(dates),'resolution_m':30,'summary':summary,'summary_metrics':[{'label':'平均NDVI差值' if change else '有效范围平均NDVI','value':mean,'unit':'指数','precision':4},{'label':'共同有效覆盖' if change else '有效覆盖','value':ratio*100,'unit':'%','precision':2}], 'limitations':copy.deepcopy(raw['limitations'])+['全市光谱参考，未限定耕地范围；不等同作物分类、农业损失或违法认定。'], 'layers':[{'id':product_id+'-raster','title':title,'kind':'raster','image':{'url':'overlay.png','bounds':item['image']['bounds']},'legend':legend},{'id':product_id+'-boundary','title':'2017公开参考市界','kind':'geojson','geojson':boundary,'boundary':True}],'native_sha256':sha_file(temporary/'analysis.tif'),'provenance':{'source_id':'pc_landsat_c2_l2','platform':'landsat-8','required_tier':'T1','series_id':ident,'series_result_sha256':publication['raw_series_sha256'],'observation':copy.deepcopy(item),'boundary_source':{'boundary_year_represented':2017,'metadata_url':'https://www.geoboundaries.org/api/current/gbOpen/CHN/ADM2/','sha256':raw['scope']['boundary_source_sha256']},'algorithm':{'index_formula':'NDVI(after) - NDVI(before)' if change else '(NIR - RED) / (NIR + RED)'},'method':'Landsat 8 Tier1地表反射率定标与QA掩膜；按同一30米米制格网计算。缺测保留未知，跨日期差值仅使用共同有效像元。'},'validation':copy.deepcopy(raw['validation'])}
            product['artifacts']=[{'file':name,'sha256':sha_file(temporary/name),'bytes':(temporary/name).stat().st_size} for name in ('analysis.tif','overlay.png','boundary.geojson','observation.json')]
            write(temporary/'result.json',product);temporary.replace(target)
            normalized=atlas.get(product_id);created.append(product_id)
            publication[kind][key]={'product_id':product_id,'product_sha256':normalized['product_sha256']}
    write(stage/'publication.json',publication);stage.replace(destination)
    return {'id':ident,'products':created,'series_root':str(destination),'raw_series_sha256':publication['raw_series_sha256']}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--data',type=Path,default=Path('data'));args=parser.parse_args()
    print(json.dumps(publish(args.source,args.data),ensure_ascii=False,indent=2))
