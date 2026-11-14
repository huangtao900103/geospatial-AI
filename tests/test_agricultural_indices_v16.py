"""Independent arithmetic and local GeoTIFF evidence; no remote data claims."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from importlib.util import find_spec
from geo_services import GeoError
from spectral_indices import specification,choose_index,detect_indices,evaluate_index,BANDS
from landsat_reader import specification as landsat_specification
from algorithm_catalog import contract,catalog,match_observation
from catalog_adapters import assets_metadata
from production_services import SentinelReader
from native_raster import grids,compute_native,read_native
from tests.test_algorithm_catalog import observation

READY=all(find_spec(module) for module in ('numpy','rasterio','PIL'))
NEW=('evi2','savi','msavi2','gndvi','ndre','bsi')


def expected(index,v):
    """Scalar reference using Python math, separate from the vector executor."""
    if index=='evi2':return 2.5*(v['nir']-v['red'])/(v['nir']+2.4*v['red']+1)
    if index=='savi':return 1.5*(v['nir']-v['red'])/(v['nir']+v['red']+.5)
    if index=='msavi2':return (2*v['nir']+1-math.sqrt((2*v['nir']+1)**2-8*(v['nir']-v['red'])))/2
    if index=='gndvi':return (v['nir']-v['green'])/(v['nir']+v['green'])
    if index=='ndre':return (v['nir_narrow']-v['rededge'])/(v['nir_narrow']+v['rededge'])
    if index=='bsi':return ((v['swir1']+v['red'])-(v['nir']+v['blue']))/((v['swir1']+v['red'])+(v['nir']+v['blue']))
    raise AssertionError(index)


class AgriculturalContractTest(unittest.TestCase):
    def test_explicit_names_are_distinct_and_alias_overlap_is_resolved(self):
        for index in NEW:self.assertEqual(choose_index('计算'+index.upper())['id'],index)
        self.assertEqual(choose_index('增强植被指数EVI2')['id'],'evi2')
        self.assertEqual(detect_indices('改进型土壤调节植被指数'),['msavi2'])
        self.assertEqual(detect_indices('MSAVI2和SAVI'),['savi','msavi2'])
        self.assertEqual(detect_indices('MSAVI20'),[])
        for q in ('EVI','MSAVI','OSAVI','LAI'):
            with self.assertRaises(GeoError):choose_index(q)
    def test_native_band_contracts_and_formula_range_are_explicit(self):
        self.assertEqual(specification('ndre')['band_keys'],['nir_narrow','rededge'])
        self.assertEqual(specification('ndre')['reference_resolution_m'],20)
        self.assertEqual([b['xml_band_id'] for b in specification('ndre')['required_bands']],['8','4'])
        self.assertEqual(specification('bsi')['band_keys'],['swir1','red','nir','blue'])
        self.assertEqual(specification('bsi')['reference_resolution_m'],20)
        for index in NEW:
            spec=specification(index)
            self.assertEqual(spec['histogram_policy'],'all_valid_values')
            if index in ('evi2','savi','msavi2'):self.assertEqual(spec['value_range'],[None,None])
            else:self.assertEqual(spec['value_range'],[-1,1])
        self.assertEqual(specification('ndvi')['version'],'1.0')
    def test_unavailable_landsat_rededge_returns_blocked_contract_and_match(self):
        blocked=contract('pc_landsat_c2_l2','ndre')
        self.assertFalse(blocked['available']);self.assertFalse(blocked['source_supported'])
        self.assertEqual(blocked['required_bands'],[]);self.assertEqual(blocked['blocking_reasons'][0]['code'],'SOURCE_INDEX_UNSUPPORTED')
        self.assertEqual(len(catalog()['contracts']),23)
        with patch('algorithm_catalog.runtime_ready',return_value=True):
            match=match_observation(observation('pc_landsat_c2_l2'),'ndre')['matches'][0]
        self.assertFalse(match['ready']);self.assertIn('SOURCE_INDEX_UNSUPPORTED',[g['code'] for g in match['gaps']])
        with self.assertRaises(GeoError) as error:landsat_specification('ndre')
        self.assertEqual(error.exception.code,'SOURCE_INDEX_UNSUPPORTED')
    def test_native_rededge_aliases_and_blue_are_not_inherited_ten_metre_gsd(self):
        assets={key:{'href':'https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/unit/'+key+'.tif','eo:bands':[{'common_name':'rededge' if key=='B8A' else 'blue'}]} for key in ('B02','B05','B8A')}
        result={a['key']:a for a in assets_metadata('pc_sentinel2_l2a',assets,{'gsd':10})}
        self.assertEqual([(result[k]['common_name'],result[k]['resolution']) for k in ('B02','B05','B8A')],[('blue',10),('rededge',20),('nir_narrow',20)])
        o=observation();o['assets']=[a for a in o['assets'] if a['key'] not in assets]+list(result.values())
        with patch('algorithm_catalog.runtime_ready',return_value=True):self.assertTrue(match_observation(o,'ndre')['matches'][0]['ready'])
    def test_exact_xml_offsets_for_new_required_bands(self):
        reader=SentinelReader(None)
        xml=('<r><BOA_QUANTIFICATION_VALUE>10000</BOA_QUANTIFICATION_VALUE>'+''.join('<BOA_ADD_OFFSET band_id="%d">%d</BOA_ADD_OFFSET>'%(i,-1000-i) for i in range(13))+'</r>').encode()
        reader.fetch_bytes=lambda *a,**kw:xml;reader.signed_url=lambda url,check:url
        for index in NEW:
            spec=specification(index);values=reader.calibration('https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/unit/metadata.xml',lambda:None,spec['band_keys'])
            self.assertEqual(set(values)-{'provenance'},set(spec['band_keys']))
            for band in spec['required_bands']:
                self.assertEqual(values[band['name']]['scale'],.0001)
                self.assertAlmostEqual(values[band['name']]['offset'],(-1000-int(band['xml_band_id']))/10000)


@unittest.skipUnless(READY,'project raster runtime required')
class AgriculturalMathAndExportTest(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def fixture(self,index):
        import numpy as np
        from rasterio.transform import array_bounds
        from rasterio.warp import transform_bounds
        from affine import Affine
        spec=specification(index);res=spec['reference_resolution_m'];shape=(2,4)
        v={'nir':.5,'red':.1,'green':.3,'swir1':.2,'blue':.05,'nir_narrow':.45,'rededge':.2}
        epoch={b['name']:np.full(shape,v[b['name']],dtype='float32') for b in spec['required_bands']};epoch['scl']=np.full(shape,4,dtype='float32')
        transform=Affine(res,0,720000,0,-res,3360000);bbox=list(transform_bounds('EPSG:32649','EPSG:4326',*array_bounds(*shape,transform)))
        meta={'sampling':'native','source_type':'public_satellite_pixels','provider':'offline unit fixture','crs':'EPSG:32649','transform':list(transform)[:6],'cell_area_m2':res**2,'input_sha256':'a'*64,'scenes':{'after':{'datetime':'2025-11-15T00:00:00Z'}},'resampling':'nearest'}
        p={'mode':'sentinel_cog','sampling':'native','index_id':index,'index':spec,'goal':'ndvi','direction':'absolute','threshold':0,'selection':{'operator':'gt'},'aoi':{'bbox':bbox},'requirement':'offline arithmetic fixture '+index,'periods':{'after':['2025-11-01','2025-11-30']},'limitations':spec['limitations']}
        return p,{'after':epoch,'metadata':meta,'_mask':np.ones(shape,dtype='bool')}
    def test_six_formulas_match_independent_scalar_math(self):
        import numpy as np
        for index in NEW:
            p,b=self.fixture(index);actual,domain=evaluate_index(index,b['after'])
            v={key:float(b['after'][key][0,0]) for key in p['index']['band_keys']}
            self.assertTrue(domain['valid'].all());np.testing.assert_allclose(actual,expected(index,v),rtol=0,atol=6e-8)
        # Adding constants means raw DN cannot cancel as in a simple ratio.
        a,_=evaluate_index('evi2',{'nir':np.array([5000.]),'red':np.array([1000.])})
        calibrated,_=evaluate_index('evi2',{'nir':np.array([.4]),'red':np.array([0.])})
        self.assertNotAlmostEqual(float(a[0]),float(calibrated[0]),places=2)
    def test_root_and_near_zero_denominator_are_masked_without_clamping(self):
        import numpy as np
        value,domain=evaluate_index('msavi2',{'nir':np.array([.5,.5]),'red':np.array([-.1,0.])})
        self.assertTrue(np.isnan(value[0]));self.assertEqual(value[1],1)
        self.assertEqual(domain['invalid_radicand'].tolist(),[True,False])
        value,domain=evaluate_index('bsi',{k:np.array([1e-15,.1]) for k in ('swir1','red','nir','blue')})
        self.assertTrue(np.isnan(value[0]));self.assertEqual(domain['invalid_denominator'].tolist(),[True,False])
        p,b=self.fixture('msavi2');b['after']['nir'][0,0]=.5;b['after']['red'][0,0]=-.1
        out,reasons=grids(b,'msavi2');self.assertFalse(out['after'][1][0,0]);self.assertEqual(reasons['after']['invalid_radicand'],1)
    def test_every_four_band_input_participates_in_mask_and_missing_is_error(self):
        import numpy as np
        p,b=self.fixture('bsi');b['after']['blue'][0,0]=np.nan;b['after']['red'][0,1]=-.1;b['after']['scl'][0,2]=9
        out,reasons=grids(b,'bsi');self.assertEqual(int(out['after'][1].sum()),5);self.assertEqual(reasons['after']['nodata'],1);self.assertEqual(reasons['after']['invalid_reflectance'],1)
        b['after'].pop('blue')
        with self.assertRaises(GeoError):evaluate_index('bsi',b['after'])
        p,b=self.fixture('ndre');b['after']['red']=np.full((2,4),np.nan)
        self.assertTrue(grids(b,'ndre')[0]['after'][1].all())
    def test_unbounded_values_are_retained_and_histograms_cover_every_value(self):
        for index in ('evi2','savi','msavi2'):
            p,b=self.fixture(index)
            if index=='msavi2':b['after']['nir'][0,0]=0;b['after']['red'][0,0]=3
            else:b['after']['nir'][0,0]=3;b['after']['red'][0,0]=0
            result=compute_native(p,b,'fixture',self.root/index,lambda:None);s=result['summary']
            self.assertEqual(s['outside_normalized_range_count'],1);self.assertFalse(s['output_values_clipped'])
            self.assertEqual(sum(row['count'] for row in s['index_histogram']),8)
            self.assertTrue(s['min_index']<-1 if index=='msavi2' else s['max_index']>1)
    def test_all_new_exports_support_independent_formula_mask_and_area_checks(self):
        import numpy as np,rasterio
        for index in NEW:
            p,b=self.fixture(index);b['after']['scl'][0,0]=9;b['_mask'][1,3]=False
            result=compute_native(p,b,'fixture',self.root/index,lambda:None)
            with rasterio.open(self.root/index/'analysis.tif') as ds:
                data={name:ds.read(i+1) for i,name in enumerate(ds.descriptions)}
                names=['after_'+key+'_reflectance' for key in p['index']['band_keys']]
                self.assertTrue(all(name in data for name in names))
                valid=(data['aoi_mask']==1)&np.isin(data['after_scl'],[4,5,6,7])
                for name in names:valid&=np.isfinite(data[name])&(data[name]>=0)
                np.testing.assert_array_equal(valid,data['common_valid_mask']==1)
                for row,col in zip(*np.where(valid)):
                    v={key:float(data['after_'+key+'_reflectance'][row,col]) for key in p['index']['band_keys']}
                    self.assertAlmostEqual(float(data['after_'+index][row,col]),expected(index,v),places=7)
                self.assertEqual(result['summary']['valid_count'],6)
                self.assertEqual(result['summary']['selected_area_ha'],int((data['after_'+index][valid]>0).sum())*p['index']['reference_resolution_m']**2/10000)
                self.assertEqual(abs(ds.transform.a),p['index']['reference_resolution_m'])
    def test_rededge_reader_enforces_actual_native_reference_and_input_spacing(self):
        import numpy as np,rasterio
        from affine import Affine
        p,b=self.fixture('ndre');t=Affine(*b['metadata']['transform'])
        files={}
        for name,value in [('nir_narrow',4500),('rededge',2000),('scl',4)]:
            path=self.root/(name+'.tif');files[name]=path
            with rasterio.open(path,'w',driver='GTiff',height=2,width=4,count=1,dtype='uint16',crs=b['metadata']['crs'],transform=t,nodata=0) as dst:dst.write(np.full((2,4),value,dtype='uint16'),1)
        assets={name:{'href':str(path),'scale':.0001 if name!='scl' else 1,'offset':0,'nodata':0} for name,path in files.items()}
        scenes={'after':{'id':'local-offline-20m-fixture','datetime':'2025-11-15T00:00:00Z','assets':assets}}
        reader=type('LocalReader',(),{'signed_url':lambda self,url,check:url})()
        result=read_native(p,scenes,reader,lambda:None)
        self.assertEqual(result['metadata']['cell_area_m2'],400)
        self.assertEqual(result['metadata']['reference_band'],'B8A')
        self.assertEqual(int(grids(result,'ndre')[0]['after'][1].sum()),8)
        # A mislabeled fine grid is rejected rather than advertised as 20m.
        for name,code in [('rededge','INPUT_GRID_MISMATCH'),('nir_narrow','REFERENCE_GRID_MISMATCH')]:
            with rasterio.open(files[name],'r+') as ds:ds.transform=Affine(10,0,t.c,0,-10,t.f)
            with self.assertRaises(GeoError) as error:read_native(p,scenes,reader,lambda:None)
            self.assertEqual(error.exception.code,code)
            with rasterio.open(files[name],'r+') as ds:ds.transform=t


if __name__=='__main__':unittest.main()
