"""Supervised crop baseline with explicit labels and held-out spatial evidence.

The module does not obtain labels from a crop map or make a classifier ready by
inventing samples. All public interfaces return needs_samples before training
when class, provenance, temporal or split requirements are not satisfied.
"""
import copy
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

VERSION = 'crop-supervised-lda-1.0'
FEATURE_VERSION = 'crop-features-1.0'
CLASSES = ('rice', 'rapeseed', 'other')
CLASS_NAMES = {'rice': '水稻', 'rapeseed': '油菜', 'other': '其他'}
LABEL_METHODS = ('field_survey', 'independent_photo_interpretation')
DEFAULT_REQUIREMENTS = {'min_train_per_class': 20, 'min_validation_per_class': 10,
                        'min_train_groups': 3, 'min_validation_groups': 2,
                        'spatial_block_degrees': .02}
LIMITATIONS = [
    '监督分类基线只适用于所声明年度、来源和特征，不是法定种植面积认定。',
    '验证指标仅来自显式留出的独立空间样本，不是对全部像元或未来年份的精度保证。',
    '模型后验分数未经概率校准，不能作为识别精度或置信区间。',
    '样本与影像缺测保持未知，不以0补值；公开分类预测不能作为独立地面真值。',
]


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float, np.integer, np.floating)) and math.isfinite(value)


def _text(value, limit=1000):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def validate_feature_schema(value):
    """Return a closed, ordered, dated feature contract or raise ValueError."""
    required = {'schema_version', 'crop_year', 'features'}
    if not isinstance(value, dict) or not required.issubset(value) or set(value) - required - {'period', 'season'}:
        raise ValueError('feature schema requires schema_version/crop_year/features')
    if value['schema_version'] != FEATURE_VERSION:
        raise ValueError('unsupported feature schema')
    year = value['crop_year']
    if isinstance(year, bool) or not isinstance(year, int) or not 1980 <= year <= 2100:
        raise ValueError('invalid crop year')
    period = value.get('period')
    if period is not None:
        if not isinstance(period, dict) or set(period) != {'start', 'end'}:
            raise ValueError('period requires explicit start/end dates')
        try:
            period_start, period_end = date.fromisoformat(period['start']), date.fromisoformat(period['end'])
        except (TypeError, ValueError):
            raise ValueError('period dates must be ISO dates')
        if period_start > period_end or period_start.year < year - 1 or period_end.year > year:
            raise ValueError('period is outside the crop year')
    if 'season' in value and value['season'] not in ('summer', 'winter_spring', 'full_crop_year', 'custom'):
        raise ValueError('unsupported declared season')
    features = value['features']
    if not isinstance(features, list) or not 2 <= len(features) <= 72:
        raise ValueError('provide 2 to 72 explicitly dated features')
    names = set()
    for item in features:
        if not isinstance(item, dict) or set(item) != {'name', 'index_id', 'start', 'end', 'aggregation', 'source_id', 'resolution_m'}:
            raise ValueError('unknown or missing feature declaration')
        name = item['name']
        if not _text(name, 100) or name in names:
            raise ValueError('feature names must be unique')
        names.add(name)
        if item['index_id'] not in ('ndvi', 'ndmi', 'mndwi', 'evi2', 'savi', 'bsi', 'ndre'):
            raise ValueError('unsupported crop feature index')
        try:
            start, end = date.fromisoformat(item['start']), date.fromisoformat(item['end'])
        except (TypeError, ValueError):
            raise ValueError('feature dates must be ISO dates')
        if start > end or (end - start).days > 92 or start.year < year - 1 or end.year > year:
            raise ValueError('feature window is outside the declared crop season')
        if period is not None and (start < period_start or end > period_end):
            raise ValueError('feature window is outside declared period')
        if item['aggregation'] not in ('single_observation', 'representative_scene', 'median', 'mean'):
            raise ValueError('feature aggregation must be explicit')
        if item['source_id'] not in ('pc_sentinel2_l2a', 'pc_landsat_c2_l2'):
            raise ValueError('unsupported feature source')
        if not _finite(item['resolution_m']) or item['resolution_m'] not in (10, 20, 30):
            raise ValueError('feature resolution must be explicit')
        if item['source_id'] == 'pc_landsat_c2_l2' and item['resolution_m'] != 30:
            raise ValueError('Landsat feature must retain its 30 metre support')
        if item['source_id'] == 'pc_landsat_c2_l2' and item['index_id'] == 'ndre':
            raise ValueError('Landsat has no declared red-edge feature')
    if len({(v['start'], v['end']) for v in features}) < 2:
        raise ValueError('crop features require at least two distinct time windows')
    return copy.deepcopy(value)


def temporal_context(feature_schema):
    """Describe observation support, never infer a complete crop season."""
    schema = validate_feature_schema(feature_schema)
    start = min(item['start'] for item in schema['features'])
    end = max(item['end'] for item in schema['features'])
    summer_only = start >= str(schema['crop_year']) + '-05-01' and end <= str(schema['crop_year']) + '-10-31'
    notes = []
    if summer_only or schema.get('season') == 'summer':
        notes.append('incomplete_for_rapeseed')
    # A date range, or merely declaring full_crop_year, is insufficient to
    # prove cloud-free observations cover all required phenological phases.
    return {'observation_start':start, 'observation_end':end,
            'declared_period':copy.deepcopy(schema.get('period')),
            'declared_season':schema.get('season', 'unspecified'),
            'rapeseed_full_season_verified':False, 'notes':notes,
            'limitation':'时间范围和夏季观测不能证明油菜完整越冬及开花物候；发布三类作物模型前需另核验季节覆盖。'}


def _requirements(overrides):
    values = {**DEFAULT_REQUIREMENTS, **overrides}
    if set(values) != set(DEFAULT_REQUIREMENTS):
        raise ValueError('unknown sample requirement')
    for key in ('min_train_per_class', 'min_validation_per_class', 'min_train_groups', 'min_validation_groups'):
        if isinstance(values[key], bool) or not isinstance(values[key], int) or not 2 <= values[key] <= 10000:
            raise ValueError('invalid minimum sample requirement')
    if not _finite(values['spatial_block_degrees']) or not .001 <= values['spatial_block_degrees'] <= 1:
        raise ValueError('invalid spatial block size')
    return values


def _bbox(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4 or not all(_finite(v) for v in value):
        raise ValueError('invalid study bbox')
    w, s, e, n = value
    if not -180 <= w < e <= 180 or not -80 <= s < n <= 80:
        raise ValueError('invalid study bbox')
    return list(value)


def audit_samples(samples, feature_schema, *, study_bbox=None, crop_year=None, **requirements):
    """Audit real labeled records without fitting, mutating, or filling gaps.

    One field and one 0.02-degree block may only belong to one split. Explicit
    splits are mandatory; the service never randomly divides pixels of a field.
    """
    schema = validate_feature_schema(feature_schema)
    req = _requirements(requirements)
    bounds = _bbox(study_bbox)
    year = schema['crop_year'] if crop_year is None else crop_year
    if year != schema['crop_year']:
        raise ValueError('sample and feature crop years differ')
    if not isinstance(samples, list) or len(samples) > 100000:
        raise ValueError('samples must be a bounded list')
    names = [f['name'] for f in schema['features']]
    valid, excluded, issues = [], [], []
    seen = set()
    positions = {}
    fields, blocks = {}, {}
    for sample in samples:
        ident = sample.get('id') if isinstance(sample, dict) else None
        reason = None
        if not isinstance(sample, dict):
            reason = 'invalid_record'
        elif not _text(ident, 120) or ident in seen:
            reason = 'missing_or_duplicate_id'
        elif sample.get('label') not in CLASSES:
            reason = 'unknown_class'
        elif sample.get('split') not in ('train', 'validation'):
            reason = 'explicit_split_required'
        elif sample.get('crop_year') != year:
            reason = 'wrong_crop_year'
        elif not _finite(sample.get('lon')) or not _finite(sample.get('lat')) or not -180 <= sample['lon'] <= 180 or not -80 <= sample['lat'] <= 80:
            reason = 'invalid_coordinates'
        elif bounds and not (bounds[0] <= sample['lon'] <= bounds[2] and bounds[1] <= sample['lat'] <= bounds[3]):
            reason = 'outside_study_area'
        elif not _text(sample.get('field_id'), 120):
            reason = 'field_id_required'
        else:
            source = sample.get('label_source')
            if not isinstance(source, dict) or source.get('method') not in LABEL_METHODS or not all(_text(source.get(k), 2000) for k in ('reference', 'license', 'verified_by')):
                reason = 'independent_label_provenance_required'
            elif not isinstance(sample.get('feature_source_sha256'), str) or len(sample['feature_source_sha256']) != 64 or any(c not in 'abcdef0123456789' for c in sample['feature_source_sha256']):
                reason = 'feature_source_version_required'
            elif not isinstance(sample.get('features'), dict) or set(sample['features']) != set(names):
                reason = 'feature_schema_mismatch'
            elif not all(_finite(sample['features'][key]) for key in names):
                reason = 'missing_or_invalid_features'
        if isinstance(ident, str):
            if ident in seen:
                issues.append({'code':'duplicate_sample_id', 'id':ident})
            seen.add(ident)
        if reason:
            excluded.append({'id':ident, 'reason':reason})
            continue
        block = (math.floor(sample['lon'] / req['spatial_block_degrees']), math.floor(sample['lat'] / req['spatial_block_degrees']))
        block_key = str(block[0]) + ':' + str(block[1])
        split = sample['split']
        field_key = str(sample['field_id'])
        position = (round(float(sample['lon']),8), round(float(sample['lat']),8))
        if position in positions:
            # Repeated coordinates are not independent evidence even if a
            # caller assigns different field IDs or a different crop label.
            issues.append({'code':'duplicate_sample_location', 'id':ident})
        positions[position] = ident
        for mapping, key, code in ((fields, field_key, 'field_split_leakage'), (blocks, block_key, 'spatial_split_leakage')):
            if key in mapping and mapping[key] != split:
                issues.append({'code':code, 'id':ident, 'group':key})
            mapping[key] = split
        # Persist only the audited contract, normalizing numpy scalars and
        # dropping unrelated client metadata from the reproducibility hash.
        row = {key:sample[key] for key in ('id','label','crop_year','field_id','split','feature_source_sha256')}
        row.update(lon=float(sample['lon']), lat=float(sample['lat']),
                   label_source={key:sample['label_source'][key] for key in ('method','reference','license','verified_by')},
                   features={key:float(sample['features'][key]) for key in names})
        row['spatial_group'] = block_key
        valid.append(row)
    counts = {split:{label:0 for label in CLASSES} for split in ('train', 'validation')}
    group_sets = {split:{label:set() for label in CLASSES} for split in counts}
    for row in valid:
        counts[row['split']][row['label']] += 1
        group_sets[row['split']][row['label']].add(row['spatial_group'])
    group_counts = {split:{label:len(values) for label,values in labels.items()} for split,labels in group_sets.items()}
    for split in counts:
        minimum = req['min_train_per_class'] if split == 'train' else req['min_validation_per_class']
        min_groups = req['min_train_groups'] if split == 'train' else req['min_validation_groups']
        for label in CLASSES:
            if counts[split][label] < minimum:
                issues.append({'code':'insufficient_class_samples', 'split':split, 'label':label, 'available':counts[split][label], 'required':minimum})
            if group_counts[split][label] < min_groups:
                issues.append({'code':'insufficient_spatial_groups', 'split':split, 'label':label, 'available':group_counts[split][label], 'required':min_groups})
    return {'status':'ready_to_train' if not issues else 'needs_samples',
            'requirements':req, 'crop_year':year, 'study_bbox':bounds,
            'feature_schema':schema, 'feature_schema_sha256':_sha(schema),
            'temporal_context':temporal_context(schema),
            'counts':counts, 'spatial_group_counts':group_counts,
            'accepted_count':len(valid), 'excluded_count':len(excluded),
            'excluded':excluded, 'issues':issues, 'records':valid,
            'validation':None, 'limitations':list(LIMITATIONS)}


def _matrix(features):
    if not isinstance(features, (list, tuple, np.ndarray)):
        raise ValueError('feature matrix required')
    raw = np.asarray(features, dtype=object)
    if any(isinstance(value, (bool, np.bool_, str)) for value in raw.flat):
        raise ValueError('features must be numeric values or explicit missing values')
    try:
        values = np.asarray(features, dtype=np.float64)
    except (TypeError, ValueError):
        raise ValueError('invalid feature matrix')
    if values.ndim != 2 or not 1 <= values.shape[0] <= 100000 or not 2 <= values.shape[1] <= 72:
        raise ValueError('invalid feature matrix shape')
    return values


def _scores(core, values):
    z = (values - np.asarray(core['mean'])) / np.asarray(core['scale'])
    means = np.asarray(core['class_means'])
    precision = np.asarray(core['precision'])
    return z @ precision @ means.T - .5 * np.einsum('ij,jk,ik->i', means, precision, means) + np.log(np.asarray(core['priors']))


def _fit(values, labels, shrinkage):
    mean = values.mean(axis=0)
    scale = values.std(axis=0)
    scale[scale < 1e-12] = 1.
    z = (values - mean) / scale
    centers = np.array([z[labels == label].mean(axis=0) for label in CLASSES])
    residuals = np.concatenate([z[labels == label] - centers[i] for i,label in enumerate(CLASSES)])
    covariance = residuals.T @ residuals / max(1, len(z) - len(CLASSES))
    variance = float(np.trace(covariance) / covariance.shape[0])
    covariance = (1 - shrinkage) * covariance + shrinkage * max(variance, 1e-6) * np.eye(covariance.shape[0])
    precision = np.linalg.pinv(covariance, rcond=1e-10)
    return {'mean':mean.tolist(), 'scale':scale.tolist(), 'class_means':centers.tolist(),
            'precision':precision.tolist(), 'priors':[1 / len(CLASSES)] * len(CLASSES)}


def _validation(truth, prediction):
    matrix = np.zeros((len(CLASSES), len(CLASSES)), dtype='int64')
    for actual, estimated in zip(truth, prediction):
        matrix[CLASSES.index(str(actual)), CLASSES.index(str(estimated))] += 1
    per_class = []
    for i,label in enumerate(CLASSES):
        tp, support, estimated = int(matrix[i,i]), int(matrix[i,:].sum()), int(matrix[:,i].sum())
        precision = tp / estimated if estimated else None
        recall = tp / support if support else None
        f1 = 2 * tp / (support + estimated) if support + estimated else None
        per_class.append({'label':label, 'name':CLASS_NAMES[label], 'support':support,
                          'predicted':estimated, 'precision':precision, 'recall':recall, 'f1':f1})
    n, correct = int(matrix.sum()), int(np.trace(matrix))
    accuracy = correct / n if n else None
    interval = None
    if n:
        z = 1.959963984540054
        denominator = 1 + z*z/n
        center = (accuracy + z*z/(2*n)) / denominator
        half = z * math.sqrt(accuracy*(1-accuracy)/n + z*z/(4*n*n)) / denominator
        interval = [max(0., center-half), min(1., center+half)]
    return {'kind':'held_out_spatial_validation', 'class_order':list(CLASSES),
            'confusion_matrix':matrix.tolist(), 'matrix_rows':'reference', 'matrix_columns':'prediction',
            'sample_count':n, 'correct_count':correct, 'accuracy':accuracy,
            'accuracy_wilson95':interval,
            'macro_f1':sum(v['f1'] for v in per_class if v['f1'] is not None) / len(per_class),
            'per_class':per_class, 'probability_calibrated':False}


def train(features, labels, groups, validation_mask, *, feature_schema,
          min_train_per_class=20, min_validation_per_class=10,
          min_train_groups=3, min_validation_groups=2, shrinkage=.2):
    """Fit a baseline with explicit grouped holdout; no implicit random split.

    The array interface assumes the caller already validated label provenance
    and coordinates. Use train_model for the full record/provenance audit.
    """
    schema = validate_feature_schema(feature_schema)
    values = _matrix(features)
    if values.shape[1] != len(schema['features']):
        raise ValueError('feature dimension differs from schema')
    labels, groups, mask = list(labels), list(groups), list(validation_mask)
    if len(labels) != len(values) or len(groups) != len(values) or len(mask) != len(values):
        raise ValueError('training array lengths differ')
    if any(label not in CLASSES for label in labels) or any(not _text(group, 150) for group in groups) or any(not isinstance(v, (bool, np.bool_)) for v in mask):
        raise ValueError('invalid labels, groups or explicit holdout')
    if not _finite(shrinkage) or not 0 < shrinkage <= 1:
        raise ValueError('shrinkage must be in (0,1]')
    req = _requirements({'min_train_per_class':min_train_per_class, 'min_validation_per_class':min_validation_per_class,
                         'min_train_groups':min_train_groups, 'min_validation_groups':min_validation_groups})
    labels = np.asarray(labels, dtype=object)
    groups = np.asarray(groups, dtype=object)
    mask = np.asarray(mask, dtype=bool)
    complete = np.isfinite(values).all(axis=1)
    excluded = np.flatnonzero(~complete).tolist()
    values, labels, groups, mask = values[complete], labels[complete], groups[complete], mask[complete]
    issues = []
    overlap = sorted(set(groups[~mask]) & set(groups[mask]))
    if overlap:
        issues.append({'code':'spatial_split_leakage', 'groups':overlap})
    counts = {split:{label:int(((labels == label) & selected).sum()) for label in CLASSES}
              for split,selected in [('train',~mask),('validation',mask)]}
    for split, selected in [('train',~mask),('validation',mask)]:
        minimum = req['min_train_per_class'] if split == 'train' else req['min_validation_per_class']
        group_minimum = req['min_train_groups'] if split == 'train' else req['min_validation_groups']
        for label in CLASSES:
            if counts[split][label] < minimum:
                issues.append({'code':'insufficient_class_samples', 'split':split, 'label':label, 'available':counts[split][label], 'required':minimum})
            count_groups = len(set(groups[selected & (labels == label)]))
            if count_groups < group_minimum:
                issues.append({'code':'insufficient_spatial_groups', 'split':split, 'label':label, 'available':count_groups, 'required':group_minimum})
    result = {'status':'needs_samples', 'model':None, 'validation':None, 'counts':counts,
              'excluded_feature_rows':excluded, 'issues':issues, 'limitations':list(LIMITATIONS),
              'temporal_context':temporal_context(schema)}
    if issues:
        return result
    core = _fit(values[~mask], labels[~mask], float(shrinkage))
    prediction = np.asarray(CLASSES, dtype=object)[np.argmax(_scores(core, values[mask]), axis=1)]
    validation = _validation(labels[mask], prediction)
    model = {'schema_version':VERSION, 'algorithm':'regularized_linear_discriminant', 'classes':list(CLASSES),
             'crop_year':schema['crop_year'], 'feature_schema':schema, 'feature_schema_sha256':_sha(schema),
             'temporal_context':temporal_context(schema),
             'core':core, 'shrinkage':float(shrinkage),
             'training_sample_count':int((~mask).sum()), 'validation_sample_count':int(mask.sum()),
             'training_groups':sorted(set(groups[~mask])), 'validation_groups':sorted(set(groups[mask])),
             'validation':validation, 'probability_calibrated':False,
             'provenance_checked':False, 'limitations':list(LIMITATIONS),
             'created_at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
    model['model_sha256'] = _sha(model)
    result.update(status='trained_baseline', model=model, validation=validation)
    return result


def train_model(samples, feature_schema, *, study_bbox=None, crop_year=None, shrinkage=.2, **requirements):
    audit = audit_samples(samples, feature_schema, study_bbox=study_bbox, crop_year=crop_year, **requirements)
    if audit['status'] != 'ready_to_train':
        return {**audit, 'model':None, 'validation':None}
    rows = audit['records']; names = [v['name'] for v in audit['feature_schema']['features']]
    result = train([[r['features'][key] for key in names] for r in rows], [r['label'] for r in rows],
                   [r['spatial_group'] for r in rows], [r['split'] == 'validation' for r in rows],
                   feature_schema=audit['feature_schema'], shrinkage=shrinkage,
                   **{key:value for key,value in audit['requirements'].items() if key != 'spatial_block_degrees'})
    result['sample_audit'] = {key:value for key,value in audit.items() if key != 'records'}
    if result['model']:
        model = result['model']
        model.update(provenance_checked=True, study_bbox=audit['study_bbox'],
                     label_methods=sorted({r['label_source']['method'] for r in rows}),
                     sample_manifest_sha256=_sha(sorted(rows, key=lambda r:r['id'])),
                     sources=[dict(values) for values in sorted({tuple((key,r['label_source'][key]) for key in ('method','reference','license','verified_by')) for r in rows})])
        model.pop('model_sha256')
        model['model_sha256'] = _sha(model)
    return result


def validate_model(model):
    if not isinstance(model, dict) or model.get('schema_version') != VERSION or model.get('classes') != list(CLASSES):
        raise ValueError('unsupported model artifact')
    copy_model = copy.deepcopy(model)
    fingerprint = copy_model.pop('model_sha256', None)
    if not isinstance(fingerprint, str) or _sha(copy_model) != fingerprint:
        raise ValueError('model artifact content/version mismatch')
    schema = validate_feature_schema(model['feature_schema'])
    if model.get('feature_schema_sha256') != _sha(schema) or model.get('crop_year') != schema['crop_year']:
        raise ValueError('model feature contract mismatch')
    core = model.get('core') or {}; dimension = len(schema['features'])
    expected = {'mean':(dimension,), 'scale':(dimension,), 'class_means':(3,dimension), 'precision':(dimension,dimension), 'priors':(3,)}
    if set(core) != set(expected):
        raise ValueError('model core fields invalid')
    for key, shape in expected.items():
        value = np.asarray(core[key], dtype=float)
        if value.shape != shape or not np.isfinite(value).all():
            raise ValueError('model core shape/values invalid')
    if any(v <= 0 for v in core['scale']) or any(v <= 0 for v in core['priors']) or abs(sum(core['priors']) - 1) > 1e-10:
        raise ValueError('model scaling/priors invalid')
    return model


def predict(model, feature_rows, *, feature_schema):
    """Classify only complete compatible feature rows, preserving unknowns."""
    validate_model(model)
    schema = validate_feature_schema(feature_schema)
    if _sha(schema) != model['feature_schema_sha256']:
        raise ValueError('inference feature source/year/order differs from training')
    values = _matrix(feature_rows)
    if values.shape[1] != len(schema['features']):
        raise ValueError('inference feature dimensions differ')
    complete = np.isfinite(values).all(axis=1)
    labels, scores = [None] * len(values), [None] * len(values)
    if complete.any():
        raw = _scores(model['core'], values[complete]); raw -= raw.max(axis=1, keepdims=True)
        probability = np.exp(raw); probability /= probability.sum(axis=1, keepdims=True)
        for row, p in zip(np.flatnonzero(complete), probability):
            labels[int(row)] = CLASSES[int(np.argmax(p))]
            scores[int(row)] = p.tolist()
    return {'model_sha256':model['model_sha256'], 'class_order':list(CLASSES), 'labels':labels,
            'posterior_scores':scores, 'unknown_count':int((~complete).sum()),
            'unknown_reason':'missing_feature_values', 'probability_calibrated':False}


def save_model(model, path):
    validate_model(model)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as output:
        output.write(_canonical(model) + b'\n')
    return str(path)


def load_model(path):
    path = Path(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('model artifact is too large')
    value = json.loads(path.read_text(encoding='utf-8'))
    return validate_model(value)


def capabilities():
    """Machine-readable requirements; this does not claim a model is trained."""
    return {'version':VERSION, 'algorithm':'regularized_linear_discriminant',
            'classes':[{'id':value,'name':CLASS_NAMES[value]} for value in CLASSES],
            'feature_schema_version':FEATURE_VERSION,
            'feature_aggregations':['single_observation','representative_scene','median','mean'],
            'optional_feature_schema_fields':['period','season'],
            'required_sample_fields':['id','label','lon','lat','crop_year','field_id','split','label_source','features','feature_source_sha256'],
            'accepted_label_methods':list(LABEL_METHODS), 'requirements':dict(DEFAULT_REQUIREMENTS),
            'validation':'explicit held-out spatial groups and fields; no random pixel split',
            'probability_calibrated':False, 'limitations':list(LIMITATIONS)}
