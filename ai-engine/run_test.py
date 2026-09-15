import cv2
from shooting_engine import ShootingTargetEngine

def main():
    # 1. Inisialisasi Engine (Ukuran Lesan 500mm, Jarak Tembak 25m)
    engine = ShootingTargetEngine(canvas_size_px=1000, target_diameter_mm=500.0, distance_m=25.0)

    # 2. Baca Gambar Lesan Uji Coba
    image_path = "lesan_test.png"
    img = cv2.imread(image_path)

    if img is None:
        print(f"[ERROR] Gambar {image_path} tidak ditemukan!")
        return

    # 3. Deteksi Titik Tembakan
    detected_shots = engine.detect_shots(img)
    print(f"\n--- HASIL DETEKSI TEMBAKAN ({len(detected_shots)} Titik) ---")
    for idx, shot in enumerate(detected_shots, 1):
        print(f"Tembakan #{idx}: X={shot['x']}, Y={shot['y']} | Skor Ring: {shot['score']} | Tipe: {shot['action_type']}")

    # 4. Hitung Parametrik Balistik (Total Skor, Group Size, MOA)
    ballistics = engine.calculate_ballistics(detected_shots)
    print("\n--- HASIL KALKULASI BALISTIK ---")
    print(f"Total Perolehan Skor : {ballistics['total_score']}")
    print(f"Rata-rata Skor       : {ballistics['mean_score']}")
    print(f"Group Size (Extreme) : {ballistics['group_size_mm']} mm")
    print(f"Akurasi MOA          : {ballistics['moa']} MOA")
    print(f"Jumlah Tembakan      : {ballistics['count']}")

    # 5. Visualisasi Hasil Deteksi pada Gambar (Output visual)
    output_img = img.copy()
    for idx, shot in enumerate(detected_shots, 1):
        # Gambar Lingkaran Penanda Tembakan
        cv2.circle(output_img, (shot['x'], shot['y']), 12, (0, 255, 0), 2)
        # Gambar Nomor Urut Tembakan
        cv2.putText(output_img, str(idx), (shot['x'] + 15, shot['y'] - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

    cv2.imwrite("lesan_result.png", output_img)
    print("\n[SUCCESS] Hasil visualisasi deteksi telah disimpan ke: lesan_result.png")

if __name__ == "__main__":
    main()