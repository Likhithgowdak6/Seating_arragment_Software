const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const ts=require('typescript');
function load(overrides={}){
 const code=ts.transpileModule(fs.readFileSync('src/media-utils.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const context={exports:{},setTimeout,clearTimeout,File,...overrides};vm.runInNewContext(code,context);return context.exports;
}
test('video metadata and seek events complete after decoding',async()=>{
 const {waitFor}=load();const video=new EventTarget();await waitFor(video,'seeked',()=>video.dispatchEvent(new Event('seeked')));
});
test('unsupported video fails instead of leaving extraction busy',async()=>{
 const {waitFor}=load();const video=new EventTarget();await assert.rejects(waitFor(video,'loadedmetadata',()=>video.dispatchEvent(new Event('error'))),/cannot be decoded/);
});
test('stalled decoder times out',async()=>{
 const {waitFor}=load({setTimeout:fn=>setTimeout(fn,1)});await assert.rejects(waitFor(new EventTarget(),'seeked',()=>{}),/too long/);
});
test('camera must have a ready frame before capture',async()=>{
 const {snapshot}=load();await assert.rejects(snapshot({videoWidth:0,videoHeight:0},'test.jpg'),/not ready/);
});
test('large camera frames are resized and encoded as JPEG',async()=>{
 let canvas={width:0,height:0,getContext:()=>({drawImage:(v,x,y,w,h)=>{assert.equal(w,1280);assert.equal(h,720);}}),toBlob:(callback,type)=>{assert.equal(type,'image/jpeg');callback(new Blob(['jpeg'],{type}));}};
 const {snapshot}=load({document:{createElement:()=>canvas}});const file=await snapshot({videoWidth:3840,videoHeight:2160},'capture.jpg');assert.equal(file.name,'capture.jpg');assert.equal(file.type,'image/jpeg');assert.equal(canvas.width,1280);assert.equal(canvas.height,720);
});
test('encoding failure produces a recoverable error',async()=>{
 const {snapshot}=load({document:{createElement:()=>({getContext:()=>({drawImage:()=>{}}),toBlob:cb=>cb(null)})}});await assert.rejects(snapshot({videoWidth:640,videoHeight:480},'test.jpg'),/Could not capture/);
});
