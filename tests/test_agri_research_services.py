"""Isolated native-raster and service contracts; no production crop labels."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin, xy
from rasterio.warp import transform

from backend.agriculture.agri_research_services import AgriResearchServices
from backend.core.geo_services import GeoError


class IsolatedStore:
    def __init__(self, root):
        self.db_path = str(Path(root) / 'test-only.sqlite3')
        self.rows = {}

    def save(self, kind, row):
        self.rows.setdefault(kind, {})[row['id']] = copy.deepcopy(row)
        return copy.deepcopy(row)

    def get(self, kind, ident):
        if ident not in self.rows.get(kind, {}):
            raise GeoError('TEST_RECORD_NOT_FOUND', 'isolated test record absent', 404)
        return copy.deepcopy(self.rows[kind][ident])

    def list(self, kind):
        return copy.deepcopy(list(reversed(list(self.rows.get(kind, {}).values()))))


class AgriResearchServiceTest(unittest.TestCase):
    """Every raster, sample, model and review lives in a TemporaryDirectory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = IsolatedStore(self.root)
        self.service = AgriResearchServices(self.store)
        self.ident = 'isolated-landsat-2026'
        self.folder = self.service.root / self.ident
        self.folder.mkdir(parents=True)
        self.grid = from_origin(684000, 3379800, 30, 30)
        self.shape = (6, 8)
        self.days = ('2026-05-15', '2026-08-15')
        self.source = {'method':'field_survey',
                       'reference':'isolated test fixture, not actual ground observations',
                       'license':'unit-test-only', 'verified_by':'isolated contract'}
        observations = []
        for i, day in enumerate(self.days):
            value = np.full(self.shape, .25 + .3*i, dtype='float32')
            valid = np.ones(self.shape, dtype='float32')
            city = valid.copy()
            ndmi = np.full(self.shape, .35 + .3*i, dtype='float32')
            ndmi_valid = valid.copy()
            if i == 0:
                valid[0,0] = 0
                city[0,1] = 0
                ndmi_valid[1,0] = 0
                ndmi[1,1] = np.nan
                value[1,2] = -9999
                value[2,2] = 0
            name = 'observation-' + str(i + 1) + '.tif'
            self.write_raster(name, [value,valid,city,ndmi,ndmi_valid],
                              ('value','valid_mask','city_mask','ndmi','ndmi_valid'))
            observations.append({'scene_id':'isolated-scene-'+str(i+1), 'date':day,
                                 'native_file':name, 'native_sha256':self.file_hash(name),
                                 'ndvi_mean':.25+.3*i, 'common_mean':.3+.3*i})
        self.delta = np.zeros(self.shape, dtype='float32')
        self.delta[:2,:2] = -.5  # Four retained native pixels = 0.36 ha.
        self.delta[4,4] = -.9  # Separate one-pixel component, below min_pixels.
        self.delta[4,7] = .8  # Separate positive component.
        self.delta[3:5,:2] = -.6  # Outside common valid support.
        self.delta[:2,5:7] = -.7  # Outside the reference city mask.
        self.delta[5,0] = np.nan
        valid = np.ones(self.shape, dtype='float32'); valid[3:5,:2] = 0
        city = np.ones(self.shape, dtype='float32'); city[:2,5:7] = 0
        self.write_raster('change.tif', [self.delta,valid,city], ('value','valid_mask','city_mask'))
        self.raw = {'id':self.ident, 'status':'ready', 'title':'isolated native fixture',
                    'period':{'start':'2026-05-01','end':'2026-10-31'},
                    'scope':{'boundary_year':2017},
                    'sequence_summary':{'common_support_pixels':38},
                    'observations':observations,
                    'changes':[{'id':'change-1', 'native_file':'change.tif',
                                'native_sha256':self.file_hash('change.tif'),
                                'before_date':self.days[0], 'after_date':self.days[1],
                                'common_valid_ratio':.75}]}
        self.write_result()

    def tearDown(self):
        self.tmp.cleanup()

    def write_raster(self, name, bands, descriptions, crs='EPSG:32649', grid=None):
        with rasterio.open(self.folder/name, 'w', driver='GTiff', width=self.shape[1],
                           height=self.shape[0], count=len(bands), dtype='float32',
                           crs=crs, transform=grid or self.grid, nodata=-9999) as ds:
            for n, (values, description) in enumerate(zip(bands,descriptions), 1):
                ds.write(values.astype('float32'), n)
                ds.set_band_description(n, description)

    def file_hash(self, name):
        return hashlib.sha256((self.folder/name).read_bytes()).hexdigest()

    def write_result(self):
        (self.folder/'result.json').write_text(json.dumps(self.raw, allow_nan=False), encoding='utf-8')
        publication = self.folder/'publication.json'
        if not publication.exists():
            publication.write_text('{"observations":{},"changes":{}}', encoding='utf-8')

    def point(self, row=2, col=3, ident='point-1'):
        x, y = xy(self.grid, row, col)
        lon, lat = transform('EPSG:32649','EPSG:4326',[x],[y])
        return {'type':'Feature', 'id':ident,
                'geometry':{'type':'Point','coordinates':[lon[0],lat[0]]},
                'properties':{'label':'rice','crop_year':2026,'field_id':'isolated-field-'+ident,
                              'split':'train','features':{'untrusted_client_ndvi':999}}}

    def import_points(self, *points):
        return self.service.import_samples({'name':'isolated raster sampling contract',
                    'series_id':self.ident,'label_source':self.source,
                    'geojson':{'type':'FeatureCollection','features':list(points or [self.point()])}})

    def make_review(self, **overrides):
        return self.service.create_review({'series_id':self.ident, 'change_id':'change-1',
                'threshold':.4,'min_pixels':2,'direction':'both', **overrides})

    def test_series_publication_is_versioned_and_rejects_stale_reference(self):
        first = self.service.get(self.ident)
        self.assertEqual(first['summary']['common_support_pixel_count'],38)
        self.assertEqual(first['observations'][0]['mean_common_support'],.3)
        self.assertEqual(self.service.get(self.ident,first['series_sha256'])['id'],self.ident)
        (self.folder/'publication.json').write_text('{"observations":{},"changes":{},"revision":2}')
        with self.assertRaises(GeoError) as error:
            self.service.get(self.ident,first['series_sha256'])
        self.assertEqual(error.exception.code,'SERIES_VERSION_CHANGED')

    def test_artifact_hash_and_allowlist_protect_native_feature_sources(self):
        series = self.service.get(self.ident)
        path = self.service.artifact(self.ident,'observation-1.tif',series['series_sha256'])
        self.assertEqual(path,self.folder/'observation-1.tif')
        for name in ('../outside.tif','/etc/passwd','not-declared.tif'):
            with self.assertRaises(GeoError):self.service.artifact(self.ident,name)
        with path.open('ab') as handle:handle.write(b'test-only corruption')
        with self.assertRaises(GeoError) as error:
            self.service.artifact(self.ident,'observation-1.tif',series['series_sha256'])
        self.assertEqual(error.exception.code,'SERIES_CONTENT_CHANGED')

    def test_symlinked_artifact_cannot_leave_its_series(self):
        path = self.folder/'observation-1.tif'
        outside = self.root/'outside.tif';outside.write_bytes(path.read_bytes())
        path.unlink();path.symlink_to(outside)
        with self.assertRaises(GeoError):self.service.artifact(self.ident,'observation-1.tif')

    def test_publication_metadata_cannot_follow_a_symlink_outside_the_series(self):
        publication = self.folder/'publication.json'
        outside = self.root/'outside-publication.json'
        outside.write_text('{"external_marker":"must not be disclosed"}',encoding='utf-8')
        publication.unlink();publication.symlink_to(outside)
        with self.assertRaises(GeoError):self.service.get(self.ident)

    def test_import_extracts_dated_ndvi_ndmi_from_native_pixels_not_properties(self):
        result = self.import_points(self.point())
        sample = result['samples'][0]
        expected = {'ndvi_2026_05_15':.25,'ndmi_2026_05_15':.35,
                    'ndvi_2026_08_15':.55,'ndmi_2026_08_15':.65}
        self.assertEqual(set(sample['features']),set(expected))
        for key,value in expected.items():self.assertAlmostEqual(sample['features'][key],value,places=6)
        self.assertEqual(sample['feature_source_sha256'],result['feature_source_sha256'])
        self.assertEqual(len(result['feature_source_sha256']),64)
        self.assertEqual(result['feature_schema']['period'],self.raw['period'])
        self.assertEqual(result['feature_schema']['season'],'summer')
        self.assertEqual(result['status'],'needs_samples')
        self.assertIsNone(result['audit']['validation'])
        self.assertIn('incomplete_for_rapeseed',result['audit']['temporal_context']['notes'])
        self.assertEqual(result['provenance']['label_truth_status'],'provider_declared_not_platform_verified')

    def test_quality_city_nodata_and_nonfinite_samples_remain_null_and_zero_is_valid(self):
        result = self.import_points(*(self.point(r,c,'p-'+str(r)+'-'+str(c))
                    for r,c in [(0,0),(0,1),(1,0),(1,1),(1,2),(2,2)]))
        values = [row['features'] for row in result['samples']]
        for i in (0,1,4):self.assertIsNone(values[i]['ndvi_2026_05_15'])
        for i in (1,2,3):self.assertIsNone(values[i]['ndmi_2026_05_15'])
        self.assertAlmostEqual(values[0]['ndmi_2026_05_15'],.35,places=6)
        self.assertEqual(values[5]['ndvi_2026_05_15'],0)
        self.assertAlmostEqual(values[2]['ndvi_2026_05_15'],.25)
        json.dumps(result,allow_nan=False)

    def test_prediction_map_cannot_be_declared_as_independent_ground_reference(self):
        self.source['method']='model_prediction'
        with self.assertRaises(GeoError) as error:self.import_points(self.point())
        self.assertEqual(error.exception.code,'SAMPLE_SOURCE_REQUIRED')
        self.assertEqual(self.store.list('crop_sample_set'),[])

    def test_malformed_geojson_objects_raise_controlled_input_errors(self):
        for key in ('geometry','properties'):
            point = self.point();point[key] = ['unexpected non-object']
            with self.assertRaises(GeoError):self.import_points(point)
        self.assertEqual(self.store.list('crop_sample_set'),[])

    def test_sampling_cannot_declare_30m_landsat_from_an_incompatible_native_grid(self):
        bands = [np.full(self.shape,.4),np.ones(self.shape),np.ones(self.shape),
                 np.full(self.shape,.3),np.ones(self.shape)]
        descriptions = ('value','valid_mask','city_mask','ndmi','ndmi_valid')
        for crs,grid in [('EPSG:4326',self.grid),
                         ('EPSG:32649',from_origin(684000,3379800,15,15)),
                         ('EPSG:2263',self.grid)]:
            self.write_raster('observation-1.tif',bands,descriptions,crs=crs,grid=grid)
            self.raw['observations'][0]['native_sha256']=self.file_hash('observation-1.tif')
            self.write_result()
            with self.assertRaises(GeoError):self.import_points(self.point())
        self.assertEqual(self.store.list('crop_sample_set'),[])

    def test_insufficient_samples_never_publish_a_model_or_precision(self):
        sample_set = self.import_points(self.point())
        result = self.service.train({'sample_set_id':sample_set['id']})
        self.assertEqual(result['status'],'needs_samples')
        self.assertIsNone(result.get('model'));self.assertIsNone(result.get('validation'))

    def test_training_refuses_features_bound_to_a_revised_series(self):
        sample_set = self.import_points(self.point())
        (self.folder/'publication.json').write_text('{"revision":"isolated-new-version"}')
        with self.assertRaises(GeoError) as error:
            self.service.train({'sample_set_id':sample_set['id']})
        self.assertEqual(error.exception.code,'SERIES_VERSION_CHANGED')
        self.assertEqual(self.store.list('crop_model'),[])

    def test_spatially_adequate_unit_fixture_cannot_publish_without_winter_phenology(self):
        # These numerical fixtures are stored only in the temporary test store,
        # bypassing import deliberately to exercise the season publication gate.
        # They are not evidence of actual ground sample availability.
        series = self.service.get(self.ident);schema = self.service.feature_schema(series)
        names = [v['name'] for v in schema['features']];rows = []
        for split,count,offset in [('train',20,0),('validation',10,1)]:
            for cls,label in enumerate(('rice','rapeseed','other')):
                for i in range(count):
                    ident = split+'-'+label+'-'+str(i)
                    rows.append({'id':ident,'field_id':ident,'label':label,'split':split,'crop_year':2026,
                        'lon':113+offset+cls*.15+(i%5)*.025,'lat':30+(i//5)*.025,
                        'label_source':copy.deepcopy(self.source),'feature_source_sha256':'a'*64,
                        'features':{name:.1+cls*.3+i*.0001 for name in names}})
        self.store.save('crop_sample_set',{'id':'unit-season-gate','series_id':self.ident,
            'series_sha256':series['series_sha256'],'samples':rows,'feature_schema':schema,
            'provenance':{'rapeseed_season_complete':False}})
        result = self.service.train({'sample_set_id':'unit-season-gate'})
        self.assertEqual(result['status'],'needs_seasonal_data')
        self.assertIsNone(result.get('model'));self.assertIsNone(result.get('validation'))

    def test_change_uses_common_native_support_and_reports_removed_small_components(self):
        result = self.make_review()
        features = result['geojson']['features'];summary = result['summary']
        self.assertEqual(len(features),1)
        self.assertEqual(features[0]['properties']['pixel_count'],4)
        self.assertAlmostEqual(features[0]['properties']['area_ha'],.36,places=6)
        self.assertAlmostEqual(summary['area_sum_ha'],.36,places=6)
        self.assertEqual(summary['candidate_pixels_all'],4)
        self.assertEqual(summary['removed_small_pixels'],2)
        self.assertFalse(summary['truncated'])
        self.assertEqual(features[0]['geometry']['type'],'Polygon')
        lon,lat = features[0]['geometry']['coordinates'][0][0]
        self.assertTrue(-180<=lon<=180 and -90<=lat<=90)

    def test_review_direction_and_empty_results_do_not_relabel_missing_area(self):
        result = self.make_review(direction='increase',min_pixels=1)
        self.assertEqual(len(result['geojson']['features']),1)
        self.assertEqual(result['geojson']['features'][0]['properties']['direction'],'increase')
        self.assertAlmostEqual(result['summary']['area_sum_ha'],.09,places=6)
        empty = self.make_review(threshold=1.5)
        self.assertEqual(empty['geojson']['features'],[])
        self.assertEqual(empty['summary']['area_sum_ha'],0)

    def test_zero_common_support_is_rejected_instead_of_reported_as_no_change(self):
        self.write_raster('change.tif',[np.zeros(self.shape),np.zeros(self.shape),np.ones(self.shape)],
                          ('value','valid_mask','city_mask'))
        self.raw['changes'][0]['native_sha256']=self.file_hash('change.tif');self.write_result()
        with self.assertRaises(GeoError) as error:self.make_review()
        self.assertEqual(error.exception.status,422)
        self.assertEqual(error.exception.code,'CHANGE_NO_COMMON_SUPPORT')
        self.assertEqual(self.store.list('agri_review'),[])

    def test_review_history_is_append_only_and_never_mutates_source_geometry_or_raster(self):
        review = self.make_review();ident = review['id'];candidate = review['geojson']['features'][0]['id']
        before = copy.deepcopy(review['geojson']);source_hash = self.file_hash('change.tif')
        self.service.update_review(ident,{'candidate_id':candidate,'status':'verified','note':'isolated manual status fixture'})
        updated = self.service.update_review(ident,{'candidate_id':candidate,'status':'dismissed','note':'second isolated state'})
        frozen = self.store.get('agri_review',ident)
        self.assertEqual(frozen['geojson'],before)
        self.assertEqual(self.file_hash('change.tif'),source_hash)
        self.assertEqual([v['status'] for v in frozen['history']],['verified','dismissed'])
        self.assertEqual(updated['geojson']['features'][0]['properties']['review_status'],'dismissed')
        self.assertEqual(self.store.list('crop_sample_set'),[])

    def test_nonmetric_and_geographic_review_grids_are_rejected(self):
        for crs in ('EPSG:4326','EPSG:2263'):
            self.write_raster('change.tif',[self.delta,np.ones(self.shape),np.ones(self.shape)],
                              ('value','valid_mask','city_mask'),crs=crs)
            self.raw['changes'][0]['native_sha256']=self.file_hash('change.tif');self.write_result()
            with self.assertRaises(GeoError) as error:self.make_review()
            self.assertEqual(error.exception.code,'REVIEW_GRID_INVALID')

    def test_training_features_exclude_unusable_observation_dates_without_filling_clouds(self):
        for item in self.raw['observations']:item['comparison_eligible']=True
        cloud=copy.deepcopy(self.raw['observations'][-1]);cloud.update(date='2026-09-15',scene_id='isolated-cloud-date',comparison_eligible=False,ndvi_mean=None,ndmi_mean=None)
        self.raw['observations'].append(cloud)
        self.raw['quality_comparison']={'dates':list(self.days),'common_support_pixels':38}
        self.write_result()
        samples=self.import_points()
        expected={index+'_'+day.replace('-','_') for index in ('ndvi','ndmi') for day in self.days}
        self.assertEqual({feature['name'] for feature in samples['feature_schema']['features']},expected)
        self.assertEqual(set(samples['samples'][0]['features']),expected)
        self.assertEqual(samples['provenance']['excluded_observation_dates'],['2026-09-15'])
        self.assertFalse(samples['provenance']['missing_value_imputation'])


if __name__=='__main__':unittest.main()
