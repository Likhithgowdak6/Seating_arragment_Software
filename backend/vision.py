"""Local pretrained detection; photos are processed in memory, never sent to an AI API."""
import io, os, threading
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import HTTPException
MODEL_ID='IDEA-Research/grounding-dino-tiny'
_lock=threading.Lock()
_processor=None
_model=None

def load_model():
    global _processor,_model
    if _model is None:
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        import torch
        from huggingface_hub import snapshot_download
        torch.set_num_threads(max(1,min(8,int(os.getenv('VISION_CPU_THREADS','4')))))
        try:
            source=snapshot_download(MODEL_ID,local_files_only=True)
            from pathlib import Path
            if not (Path(source)/'model.safetensors').is_file():raise FileNotFoundError()
        except (OSError,FileNotFoundError):
            source=snapshot_download(MODEL_ID,allow_patterns=['*.json','*.txt','*.safetensors'],max_workers=2)
        processor=AutoProcessor.from_pretrained(source,local_files_only=True)
        model=AutoModelForZeroShotObjectDetection.from_pretrained(source,local_files_only=True)
        model.eval()
        _processor,_model=processor,model
    return _processor,_model

def remove_duplicates(detections):
    # IoU suppresses repeated predictions while retaining small rear desks that
    # may lie inside a much larger foreground prediction in perspective views.
    kept=[]
    for d in sorted(detections,key=lambda box:box['score'],reverse=True):
        x1,y1,x2,y2=d['box'];area=max(0,x2-x1)*max(0,y2-y1)
        if area<=0:continue
        duplicate=False
        for k in kept:
            a,b,c,e=k['box'];intersection=max(0,min(x2,c)-max(x1,a))*max(0,min(y2,e)-max(y1,b))
            other_area=(c-a)*(e-b);union=area+other_area-intersection
            # A tabletop-only box and a whole-desk box often share the same top
            # edge. Collapse those, while retaining separate lower/front desks.
            same_top=abs(y1-b)<.15*max(min(y2-y1,e-b),1e-9)
            contained=intersection/max(min(area,other_area),1e-9)>.85
            if intersection/max(union,1e-9)>.5 or (same_top and contained):duplicate=True;break
        if not duplicate:kept.append(d)
    filtered=[]
    for d in kept:
        x1,y1,x2,y2=d['box'];area=(x2-x1)*(y2-y1)
        contained=0
        for other in kept:
            if other is d or other['score']<d['score']:continue
            a,b,c,e=other['box'];cx=(a+c)/2;cy=(b+e)/2
            if (c-a)*(e-b)<area*.65 and x1<cx<x2 and y1<cy<y2:contained+=1
        # Weak predictions encompassing several stronger individual desks are
        # likely a group/table row, not an additional seating unit.
        if contained<3:filtered.append(d)
    return filtered[:100]

def detect(raw,confidence=.2,enhanced=True,room_type='classroom'):
    if not .1<=confidence<=.6:raise HTTPException(400,'Confidence must be between 0.1 and 0.6.')
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width*source.height>20_000_000:raise HTTPException(400,'Photo must be under 20 megapixels.')
            image=ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((1600,1600))
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError):raise HTTPException(400,'Upload a valid JPEG or PNG photo.')
    if not _lock.acquire(blocking=False):raise HTTPException(429,'Another photo is being processed. Try again shortly.')
    try:
        import torch
        processor,model=load_model()
        prompts={'classroom':'a classroom desk.','computer_lab':'a computer desk.','benches':'a school bench.'}
        prompt=prompts.get(room_type,prompts['classroom'])
        w,h=image.size
        regions=[(0,0,w,h)]
        if enhanced:
            # Perspective makes far desks small: zoom both sides of the upper
            # 65% of the image rather than guessing a count from monitors.
            regions.extend([(0,0,int(w*.55),int(h*.65)),(int(w*.45),0,w,int(h*.65))])
        candidates=[]
        for left,top,right,bottom in regions:
            crop=image.crop((left,top,right,bottom))
            inputs=processor(images=crop,text=prompt,return_tensors='pt')
            with torch.inference_mode():outputs=model(**inputs)
            result=processor.post_process_grounded_object_detection(outputs,inputs.input_ids,threshold=confidence,text_threshold=.2,target_sizes=[crop.size[::-1]])[0]
            for box,score in zip(result['boxes'],result['scores']):
                x1,y1,x2,y2=[float(v) for v in box]
                # Avoid counting a desk fragment created by an interior crop edge.
                if left>0 and x1<crop.width*.015:continue
                if right<w and x2>crop.width*.985:continue
                if bottom<h and y2>crop.height*.985:continue
                mapped=[max(0,min(1,(x1+left)/w)),max(0,min(1,(y1+top)/h)),max(0,min(1,(x2+left)/w)),max(0,min(1,(y2+top)/h))]
                candidates.append({'box':mapped,'score':float(score)})
        return {'model':MODEL_ID,'target':prompt,'boxes':remove_duplicates(candidates),'passes':len(regions),'confidence':confidence,'warning':'AI suggestions, not a verified count. Zoomed crops can still miss or duplicate desks. Review every box before confirming room capacity.'}

    except HTTPException:raise
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception('Photo detection failed')
        raise HTTPException(503,'Photo detection is unavailable. Check the model download and AI dependencies; manual classroom setup still works.') from exc
    finally:_lock.release()

def stitch_frames(images):
    """Merge overlapping views geometrically. Refuse a map when alignment fails."""
    import base64
    try:import cv2,numpy as np
    except ImportError:raise HTTPException(503,'Install the AI requirements to enable overlapping-frame stitching.')
    frames=[]
    for raw in images:
        try:
            with Image.open(io.BytesIO(raw)) as source:
                if source.width*source.height>8_000_000:raise HTTPException(400,'Each frame must be under 8 megapixels.')
                frame=ImageOps.exif_transpose(source).convert('RGB');frame.thumbnail((1280,1280));frames.append(cv2.cvtColor(np.array(frame),cv2.COLOR_RGB2BGR))
        except (UnidentifiedImageError,OSError,Image.DecompressionBombError):raise HTTPException(400,'An uploaded frame is invalid.')
    try:
        stitcher=cv2.Stitcher_create(cv2.Stitcher_PANORAMA);stitcher.setPanoConfidenceThresh(.5)
        status,panorama=stitcher.stitch(frames)
        if status!=cv2.Stitcher_OK or panorama is None:raise HTTPException(422,'These views cannot be aligned reliably. Use overlapping images taken from one position, or choose a single full-room frame.')
        height,width=panorama.shape[:2]
        if width*height>20_000_000:raise HTTPException(422,'Combined image is too large. Use fewer frames.')
        ratio=min(1,1800/max(width,height))
        if ratio<1:panorama=cv2.resize(panorama,(int(width*ratio),int(height*ratio)))
        ok,encoded=cv2.imencode('.jpg',panorama,[cv2.IMWRITE_JPEG_QUALITY,90])
        if not ok:raise HTTPException(422,'Could not encode the combined view.')
        return {'image':'data:image/jpeg;base64,'+base64.b64encode(encoded.tobytes()).decode(),'warning':'Review seams and overlapping objects. Moving cameras and parallax can distort or duplicate benches. This is a panorama, not a 3D floor plan.'}
    except HTTPException:raise
    except Exception:raise HTTPException(422,'View alignment failed. Try a single full-room frame instead.')
