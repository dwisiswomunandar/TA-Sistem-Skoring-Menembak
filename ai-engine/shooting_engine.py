# cv-engine/shooting_engine.py
import cv2
import numpy as np
import math

class TargetScoringEngine:
    def __init__(self, target_distance_m=25.0, bullet_cal_mm=9.0):
        self.target_distance_m = target_distance_m
        self.bullet_radius_mm = bullet_cal_mm / 2.0
        self.BLACK_ZONE_RADIUS_MM = 100.0  
        self.RING_STEP_MM = 25.0           

    # TASK 1: Algoritma Computer Vision (Perspective & Hough Circle)
    def perspective_transform(self, image, corners):
        """Meluruskan gambar lesan yang miring dari 4 titik sudut."""
        width, height = 500, 530
        pts1 = np.float32(corners)
        pts2 = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
        matrix = cv2.getPerspectiveTransform(pts1, pts2)
        return cv2.warpPerspective(image, matrix, (width, height))

    def calibrate_target(self, warped_image):
        """Mendeteksi pusat target untuk rasio milimeter per piksel."""
        gray = cv2.cvtColor(warped_image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (7, 7), 0)
        _, thresh = cv2.threshold(blurred, 80, 255, cv2.THRESH_BINARY_INV)
        
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
            
        largest_contour = max(contours, key=cv2.contourArea)
        (center_x, center_y), radius_px = cv2.minEnclosingCircle(largest_contour)
        mm_per_pixel = self.BLACK_ZONE_RADIUS_MM / radius_px
        
        return {"center": (float(center_x), float(center_y)), "mm_per_pixel": float(mm_per_pixel)}

    # TASK 2: Kalkulasi Balistik
    def compute_ballistics(self, yolo_boxes, calibration_data):
        """Menghitung Skor (Euclidean), Group Size, dan MOA."""
        center_x, center_y = calibration_data["center"]
        mm_px = calibration_data["mm_per_pixel"]
        
        bullet_radius_px = self.bullet_radius_mm / mm_px
        ring_step_px = self.RING_STEP_MM / mm_px
        hit_points = []
        
        for box in yolo_boxes:
            dist = math.hypot(box['x'] - center_x, box['y'] - center_y)
            closest_edge = max(0.0, dist - bullet_radius_px)
            raw_ring = 10 - math.floor(closest_edge / ring_step_px)
            score = max(0, min(10, int(raw_ring)))
            hit_points.append({"x": box['x'], "y": box['y'], "score": score})
            
        total_shots = len(hit_points)
        total_score = sum(p['score'] for p in hit_points)
        
        max_dist_px = 0.0
        for i in range(total_shots):
            for j in range(i + 1, total_shots):
                d = math.hypot(hit_points[i]['x'] - hit_points[j]['x'], hit_points[i]['y'] - hit_points[j]['y'])
                if d > max_dist_px: max_dist_px = d
                    
        group_size_mm = max_dist_px * mm_px
        moa_const = self.target_distance_m * 1000 * math.tan(math.radians(1/60))
        moa = round(group_size_mm / moa_const, 2) if moa_const > 0 else 0.0
        
        return {
            "shots": hit_points,
            "total_score": total_score,
            "group_size_mm": round(group_size_mm, 2),
            "moa": moa
        }