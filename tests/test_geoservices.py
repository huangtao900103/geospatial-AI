"""Deterministic V3 algorithm, import, provenance and public-adapter checks."""
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geo_services import (GeoError, PublicSources, FixedRedirect, EARTH_RADIUS_M, analyse,
                          validate_geojson, digest, polygon_area_m2, haversine)
from server import Workspace, LocalServer, make_handler


def feature(geometry, properties=None, ident="a"):
    return {"type": "Feature", "id": ident, "geometry": geometry, "properties": properties or {}}


def point(x, y, props=None, ident="a"):
    return feature({"type": "Point", "coordinates": [x, y]}, props, ident)


def ring(x1=113, y1=30, x2=113.01, y2=30.01):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]


def collection(features):
    return {"type": "FeatureCollection", "features": features}


WEATHER = {"latitude": 30.33, "longitude": 113.42, "timezone": "Asia/Shanghai",
           "current": {"time": "2026-11-12T12:00", "temperature_2m": 18, "precipitation": .2, "wind_speed_10m": 3},
           "current_units": {"temperature_2m": "°C", "precipitation": "mm", "wind_speed_10m": "m/s"},
           "daily": {"time": ["2026-11-12"], "temperature_2m_max": [20], "temperature_2m_min": [10], "precipitation_sum": [1]},
           "daily_units": {"temperature_2m_max": "°C", "temperature_2m_min": "°C", "precipitation_sum": "mm"}}
STAC = {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "S2-test", "collection": "sentinel-2-l2a", "properties": {"datetime": "2025-11-07T03:00:00Z", "eo:cloud_cover": 12}, "bbox": [113, 30, 114, 31], "geometry": {"type": "Polygon", "coordinates": [ring(113, 30, 114, 31)]}, "assets": {"thumbnail": {"href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/test.jpg"}}}]}


class GeometryTest(unittest.TestCase):
    def test_valid_types_and_metadata(self):
        geo = collection([point(113, 30), feature({"type": "LineString", "coordinates": [[113, 30], [113.01, 30.01]]}, ident="b"), feature({"type": "Polygon", "coordinates": [ring()]}, ident="c")])
        normalized, meta = validate_geojson(geo)
        self.assertEqual(meta["feature_count"], 3)
        self.assertEqual(meta["geometry_types"], {"Point": 1, "LineString": 1, "Polygon": 1})
        self.assertEqual(meta["bbox"], [113, 30, 113.01, 30.01])
        self.assertEqual(digest(normalized), digest(copy.deepcopy(normalized)))

    def test_bad_coordinates_and_unsupported_types(self):
        for coords in ([True, 30], [10**1000,30], [float('nan'), 30], [113, float('inf')], [181, 30], [113, 86], [113, 30, 0]):
            with self.subTest(coords=coords), self.assertRaises(GeoError):
                validate_geojson(collection([feature({"type": "Point", "coordinates": coords})]))
        with self.assertRaises(GeoError):
            validate_geojson(collection([feature({"type": "MultiPoint", "coordinates": [[113, 30]]})]))

    def test_ring_constraints_and_self_intersections(self):
        bad = [ring()[:-1], [[113, 30], [113.01, 30], [113.02, 30], [113, 30]],
               [[113, 30], [113.01, 30.01], [113, 30.01], [113.01, 30], [113, 30]],
               ring(179, 30, -179, 31), ring(100, 10, 120, 20)]
        for coords in bad:
            with self.subTest(coords=coords), self.assertRaises(GeoError):
                validate_geojson(collection([feature({"type": "Polygon", "coordinates": [coords]})]))

    def test_diagonal_collinear_ring_rejected_before_spherical_area(self):
        coords=[[113,30],[113.01,30.01],[113.02,30.02],[113,30]]
        for positions in (coords, list(reversed(coords))):
            with self.assertRaises(GeoError) as error:
                validate_geojson(collection([feature({"type":"Polygon","coordinates":[positions]})]))
            self.assertEqual(error.exception.code,"DEGENERATE_RING")

    def test_hole_area_matches_analytic_spherical_rectangle(self):
        outer, hole = ring(113, 30, 113.02, 30.02), ring(113.005, 30.005, 113.015, 30.015)
        data, _ = validate_geojson(collection([feature({"type": "Polygon", "coordinates": [outer, hole]})]))
        _, summary = analyse(data, "spatial_summary", {})
        def expected(x1,y1,x2,y2):
            return EARTH_RADIUS_M ** 2 * math.radians(x2-x1) * (math.sin(math.radians(y2))-math.sin(math.radians(y1)))
        expected_area = (expected(113,30,113.02,30.02)-expected(113.005,30.005,113.015,30.015))/10000
        self.assertAlmostEqual(summary["area_sum_ha"], expected_area, places=5)
        self.assertGreater(summary["area_sum_ha"], 0)

    def test_invalid_hole_topology(self):
        outer = ring(113,30,113.03,30.03)
        bad_holes = [[ring(114,30,114.01,30.01)], [ring(113,30.01,113.01,30.02)],
                     [ring(113.005,30.005,113.02,30.02), ring(113.01,30.01,113.025,30.025)],
                     [ring(113.005,30.005,113.025,30.025), ring(113.01,30.01,113.02,30.02)]]
        for holes in bad_holes:
            with self.subTest(holes=holes), self.assertRaises(GeoError):
                validate_geojson(collection([feature({"type": "Polygon", "coordinates": [outer] + holes})]))

    def test_multipolygon_sum_and_overlap_rejection(self):
        parts = [[ring(113,30,113.01,30.01)], [ring(113.02,30,113.03,30.01)]]
        geo, _ = validate_geojson(collection([feature({"type": "MultiPolygon", "coordinates": parts})]))
        _, summary = analyse(geo,"spatial_summary",{})
        self.assertAlmostEqual(summary["area_sum_ha"], sum(polygon_area_m2(p) for p in parts)/10000, places=5)
        for second in ([ring(113.005,30,113.015,30.01)], [ring(113.01,30,113.02,30.01)]):
            with self.assertRaises(GeoError):
                validate_geojson(collection([feature({"type":"MultiPolygon","coordinates":[parts[0],second]})]))

    def test_properties_limits_and_no_nested_content(self):
        for props in ({"x": {}}, {"x": []}, {"__proto__": "bad"}, {"x": float('inf')}, {"x":10**1000}):
            with self.assertRaises(GeoError):
                validate_geojson(collection([point(113,30,props)]))
        for props in ([],False,"",0):
            bad=point(113,30)
            bad["properties"]=props
            with self.assertRaises(GeoError): validate_geojson(collection([bad]))
        with self.assertRaises(GeoError):
            validate_geojson(collection([point(113,30,ident="same"),point(114,30,ident="same")]))
        with self.assertRaises(GeoError):
            validate_geojson(collection([point(113,30,ident=str(i)) for i in range(501)]))

    def test_nearest_known_distance_self_exclusion_and_duplicate_location(self):
        data, _ = validate_geojson(collection([point(0,0,ident="a"),point(0,.01,ident="b"),point(0,.01,ident="c")]))
        result, summary = analyse(data,"nearest",{"max_distance_m":1000})
        self.assertAlmostEqual(result["features"][0]["properties"]["nearest_distance_m"],1111.951, places=2)
        self.assertNotEqual(result["features"][0]["properties"]["nearest_id"],"a")
        self.assertEqual(result["features"][1]["properties"]["nearest_distance_m"],0)
        self.assertEqual(summary["over_distance_count"],1)
        self.assertEqual(summary["point_count"],3)

    def test_nearest_no_points_or_bad_parameters(self):
        data, _ = validate_geojson(collection([feature({"type":"Polygon","coordinates":[ring()]})]))
        with self.assertRaises(GeoError): analyse(data,"nearest",{})
        for limit in (True, -1, 0, "100", float('nan'), 20000001, 10**1000):
            with self.assertRaises(GeoError): analyse(collection([point(0,0),point(1,0,ident="b")]),"nearest",{"max_distance_m":limit})

    def test_ndvi_valid_invalid_and_no_false_zero(self):
        data, _ = validate_geojson(collection([point(113,30,{"red":.2,"nir":.6},"a"),point(113.01,30,{"red":0,"nir":0},"b"),point(113.02,30,{"red":None,"nir":.6},"c"),point(113.03,30,{"red":-.1,"nir":.6},"d"),point(113.04,30,{"red":True,"nir":.6},"e")]))
        output, summary = analyse(data,"ndvi",{})
        self.assertEqual(summary["valid_count"],1)
        self.assertEqual(summary["invalid_count"],4)
        self.assertEqual(output["features"][0]["properties"]["ndvi"],.5)
        self.assertTrue(all(f["properties"]["ndvi"] is None for f in output["features"][1:]))
        with self.assertRaises(GeoError): analyse(collection([point(113,30,{"red":0,"nir":0})]),"ndvi",{})


class PublicTest(unittest.TestCase):
    def test_weather_cache_and_location_provenance(self):
        service = PublicSources()
        service.fetch_json = Mock(return_value=copy.deepcopy(WEATHER))
        first = service.weather({"lat":["30.36"],"lon":["113.45"]})
        second = service.weather({"lat":["30.36"],"lon":["113.45"]})
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["fetched_at"],second["fetched_at"])
        self.assertEqual(first["source_type"],"public_live")
        self.assertEqual(first["requested_location"],{"latitude":30.36,"longitude":113.45})
        self.assertEqual(first["grid_location"],{"latitude":30.33,"longitude":113.42})
        self.assertEqual(first["mode"],"model_forecast")
        self.assertEqual(service.fetch_json.call_count,1)
        self.assertIn("api.open-meteo.com",service.fetch_json.call_args[0][0])

    def test_expired_cache_refetch_failure_never_uses_stale_success(self):
        service = PublicSources()
        service.fetch_json = Mock(return_value=copy.deepcopy(WEATHER))
        service.weather({})
        key = next(iter(service.cache))
        service.cache[key] = (service.cache[key][0]-301, service.cache[key][1])
        service.fetch_json.side_effect = GeoError("UPSTREAM_UNAVAILABLE","test failure",502)
        with self.assertRaises(GeoError) as exc: service.weather({})
        self.assertEqual(exc.exception.status,502)
        self.assertEqual(service.sources()[0]["status"],"最近请求失败")

    def test_satellite_catalog_only_metadata(self):
        service = PublicSources()
        service.fetch_json=Mock(return_value=copy.deepcopy(STAC))
        result=service.satellite({"bbox":["113.2,30.2,113.4,30.4"],"start":["2025-11-01"],"end":["2025-11-10"],"cloud":["40"]})
        self.assertEqual(result["mode"],"catalog_only")
        self.assertEqual(result["source_type"],"public_catalog")
        self.assertEqual(result["items"][0]["id"],"S2-test")
        url=service.fetch_json.call_args[0][0]
        self.assertIn("earth-search.aws.element84.com/v1/search?",url)
        self.assertIn("limit=6",url)
        self.assertEqual(result["query"]["cloud"],40)

    def test_public_input_constraints_and_fixed_host(self):
        service=PublicSources()
        service.fetch_json=Mock()
        for query in ({"bbox":["0,0,179,80"]},{"bbox":["nan,30,114,31"]},{"start":["2025-12-01"],"end":["2025-01-01"]},{"cloud":["101"]}):
            with self.assertRaises(GeoError): service.satellite(query)
        for query in ({"lat":["nan"]},{"lat":["86"]},{"lon":["113","114"]}):
            with self.assertRaises(GeoError): service.weather(query)
        self.assertFalse(service.fetch_json.called)
        real=PublicSources()
        with self.assertRaises(GeoError): real.fetch_json("https://evil.example/")
        with self.assertRaises(GeoError): FixedRedirect().redirect_request(Request("https://api.open-meteo.com/"),None,302,"Found",{},"https://evil.example/")

    def test_weather_never_fakes_grid_and_rejects_empty_query(self):
        for coords in ({"latitude": "unknown", "longitude": None}, {"latitude": 91, "longitude": 113}, {"longitude": 113}):
            service=PublicSources()
            data=copy.deepcopy(WEATHER)
            data.pop("latitude",None)
            data.pop("longitude",None)
            data.update(coords)
            service.fetch_json=Mock(return_value=data)
            with self.assertRaises(GeoError) as exc: service.weather({})
            self.assertEqual(exc.exception.status,502)
        with self.assertRaises(GeoError) as exc: PublicSources().weather({"lat":[""]})
        self.assertEqual(exc.exception.status,400)

    def test_invalid_upstream_schema_is_502(self):
        service=PublicSources()
        service.fetch_json=Mock(return_value={})
        with self.assertRaises(GeoError) as error: service.weather({})
        self.assertEqual(error.exception.status,502)
        with self.assertRaises(GeoError): service.satellite({})


class GeoAPITest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.workspace=Workspace(str(Path(self.temp.name)/"db.sqlite3"),node_delay=.01)
        self.server=LocalServer(("127.0.0.1",0),make_handler(self.workspace))
        self.server.quiet=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base="http://127.0.0.1:%d"%self.server.server_port

    def tearDown(self):
        self.workspace.close();self.server.shutdown();self.server.server_close();self.thread.join(2);self.temp.cleanup()

    def call(self,method,path,body=None):
        request=Request(self.base+path,method=method,data=None if body is None else json.dumps(body).encode(),headers={"Content-Type":"application/json"})
        try: response=urlopen(request,timeout=4)
        except HTTPError as exc: response=exc
        return response.status,json.loads(response.read())

    def test_import_hash_analysis_and_persistence(self):
        geo=collection([point(113,30,{"red":.2,"nir":.6},"p1"),point(113.01,30,{"red":.3,"nir":.6},"p2")])
        code,dataset=self.call("POST","/api/datasets",{"name":"真实用户上传（真实性待核）","geojson":geo})
        self.assertEqual(code,201,dataset)
        self.assertEqual(dataset["source_type"],"user")
        self.assertNotIn("geojson",dataset)
        _,full=self.call("GET","/api/datasets/"+dataset["id"])
        self.assertEqual(full["sha256"],digest(full["geojson"]))
        code,result=self.call("POST","/api/analysis",{"dataset_id":dataset["id"],"algorithm_id":"ndvi","parameters":{}})
        self.assertEqual(code,201,result)
        self.assertEqual(result["provenance"]["source_type"],"user")
        self.assertEqual(result["provenance"]["sha256"],dataset["sha256"])
        self.assertEqual(result["provenance"]["dataset_version"],1)
        self.assertEqual(result["summary"]["valid_count"],2)
        _,loaded=self.call("GET","/api/analysis/"+result["id"])
        self.assertEqual(loaded,result)
        reopened=Workspace(self.workspace.db_path)
        self.assertEqual(reopened.get_dataset(dataset["id"])["sha256"],dataset["sha256"])
        self.assertEqual(reopened.get("analysis",result["id"])["id"],result["id"])
        reopened.close()

    def test_virtual_fixture_removed_and_user_source_cannot_be_forged(self):
        _,boot=self.call("GET","/api/bootstrap")
        self.assertEqual(boot["datasets"],[])
        self.assertEqual(len(boot["algorithms"]),3)
        code,error=self.call("POST","/api/analysis",{"dataset_id":"xt-fixture","algorithm_id":"spatial_summary","parameters":{}})
        self.assertEqual(code,410)
        self.assertEqual(error["error"]["code"],"SAMPLE_DATASET_REMOVED")
        self.assertFalse(self.workspace.list("analysis"))
        code,error=self.call("POST","/api/datasets",{"name":"x","geojson":collection([point(113,30)]),"source_type":"synthetic"})
        self.assertEqual(code,400)
    def test_no_applicable_returns_422_not_empty_success(self):
        _,dataset=self.call("POST","/api/datasets",{"name":"one point","geojson":collection([point(113,30)])})
        for algorithm in ("ndvi","nearest"):
            code,error=self.call("POST","/api/analysis",{"dataset_id":dataset["id"],"algorithm_id":algorithm,"parameters":{}})
            self.assertEqual(code,422,error)
            self.assertEqual(error["error"]["code"],"NO_APPLICABLE_FEATURES")
        _,analyses=self.call("GET","/api/analysis")
        self.assertEqual(analyses,[])

    def test_public_error_api_and_nan_body(self):
        self.workspace.public_sources.fetch_json=Mock(side_effect=GeoError("UPSTREAM_UNAVAILABLE","temporarily unavailable",502))
        code,error=self.call("GET","/api/public/weather?lat=30.36&lon=113.45")
        self.assertEqual(code,502,error)
        self.assertNotIn("current",error)
        code,error=self.call("POST","/api/datasets",{"name":"bad","geojson":collection([point(float('nan'),30)])})
        self.assertEqual(code,400,error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
