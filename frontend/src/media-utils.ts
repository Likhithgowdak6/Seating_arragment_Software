export function waitFor(video:HTMLVideoElement,event:string,start:()=>void):Promise<void>{
 return new Promise((resolve,reject)=>{const timer=setTimeout(()=>finish(new Error('Video decoding took too long. Try a shorter MP4 clip.')),15000);const ok=()=>finish();const fail=()=>finish(new Error('This video cannot be decoded. Try an MP4 supported by your browser.'));function finish(error?:Error){clearTimeout(timer);video.removeEventListener(event,ok);video.removeEventListener('error',fail);error?reject(error):resolve();}video.addEventListener(event,ok,{once:true});video.addEventListener('error',fail,{once:true});try{start();}catch(e){finish(e as Error);}});
}
export async function snapshot(video:HTMLVideoElement,name:string):Promise<File>{
 if(!video.videoWidth||!video.videoHeight)throw new Error('Camera is not ready. Wait for the preview and try again.');
 const scale=Math.min(1,1280/video.videoWidth,1280/video.videoHeight);const canvas=document.createElement('canvas');canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);const context=canvas.getContext('2d');if(!context)throw new Error('Image capture is unavailable in this browser.');context.drawImage(video,0,0,canvas.width,canvas.height);
 const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('Could not capture image.')),'image/jpeg',.9));return new File([blob],name,{type:'image/jpeg'});
}
