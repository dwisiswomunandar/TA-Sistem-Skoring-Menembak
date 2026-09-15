# cv-engine/main_api.py
from fastapi import FastAPI, UploadFile, File
from ultralytics import YOLO
import cv2
import shutil
import os
from shooting_engine import TargetScoringEngine

app = FastAPI()
model = YOLO("best.pt") # Pastikan best.pt ditaruh sejajar dengan file ini
engine = TargetScoringEngine()

@app.post("/api/evaluate")
async def evaluate_target(file: UploadFile = File(...)):
    temp_path = f"temp_{file.filename}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    image = cv2.imread(temp_path)
    results = model(image, conf=0.4)
    
    yolo_boxes = []
    for r in results:
        for box in r.boxes:
            xyxy = box.xyxy[0].cpu().numpy()
            yolo_boxes.append({"x": float((xyxy[0]+xyxy[2])/2), "y": float((xyxy[1]+xyxy[3])/2)})
            
    cal_data = engine.calibrate_target(image)
    if not cal_data:
        return {"status": "error", "message": "Target tidak terdeteksi"}
        
    final_data = engine.compute_ballistics(yolo_boxes, cal_data)
    os.remove(temp_path)
    
    return {"status": "success", "data": final_data}