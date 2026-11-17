"""Public catalog retry remains bounded and never changes provider or product."""
import io
import json
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError
from backend.core.geo_services import GeoError
from backend.analysis.io_lulc_reader import fetch_catalog

class CatalogTransportTest(unittest.TestCase):
    def test_retryable_provider_failure_retries_same_url_once(self):
        opener=Mock();opener.open.side_effect=[HTTPError('https://api.impactobservatory.com',503,'temporary',{},None),io.BytesIO(json.dumps({'features':[]}).encode())]
        with patch('backend.analysis.io_lulc_reader.build_opener',return_value=opener),patch('backend.analysis.io_lulc_reader.time.sleep'):
            result=fetch_catalog([113.08,30.43,113.09,30.44])
        self.assertEqual(result['_geoai_transport']['attempts'],2);self.assertEqual(opener.open.call_count,2)
        self.assertEqual(opener.open.call_args_list[0].args[0].full_url,opener.open.call_args_list[1].args[0].full_url)
    def test_permanent_or_repeated_failure_is_not_replaced_with_data(self):
        for status,expected in ((403,1),(503,2)):
            with self.subTest(status=status):
                opener=Mock();opener.open.side_effect=HTTPError('https://api.impactobservatory.com',status,'failure',{},None)
                with patch('backend.analysis.io_lulc_reader.build_opener',return_value=opener),patch('backend.analysis.io_lulc_reader.time.sleep'),self.assertRaises(GeoError) as error:
                    fetch_catalog([113.08,30.43,113.09,30.44])
                self.assertEqual(opener.open.call_count,expected);self.assertIn('HTTP '+str(status),error.exception.message)
