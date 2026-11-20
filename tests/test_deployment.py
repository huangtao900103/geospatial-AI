"""Container network behavior and CLI lifecycle, without touching production data."""
import importlib.util
import json
import selectors
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from backend.core.network_policy import allowed_hostname,host_allowlist,request_hostname
from backend.server import Workspace,LocalServer,make_handler
from backend.paths import PROJECT_ROOT


class NetworkPolicyTest(unittest.TestCase):
    def test_exact_hosts_allow_container_addresses_without_a_wildcard(self):
        self.assertEqual(host_allowlist(['AGRI.EXAMPLE','192.168.1.50']),frozenset(['127.0.0.1','localhost','agri.example','192.168.1.50']))
        for bad in ['*','*.example','http://agri.example','agri.example:8020','agri.example/path',' agri.example','']:
            with self.assertRaises(ValueError):allowed_hostname(bad)
    def test_request_parsing_rejects_malformed_or_confusing_authorities(self):
        for bad in ['localhost@evil.example','localhost/path','localhost,evil.example','localhost:abc','localhost:99999','localhost?x','localhost\\evil','localhost\n']:
            self.assertIsNone(request_hostname(bad),bad)
        self.assertEqual(request_hostname('AGRI.EXAMPLE:8020'),'agri.example')
        self.assertEqual(request_hostname('[::1]:8016'),'::1')
    def test_entrypoint_keeps_data_in_the_volume_and_validates_environment(self):
        path=PROJECT_ROOT/'deploy/docker/entrypoint.py';spec=importlib.util.spec_from_file_location('container_entrypoint',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        args=module.command({'GEOAI_ALLOWED_HOSTS':'agri.example,192.168.1.50'},Path('/app'))
        self.assertEqual(args[args.index('--bind')+1],'0.0.0.0');self.assertEqual(args[args.index('--db')+1],'/app/data/workspace.sqlite3')
        self.assertIn('localhost',args);self.assertIn('agri.example',args)
        with self.assertRaises(ValueError):module.command({'GEOAI_ALLOWED_HOSTS':'*'},Path('/app'))


class ContainerNetworkHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.workspace=Workspace(Path(self.tmp.name)/'workspace.sqlite3');self.server=LocalServer(('0.0.0.0',0),make_handler(self.workspace,allowed_hosts=['agri.example','192.168.1.50']))
        self.server.quiet=True;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.workspace.close();self.tmp.cleanup()
    def request(self,host,body=None,origin=None,extra=None):
        headers={'Host':host,**(extra or {})}
        if origin:headers['Origin']=origin
        path='/api/agri-operations/registries'
        if body is not None:headers['Content-Type']='application/json'
        request=Request(self.base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:r=urlopen(request,timeout=5)
        except HTTPError as e:r=e
        with r:return r.status,json.load(r)
    def test_custom_host_works_and_unlisted_host_cannot_use_api(self):
        self.assertEqual(self.request('agri.example:8020')[0],200)
        self.assertEqual(self.request('192.168.1.50:8020')[0],200)
        self.assertEqual(self.request('evil.example:8020')[0],403)
    def test_custom_host_writes_still_require_matching_origin(self):
        body={'name':'isolated deployment test','kind':'farmland','source':'explicit test input','actor':'test'}
        self.assertEqual(self.request('agri.example:8020',body,'https://evil.example')[0],403)
        self.assertEqual(self.request('agri.example:8020',body,'http://agri.example:8020')[0],201)
        self.assertEqual(len(self.workspace.agri_operations.registries()),1)
    def test_proxy_headers_do_not_bypass_host_or_origin_validation(self):
        self.assertEqual(self.request('evil.example',extra={'X-Forwarded-Host':'agri.example'})[0],403)
        body={'name':'x','kind':'farmland','source':'test','actor':'test'}
        self.assertEqual(self.request('agri.example',body,'http://evil.example',{'X-Forwarded-Host':'evil.example'})[0],403)
    def test_default_handler_remains_local_only(self):
        other=LocalServer(('127.0.0.1',0),make_handler(self.workspace));other.quiet=True;thread=threading.Thread(target=other.serve_forever,daemon=True);thread.start()
        try:
            req=Request('http://127.0.0.1:'+str(other.server_port)+'/api/agri-operations/catalog',headers={'Host':'agri.example'})
            with self.assertRaises(HTTPError) as error:urlopen(req,timeout=5)
            self.assertEqual(error.exception.code,403)
        finally:other.shutdown();other.server_close();thread.join()


class DeploymentCLITest(unittest.TestCase):
    def test_real_cli_binds_for_containers_and_stops_cleanly_on_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'workspace.sqlite3'
            process=subprocess.Popen([sys.executable,str(PROJECT_ROOT/'server.py'),'--port','0','--bind','0.0.0.0','--allowed-host','agri.example','--db',str(db)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=directory)
            try:
                reader=selectors.DefaultSelector();reader.register(process.stdout,selectors.EVENT_READ)
                self.assertTrue(reader.select(15),'service startup timed out');line=process.stdout.readline();reader.close()
                self.assertIn('0.0.0.0:',line);port=int(line.strip().rsplit(':',1)[1])
                with urlopen(Request('http://127.0.0.1:'+str(port)+'/api/agri-operations/catalog',headers={'Host':'agri.example:8020'}),timeout=5) as r:self.assertEqual(len(json.load(r)['modules']),9)
                process.terminate();self.assertEqual(process.wait(timeout=15),0)
                self.assertTrue(db.is_file())
            finally:
                if process.poll() is None:process.kill();process.wait()
                process.stdout.close();process.stderr.close()
    def test_invalid_container_host_argument_fails_before_creating_a_database(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'workspace.sqlite3'
            result=subprocess.run([sys.executable,str(PROJECT_ROOT/'server.py'),'--db',str(db),'--allowed-host','*'],cwd=directory,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,2);self.assertFalse(db.exists())
