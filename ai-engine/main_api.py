import cv2
import numpy as np
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from ultralytics import YOLO

# Inisialisasi Aplikasi FastAPI
app = FastAPI(title="API Penilaian Menembak - CV Engine")

# Load Model YOLOv8 (Pastikan best.pt berada sejajar di dalam folder ai-engine)
try:
    model = YOLO('best.pt')
except Exception as e:
    print("WARNING: Model 'best.pt' tidak ditemukan di folder ai-engine!")
    model = None

def apply_perspective_transform(image):
    """
    Fungsi placeholder untuk meluruskan gambar (Perspective Transform).
    Saat ini mengembalikan gambar asli. Logika ekstraksi sudut kertas 
    bisa ditambahkan di sini nantinya.
    """
    # TODO: Implementasi cv2.findContours dan cv2.warpPerspective
    return image

@app.post("/api/evaluate")
async def evaluate_target(file: UploadFile = File(...)):
    """
    Endpoint untuk menerima gambar lesan, melakukan deteksi, 
    dan mengembalikan koordinat [x, y] murni dalam format JSON.
    """
    try:
        # 1. Membaca file gambar langsung ke memory (jauh lebih cepat dari shutil/temp_file)
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            return JSONResponse(
                content={"status": "error", "message": "File tidak terbaca sebagai gambar yang valid."},
                status_code=400
            )

        # 2. Pre-processing gambar
        processed_img = apply_perspective_transform(img)

        # 3. Eksekusi Model YOLOv8
        detected_points = []
        
        if model is not None:
            # conf=0.2 digunakan agar batas toleransi lebih baik untuk lubang peluru
            results = model.predict(source=processed_img, conf=0.2, save=False, verbose=False)
            
            # Ekstraksi koordinat X dan Y dari setiap bounding box
            for result in results:
                for box in result.boxes:
                    # Ambil koordinat bounding box [x1, y1, x2, y2]
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    
                    # Hitung titik tengah (centroid) dari lubang peluru
                    center_x = (x1 + x2) / 2.0
                    center_y = (y1 + y2) / 2.0
                    
                    detected_points.append({
                        "x": round(center_x, 2),
                        "y": round(center_y, 2)
                    })

        # 4. Return respon JSON murni (Hanya koordinat, TIDAK ADA output gambar)
        return JSONResponse(content={
            "status": "success",
            "image_width": processed_img.shape[1],
            "image_height": processed_img.shape[0],
            "shots": detected_points
        })

    except Exception as e:
        return JSONResponse(
            content={"status": "error", "message": str(e)},
            status_code=500
        )