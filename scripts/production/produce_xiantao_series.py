#!/usr/bin/env python3
"""Produce bounded real, single-platform Xiantao temporal observations."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.agriculture.agri_temporal import request,produce,validate,add_quality_comparison
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',default='output/agri-research/series-2026')
p.add_argument('--start',default='2026-05-01');p.add_argument('--end',default='2026-10-31')
p.add_argument('--platform',choices=['landsat-8','landsat-9'],default='landsat-8')
p.add_argument('--interval',choices=['month','dekad'],default='month')
p.add_argument('--max-candidates',type=int,default=2);p.add_argument('--minimum-qa',type=float,default=.7)
p.add_argument('--archive-root',default='data/agriculture-atlas');p.add_argument('--validate-only',action='store_true')
p.add_argument('--add-quality-comparison',action='store_true',help='Offline derived quality subset; preserve frozen source pixels')
p.add_argument('--frozen-catalog-dir');p.add_argument('--reuse-from')
a=p.parse_args()
if a.validate_only:result=validate(a.output)
elif a.add_quality_comparison:
 result=add_quality_comparison(a.output)
 result={key:result[key] for key in ('id','status','sequence_summary','quality_comparison','validation')}
else:
 result=produce(a.output,request(a.start,a.end,a.platform,a.interval,a.max_candidates,a.minimum_qa),archive_root=a.archive_root,progress=lambda value:print(value,flush=True),frozen_catalog_dir=a.frozen_catalog_dir,reuse_from=a.reuse_from)
 result={key:result[key] for key in ('id','status','sequence_summary','validation')}
print(json.dumps(result,ensure_ascii=False,indent=2))
