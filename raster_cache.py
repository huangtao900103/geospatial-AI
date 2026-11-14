"""Bounded process-local cache for unsigned-source, uncalibrated raster windows.

The byte budget counts retained ndarray buffers, not total Python/GDAL memory.
This is an execution optimisation, not upstream content revalidation. Cache
runtime metadata must never be included in the analysis input content hash.
"""
import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import datetime,timezone
from urllib.parse import urlsplit

class _Flight:
    def __init__(self):self.done=threading.Event();self.value=None;self.error=None

class RasterWindowCache:
    def __init__(self,max_bytes=64*1024*1024,ttl_seconds=1800,max_entries=128,clock=None):
        if max_bytes<0 or ttl_seconds<=0 or max_entries<1:raise ValueError('Invalid raster cache limits')
        self.max_bytes=max_bytes;self.ttl_seconds=ttl_seconds;self.max_entries=max_entries;self.clock=clock or time.monotonic
        self.lock=threading.RLock();self.entries=OrderedDict();self.flights={};self.bytes=0
        self.counts={k:0 for k in ('hits','misses','waits','load_errors','evictions','expirations','oversize_skips','loaded_raw_bytes')}
    @staticmethod
    def key(value):
        def inspect(v):
            if isinstance(v,dict):
                for k,x in v.items():
                    if not isinstance(k,str):raise ValueError('Cache keys require string field names')
                    if k.lower() in ('token','sig','signature','authorization','sas','signed_url'):raise ValueError('Credentials are not cache keys')
                    inspect(x)
            elif isinstance(v,(list,tuple)):
                for x in v:inspect(x)
            elif isinstance(v,str) and v.startswith(('https://','http://')):
                u=urlsplit(v)
                if u.query or u.fragment or u.username or u.password:raise ValueError('Raster cache requires unsigned asset URLs')
        inspect(value);encoded=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        if len(encoded)>8192:raise ValueError('Cache key too large')
        return hashlib.sha256(encoded.encode()).hexdigest()
    def _prune(self):
        expired=[k for k,v in self.entries.items() if self.clock()-v['at']>=self.ttl_seconds]
        for k in expired:self.bytes-=self.entries.pop(k)['bytes'];self.counts['expirations']+=1
    @staticmethod
    def _freeze(value):
        import numpy as np
        if not isinstance(value,dict) or not isinstance(value.get('raw'),np.ndarray) or value['raw'].dtype.hasobject:raise ValueError('Raster cache values require a numeric raw ndarray')
        # Own the buffer; the loader cannot mutate it after insertion.
        raw=np.array(value['raw'],copy=True,order='C');raw.setflags(write=False)
        saved={k:copy.deepcopy(v) for k,v in value.items() if k not in ('raw','cache')};saved['raw']=raw
        return saved,raw.nbytes
    def _view(self,entry):
        result={k:copy.deepcopy(v) for k,v in entry['value'].items() if k!='raw'}
        result['raw']=entry['value']['raw'].view();result['raw'].setflags(write=False)
        result['cache']={'fetched_at':entry['fetched_at'],'age_seconds':max(0,self.clock()-entry['at']),'ttl_seconds':self.ttl_seconds,'source_revalidated':False}
        return result
    def get_or_load(self,key,loader,check=None):
        token=self.key(key);check=check or (lambda:None)
        while True:
            check()
            with self.lock:
                self._prune();entry=self.entries.get(token)
                if entry:
                    self.entries.move_to_end(token);self.counts['hits']+=1;return self._view(entry),True
                flight=self.flights.get(token)
                if flight:self.counts['waits']+=1;leader=False
                else:flight=_Flight();self.flights[token]=flight;self.counts['misses']+=1;leader=True
            if leader:
                try:
                    check();loaded=loader();check();value,size=self._freeze(loaded)
                    entry={'value':value,'bytes':size,'at':self.clock(),'fetched_at':datetime.now(timezone.utc).isoformat(timespec='milliseconds')}
                    with self.lock:
                        self.counts['loaded_raw_bytes']+=size
                        if size<=self.max_bytes:
                            self._prune()
                            while self.entries and (self.bytes+size>self.max_bytes or len(self.entries)>=self.max_entries):
                                _,old=self.entries.popitem(last=False);self.bytes-=old['bytes'];self.counts['evictions']+=1
                            self.entries[token]=entry;self.bytes+=size
                        else:self.counts['oversize_skips']+=1
                        flight.value=entry
                    return self._view(entry),False
                except BaseException as e:
                    with self.lock:self.counts['load_errors']+=1;flight.error=e
                    raise
                finally:
                    with self.lock:self.flights.pop(token,None);flight.done.set()
            while not flight.done.wait(.1):check()
            check()
            if flight.error is not None:
                # A cancelled leader must not cancel an independently live job.
                if getattr(flight.error,'code',None) in ('CANCELLED','INTERRUPTED','QUESTION_STOPPED','TASK_CANCELLED'):continue
                raise flight.error
            with self.lock:self.counts['hits']+=1
            return self._view(flight.value),True
    def stats(self):
        with self.lock:
            self._prune();return {**self.counts,'entries':len(self.entries),'inflight':len(self.flights),'current_bytes':self.bytes,'max_bytes':self.max_bytes,'max_entries':self.max_entries,'ttl_seconds':self.ttl_seconds,'storage':'process_memory_raw_array_buffers','upstream_revalidation':False}

WINDOW_CACHE=RasterWindowCache()
