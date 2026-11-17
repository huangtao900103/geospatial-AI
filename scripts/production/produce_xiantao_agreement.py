#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.agriculture.city_cropland_agreement import produce
p=argparse.ArgumentParser();p.add_argument('--output',type=Path);args=p.parse_args()
r=produce(args.output,lambda detail:print(json.dumps(detail,ensure_ascii=False),flush=True));print(json.dumps({'status':r['status'],'summary':r['summary']},ensure_ascii=False),flush=True)
