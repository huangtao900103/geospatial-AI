#!/usr/bin/env python3
"""CI-only Docker smoke: disposable Compose project on host loopback port 8020.

Seed only an empty workspace, then verify the same record after container restart.
Never invoke against a production workspace; this is not a deployment entrypoint.
"""
import json
import sys
from pathlib import Path
from urllib.request import Request,urlopen

BASE='http://127.0.0.1:8020/api'
STATE=Path('output/docker-smoke-state.json')


def request(path,body=None):
    req=Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json','Origin':'http://127.0.0.1:8020'})
    with urlopen(req,timeout=15) as response:return json.load(response)


def main():
    mode=sys.argv[1] if len(sys.argv)>1 else ''
    if mode=='seed':
        assert request('/agri-operations/registries')['registries']==[], 'Docker test requires a new, empty data volume'
        assert request('/datasets')==[], 'Docker test cannot seed an existing workspace'
        reg=request('/agri-operations/registries',{'name':'Docker isolated verification','kind':'farmland','source':'CI-only synthetic geometry','actor':'CI'})
        geo={'type':'Polygon','coordinates':[[[113,30],[113.01,30],[113.01,30.01],[113,30.01],[113,30]]]}
        request('/agri-operations/registries/'+reg['id']+'/import',{'geojson':{'type':'FeatureCollection','features':[{'type':'Feature','geometry':geo,'properties':{'code':'ci-only'}}]},'code_field':'code','batch_id':'docker-ci','expected_revision':1,'actor':'CI'})
        event=request('/agri-operations/events',{'title':'Docker test event','hazard':'other','geometry':geo,'source_name':'CI-only input','source_kind':'manual','issued_at':'2026-01-01T00:00:00Z','actor':'CI'})
        impact=request('/agri-operations/events/'+event['id']+'/analyze',{'registry_ids':[reg['id']],'actor':'CI'})
        assert impact['object_count']==1
        STATE.parent.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps({'registry_id':reg['id'],'impact_id':impact['id']}))
        print('Docker smoke: real SQLite/RTree and Shapely intersection passed')
    elif mode=='verify':
        state=json.loads(STATE.read_text());objects=request('/agri-operations/objects?registry_id='+state['registry_id'])
        assert objects['total']==1 and objects['items'][0]['code']=='ci-only'
        assert request('/agri-operations/impacts/'+state['impact_id'])['object_count']==1
        print('Docker smoke: database and frozen result persisted across restart')
    else:raise SystemExit('Usage: python deploy/docker/container_smoke.py seed|verify (disposable Docker workspace only)')


if __name__=='__main__':main()
