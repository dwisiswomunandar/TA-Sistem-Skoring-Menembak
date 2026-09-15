/**
 * [TASK 3] Interactive Canvas Editor (Click-to-add / Click-to-remove)
 */
class TargetCanvasEditor {
    constructor(canvasId, bgImageUrl, initialShots = []) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.shots = initialShots;
        
        this.bg = new Image();
        this.bg.onload = () => this.render();
        this.bg.src = bgImageUrl;

        this.initEvents();
    }

    initEvents() {
        this.canvas.addEventListener('click', (e) => {
            const rect = this.canvas.getBoundingClientRect();
            const scaleX = this.canvas.width / rect.width;
            const scaleY = this.canvas.height / rect.height;

            const clickX = Math.round((e.clientX - rect.left) * scaleX);
            const clickY = Math.round((e.clientY - rect.top) * scaleY);

            // Cek klik terdekat (toleransi radius 15px) untuk hapus titik
            const existingIndex = this.shots.findIndex(s => Math.hypot(s.x - clickX, s.y - clickY) <= 15);

            if (existingIndex !== -1) {
                this.shots.splice(existingIndex, 1);
            } else {
                this.shots.push({
                    x: clickX,
                    y: clickY,
                    score: this.calculateRingScore(clickX, clickY),
                    action_type: 'manual_added'
                });
            }
            this.render();
        });
    }

    calculateRingScore(x, y) {
        const dist = Math.hypot(x - 500, y - 500);
        const ringRadii = {10: 35, 9: 75, 8: 120, 7: 170, 6: 225, 5: 285, 4: 350, 3: 415, 2: 465, 1: 495};
        
        for (const score of Object.keys(ringRadii).map(Number).sort((a, b) => b - a)) {
            if (dist <= ringRadii[score]) return score;
        }
        return 0;
    }

    render() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.ctx.drawImage(this.bg, 0, 0, this.canvas.width, this.canvas.height);

        this.shots.forEach((shot, index) => {
            // Circle Marker
            this.ctx.beginPath();
            this.ctx.arc(shot.x, shot.y, 12, 0, 2 * Math.PI);
            this.ctx.strokeStyle = shot.action_type === 'manual_added' ? '#00FF00' : '#FF0000';
            this.ctx.lineWidth = 2;
            this.ctx.stroke();

            // Center Dot
            this.ctx.beginPath();
            this.ctx.arc(shot.x, shot.y, 3, 0, 2 * Math.PI);
            this.ctx.fillStyle = '#FFFF00';
            this.ctx.fill();

            // Number Label
            this.ctx.font = 'bold 14px Arial';
            this.ctx.fillStyle = '#FF0000';
            this.ctx.fillText((index + 1).toString(), shot.x + 14, shot.y - 4);
        });
    }

    getExportData() {
        return this.shots;
    }
}