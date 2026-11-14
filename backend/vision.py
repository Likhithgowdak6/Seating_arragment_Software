"""Local pretrained detection; photos are processed in memory, never sent to an AI API."""
import io, threading
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
        processor=AutoProcessor.from_pretrained(MODEL_ID)
        model=AutoModelForZeroShotObjectDetection.from_pretrained(MODEL_ID)
        model.eval()
        _processor,_model=processor,model
    return _processor,_model

def remove_duplicates(detections):
    kept=[]
    for d in sorted(detections,key=lambda b:b['score'],reverse=True):
        x1,y1,x2,y2=d['box'];area=max(0,x2-x1)*max(0,y2-y1)
        if area<=0:continue
        duplicate=False
        for k in kept:
            a,b,c,e=k['box'];intersection=max(0,min(x2,c)-max(x1,a))*max(0,min(y2,e)-max(y1,b))
            other=(c-a)*(e-b)
            if intersection/max(min(area,other),1e-9)>.75:duplicate=True;break
        if not duplicate:kept.append(d)
    return kept

def detect(raw):
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
        inputs=processor(images=image,text='a classroom desk.',return_tensors='pt')
        with torch.inference_mode():outputs=model(**inputs)
        result=processor.post_process_grounded_object_detection(outputs,inputs.input_ids,threshold=.3,text_threshold=.25,target_sizes=[image.size[::-1]])[0]
        w,h=image.size
        boxes=[{'box':[max(0,min(1,float(v)/([w,h,w,h][i]))) for i,v in enumerate(box)],'score':float(score)} for box,score in zip(result['boxes'],result['scores'])]
        return {'model':MODEL_ID,'target':'classroom desk (one desk-and-bench unit)','boxes':remove_duplicates(boxes),'warning':'Suggestions only. Confirm one box per desk-and-bench unit, room orientation, rows and seat capacity.'}
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
