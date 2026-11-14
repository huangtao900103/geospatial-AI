// Check authored browser modules and isolated HTTP/algorithm tests.
const {spawnSync}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const localPython=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const python=fs.existsSync(localPython)?localPython:'python3';
function modules(dir){return fs.readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.name==='vendor'?[]:e.isDirectory()?modules(path.join(dir,e.name)):e.name.endsWith('.js')?[path.join(dir,e.name)]:[])}
const steps=[...modules(path.join(root,'dist')).map(file=>['node',['--check',file]]),[python,['-m','unittest','discover','-s','tests','-v']]];
for(const [command,args] of steps){const r=spawnSync(command,args,{cwd:root,stdio:'inherit'});if(r.error){console.error(r.error.message);process.exit(1)}if(r.status!==0)process.exit(r.status??1)}
console.log('Authored module syntax and isolated backend checks passed. Browser evidence is recorded separately.');
