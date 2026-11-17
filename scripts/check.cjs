// Repository checks use isolated fixtures, not the running workspace database.
const {spawnSync}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const localPython=path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const python=fs.existsSync(localPython)?localPython:'python3';
const args=process.argv.slice(2),allowed=new Set(['--syntax','--backend','--ui']);
if(args.includes('--help')||args.includes('-h')){
 console.log('Usage: node scripts/check.cjs [--syntax] [--backend] [--ui]\nNo options runs every group. Groups may be combined.');
 process.exit(0);
}
if(args.some(arg=>!allowed.has(arg))){console.error('Unknown check option. Use --help.');process.exit(2)}
const groups=new Set(args.length?args:['--syntax','--backend','--ui']);
function files(dir,extensions){
 return fs.readdirSync(dir,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name)).flatMap(entry=>
  ['vendor','__pycache__'].includes(entry.name)?[]:entry.isDirectory()?files(path.join(dir,entry.name),extensions):extensions.has(path.extname(entry.name))?[path.join(dir,entry.name)]:[]);
}
function run(label,command,parameters){
 console.log('\n'+label);
 const result=spawnSync(command,parameters,{cwd:root,stdio:'inherit'});
 if(result.error){console.error(result.error.message);process.exit(1)}
 if(result.status!==0)process.exit(result.status??1);
}
if(groups.has('--syntax')){
 const modules=[...files(path.join(root,'dist'),new Set(['.js'])),...files(path.join(root,'scripts'),new Set(['.js','.mjs','.cjs']))];
 for(const module of modules)run('Syntax: '+path.relative(root,module),process.execPath,['--check',module]);
 run('Python syntax',python,['-c',
  'import ast; from pathlib import Path; paths=[Path("server.py")]+[p for folder in ("backend","scripts","tests") for p in Path(folder).rglob("*.py")]; [ast.parse(p.read_text(encoding="utf-8"),filename=str(p)) for p in paths]; print("Python syntax passed: %s files" % len(paths))']);
}
if(groups.has('--backend'))run('Isolated backend tests',python,['-m','unittest','discover','-s','tests','-q']);
if(groups.has('--ui')){
 for(const script of files(path.join(root,'scripts','verification'),new Set(['.mjs'])))run('UI contract: '+path.relative(root,script),process.execPath,[script]);
}
console.log('\nRequested checks passed. Browser and external-source integration checks run separately.');
