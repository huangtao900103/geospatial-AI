#!/usr/bin/env python3
"""Bounded request -> official STAC -> actual QA -> native city production."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.agriculture.city_spectral import produce,validate_product,write_json,production_request,prepare_catalogs,canonical
from backend.analysis.landcover_services import load_boundary
import hashlib
from datetime import datetime,timezone
from uuid import uuid4
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',help='New output root; existing ready products are never overwritten')
p.add_argument('--catalog-dir',help='Frozen input snapshots in frozen mode')
p.add_argument('--mode',choices=['live','frozen'],default='live')
p.add_argument('--before',nargs=2,metavar=('START','END'),default=['2026-09-01','2026-09-30'])
p.add_argument('--after',nargs=2,metavar=('START','END'),default=['2026-10-01','2026-11-14'])
p.add_argument('--cloud-max',type=float,default=80)
p.add_argument('--max-candidates',type=int,default=3)
p.add_argument('--catalog-limit',type=int,default=60)
p.add_argument('--minimum-qa',type=float,default=.7)
p.add_argument('--crop-reference',help='Explicit historical reference; validation defaults to the verified copy in each product')
p.add_argument('--discover-only',action='store_true',help='Retrieve and freeze bounded real STAC metadata without raster IO')
p.add_argument('--validate-only',action='store_true',help='Read-only recomputation of archived native pixels')
p.add_argument('--validation-report',help='Optional validation report outside the production/data directories')
a=p.parse_args()
if a.validate_only:
 root=Path(a.output or 'data/agriculture-atlas');reports=[]
 for folder in sorted(root.glob('xiantao-*-spectral-*')):
  if not (folder/'result.json').is_file():continue
  result=json.loads((folder/'result.json').read_text());reports.append({'id':result['id'],'validation':validate_product(folder,result,a.crop_reference)})
 if not reports:p.error('No ready spectral products in the requested output root')
 if a.validation_report:
  destination=Path(a.validation_report).resolve();blocked=[root.resolve(),Path('data').resolve()]
  if any(destination==base or base in destination.parents for base in blocked):p.error('Validation report must be outside production and data directories')
  destination.parent.mkdir(parents=True,exist_ok=True);write_json(destination,reports)
 print(json.dumps(reports,ensure_ascii=False,indent=2))
else:
 request=production_request({'before':a.before,'after':a.after},a.cloud_max,a.max_candidates,a.catalog_limit,a.minimum_qa)
 run_id=hashlib.sha256(canonical(request)).hexdigest()[:16];run_name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+run_id+'-'+uuid4().hex[:6];root=Path(a.output or ('output/xiantao/spectral-runs/'+run_name+'/products'));catalogs=root.parent/(root.name+'-catalogs-'+run_id)
 if a.mode=='frozen' and not a.catalog_dir:p.error('--mode frozen requires --catalog-dir with actual snapshots')
 if a.discover_only:
  boundary,metadata=load_boundary();contracts,evidence=prepare_catalogs(boundary,metadata,request,catalogs,a.mode,frozen_dir=a.catalog_dir)
  print(json.dumps({'catalog_dir':str(catalogs),'request':request,'candidates':{epoch:[s['scene_id'] for s in scenes] for epoch,scenes in contracts.items()},'screening':evidence['screening']},ensure_ascii=False,indent=2))
 else:
  products=produce(root,catalogs,request=request,mode=a.mode,frozen_dir=a.catalog_dir,crop_reference_path=a.crop_reference or 'data/agriculture-atlas/xiantao-2024-classification/classification.tif',progress=lambda value:print(value,flush=True))
  print(json.dumps([{'id':r['id'],'status':r['status'],'dates':r['summary']['actual_dates'],'valid_ratio':r['summary']['valid_ratio'],'mean':r['summary']['mean_index']} for r in products],ensure_ascii=False,indent=2))
