"""Isolated numerical/provenance contracts, never formal crop samples."""
import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.agriculture.crop_model import (CLASSES, DEFAULT_REQUIREMENTS, audit_samples, train_model,
                        train, predict, validate_feature_schema, validate_model,
                        save_model, load_model, temporal_context)


def schema():
    return {'schema_version':'crop-features-1.0', 'crop_year':2026,
            'features':[{'name':'spring_ndvi', 'index_id':'ndvi', 'start':'2026-04-01', 'end':'2026-04-30',
                         'aggregation':'median', 'source_id':'pc_landsat_c2_l2', 'resolution_m':30},
                        {'name':'summer_ndmi', 'index_id':'ndmi', 'start':'2026-08-01', 'end':'2026-08-31',
                         'aggregation':'median', 'source_id':'pc_landsat_c2_l2', 'resolution_m':30}]}


def samples():
    # Deterministic, clearly isolated unit fixtures. No formal sample service or
    # field-survey evidence is created by this helper.
    values = {'rice':(.2,.8), 'rapeseed':(.8,.15), 'other':(.1,.1)}
    result = []
    for split, count, offset in [('train',20,0),('validation',10,1)]:
        for c,label in enumerate(CLASSES):
            for i in range(count):
                lon = 113 + offset + c*.15 + (i % 5)*.025
                lat = 30 + (i//5)*.025
                first, second = values[label]
                result.append({'id':split+'-'+label+'-'+str(i), 'label':label,
                               'lon':lon, 'lat':lat, 'crop_year':2026,
                               'field_id':split+'-field-'+label+'-'+str(i), 'split':split,
                               'label_source':{'method':'field_survey', 'reference':'isolated unit fixture; not actual ground observations',
                                               'license':'unit-test-only', 'verified_by':'isolated contract'},
                               'feature_source_sha256':'a'*64,
                               'features':{'spring_ndvi':first+i*.0001, 'summer_ndmi':second-i*.0001}})
    return result


class CropModelContractTest(unittest.TestCase):
    def test_empty_samples_remain_unknown_without_fake_accuracy(self):
        result = train_model([],schema())
        self.assertEqual(result['status'],'needs_samples')
        self.assertIsNone(result['model']);self.assertIsNone(result['validation'])
        self.assertTrue(all(result['counts'][split][label] == 0 for split in ('train','validation') for label in CLASSES))
        self.assertEqual(result['requirements'],DEFAULT_REQUIREMENTS)

    def test_negative_class_and_explicit_validation_are_required(self):
        for selected in ([r for r in samples() if r['label']!='other'], [r for r in samples() if r['split']=='train']):
            result = train_model(selected,schema())
            self.assertEqual(result['status'],'needs_samples');self.assertIsNone(result['validation'])

    def test_weak_map_labels_are_not_independent_reference(self):
        rows = samples()
        for row in rows:
            row['label_source']['method'] = 'map_prediction'
        result = train_model(rows,schema())
        self.assertEqual(result['status'],'needs_samples')
        self.assertEqual(result['excluded_count'],90)
        self.assertTrue(all(row['reason']=='independent_label_provenance_required' for row in result['excluded']))

    def test_year_and_study_bbox_do_not_silently_transfer_labels(self):
        rows = samples();rows[0]['crop_year']=2021
        audit = audit_samples(rows,schema(),study_bbox=[112.9,29.9,113.9,30.5])
        self.assertEqual(audit['status'],'needs_samples')
        self.assertIn('wrong_crop_year',[r['reason'] for r in audit['excluded']])
        self.assertIn('outside_study_area',[r['reason'] for r in audit['excluded']])
        with self.assertRaises(ValueError):audit_samples(rows,schema(),crop_year=2025)

    def test_nearby_pixels_cannot_cross_the_validation_split(self):
        rows=samples();rows[-1]['lon']=rows[0]['lon'];rows[-1]['lat']=rows[0]['lat']
        audit=audit_samples(rows,schema())
        self.assertEqual(audit['status'],'needs_samples')
        self.assertIn('spatial_split_leakage',[r['code'] for r in audit['issues']])

    def test_same_field_cannot_cross_split_even_when_coordinates_differ(self):
        rows=samples();rows[-1]['field_id']=rows[0]['field_id']
        result=train_model(rows,schema())
        self.assertEqual(result['status'],'needs_samples')
        self.assertIn('field_split_leakage',[r['code'] for r in result['issues']])

    def test_missing_features_are_excluded_without_zero_fill(self):
        rows=samples();rows[0]['features']['spring_ndvi']=None
        audit=audit_samples(rows,schema())
        self.assertEqual(audit['excluded'][0]['reason'],'missing_or_invalid_features')
        self.assertEqual(audit['counts']['train']['rice'],19)
        self.assertEqual(audit['status'],'needs_samples')

    def test_source_version_and_feature_names_are_required(self):
        for mutate in [lambda r:r.pop('feature_source_sha256'),lambda r:r['features'].update(extra=.5)]:
            rows=samples();mutate(rows[0]);audit=audit_samples(rows,schema())
            self.assertEqual(audit['excluded_count'],1)
            self.assertEqual(audit['status'],'needs_samples')

    def test_malformed_identifiers_are_excluded_and_unaudited_metadata_is_dropped(self):
        rows=samples();rows[0]['id']={'unexpected':'object'}
        audit=audit_samples(rows,schema())
        self.assertEqual(audit['status'],'needs_samples')
        self.assertEqual(audit['excluded'][0]['reason'],'missing_or_duplicate_id')
        rows=samples();rows[0]['ignored_metadata']={'not_model_input':float('nan')}
        rows[0]['features']['spring_ndvi']=np.float64(.2)
        result=train_model(rows,schema())
        self.assertEqual(result['status'],'trained_baseline')
        self.assertNotIn('ignored_metadata',result['sample_audit'])

    def test_metrics_come_from_held_out_samples_and_train_scaling_only(self):
        rows=samples();before=copy.deepcopy(rows);result=train_model(rows,schema())
        self.assertEqual(result['status'],'trained_baseline')
        self.assertEqual(rows,before)
        validation=result['validation'];model=result['model']
        self.assertEqual(validation['sample_count'],30)
        self.assertEqual(validation['confusion_matrix'],[[10,0,0],[0,10,0],[0,0,10]])
        self.assertEqual(validation['accuracy'],1)
        self.assertLess(validation['accuracy_wilson95'][0],1)
        self.assertEqual([r['support'] for r in validation['per_class']],[10,10,10])
        self.assertFalse(set(model['training_groups']) & set(model['validation_groups']))
        self.assertTrue(model['provenance_checked']);self.assertFalse(model['probability_calibrated'])
        self.assertAlmostEqual(model['core']['mean'][0],sum(r['features']['spring_ndvi'] for r in rows if r['split']=='train')/60)

    def test_validation_changes_do_not_train_the_classifier(self):
        first=train_model(samples(),schema())
        changed=samples()
        for row in changed:
            if row['split']=='validation':
                row['features']={'spring_ndvi':-5,'summer_ndmi':-5}
        second=train_model(changed,schema())
        self.assertEqual(first['model']['core'],second['model']['core'])
        self.assertNotEqual(first['validation']['confusion_matrix'],second['validation']['confusion_matrix'])

    def test_predict_preserves_missing_and_valid_zero(self):
        model=train_model(samples(),schema())['model']
        result=predict(model,[[0,0],[None,.4],[.2,.8]],feature_schema=schema())
        self.assertIsNotNone(result['labels'][0]);self.assertIsNone(result['labels'][1])
        self.assertIsNone(result['posterior_scores'][1]);self.assertEqual(result['unknown_count'],1)
        self.assertEqual(result['labels'][2],'rice')
        self.assertAlmostEqual(sum(result['posterior_scores'][2]),1)

    def test_year_order_and_sensor_contract_cannot_change_at_inference(self):
        model=train_model(samples(),schema())['model']
        changed=schema();changed['features'].reverse()
        with self.assertRaises(ValueError):predict(model,[[.2,.8]],feature_schema=changed)
        changed=schema();changed['crop_year']=2027
        with self.assertRaises(ValueError):predict(model,[[.2,.8]],feature_schema=changed)

    def test_model_tamper_and_pickle_free_roundtrip(self):
        model=train_model(samples(),schema())['model'];changed=copy.deepcopy(model);changed['core']['mean'][0]+=.01
        with self.assertRaises(ValueError):validate_model(changed)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model.json';save_model(model,path)
            self.assertEqual(load_model(path),model)
            with self.assertRaises(FileExistsError):save_model(model,path)

    def test_bottom_level_group_holdout_guard(self):
        rows=samples();names=['spring_ndvi','summer_ndmi'];features=[[r['features'][n] for n in names] for r in rows]
        result=train(features,[r['label'] for r in rows],['same-field']*90,[r['split']=='validation' for r in rows],feature_schema=schema())
        self.assertEqual(result['status'],'needs_samples');self.assertIsNone(result['model'])

    def test_feature_contract_rejects_implicit_or_incompatible_inputs(self):
        changed=schema();changed['features'][0]['resolution_m']=10
        with self.assertRaises(ValueError):validate_feature_schema(changed)
        changed=schema();changed['features'][0]['index_id']='ndre'
        with self.assertRaises(ValueError):validate_feature_schema(changed)
        changed=schema();changed['features'][1].update(start='2026-04-01',end='2026-04-30')
        with self.assertRaises(ValueError):validate_feature_schema(changed)

    def test_duplicate_sites_and_ids_do_not_inflate_independent_sample_count(self):
        for key in ('id','position'):
            rows=samples()
            if key=='id':rows[1]['id']=rows[0]['id']
            else:rows[1].update(lon=rows[0]['lon'],lat=rows[0]['lat'])
            audit=audit_samples(rows,schema())
            self.assertEqual(audit['status'],'needs_samples')
            self.assertTrue(any(v['code'].startswith('duplicate_sample_') for v in audit['issues']))

    def test_array_interface_rejects_boolean_and_text_features(self):
        model=train_model(samples(),schema())['model']
        for rows in ([[True,.1]],[['0.1',.2]]):
            with self.assertRaises(ValueError):predict(model,rows,feature_schema=schema())

    def test_actual_scene_metadata_never_claims_complete_rapeseed_season(self):
        changed=schema();changed.update(period={'start':'2026-05-01','end':'2026-10-31'},season='summer')
        changed['features'][0].update(start='2026-05-08',end='2026-05-08',aggregation='representative_scene')
        changed['features'][1].update(start='2026-08-12',end='2026-08-12',aggregation='single_observation')
        self.assertEqual(validate_feature_schema(changed),changed)
        report=train_model([],changed)
        self.assertEqual(report['status'],'needs_samples')
        self.assertIn('incomplete_for_rapeseed',report['temporal_context']['notes'])
        self.assertFalse(report['temporal_context']['rapeseed_full_season_verified'])
        changed['season']='full_crop_year'
        self.assertFalse(temporal_context(changed)['rapeseed_full_season_verified'])
        self.assertIn('incomplete_for_rapeseed',temporal_context(changed)['notes'])

    def test_explicit_period_bounds_are_enforced(self):
        for period in ({'start':'2026-05-01','end':'2026-10-31'},
                       {'start':'2026-09-01','end':'2026-08-31'},
                       {'start':'2026-01-01','end':'2027-01-01'}):
            changed=schema();changed['period']=period
            with self.assertRaises(ValueError):validate_feature_schema(changed)


if __name__=='__main__':unittest.main()
