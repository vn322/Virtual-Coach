# coach_window.py 

import sys
import os
import time
import cv2
import pandas as pd
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget,
    QLabel, QHBoxLayout, QFileDialog, QMessageBox, QSplitter, QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsTextItem
)
from PyQt5.QtCore import QTimer, Qt, QRectF
from PyQt5.QtGui import QImage, QPixmap, QFont, QColor, QPainter

from pose_analyzer import PoseAnalyzer
from model_loader import load_model
from comparison import compare_model_user
from report_coach import save_coach_report
from report_generator import save_video_with_overlay


class VirtualCoachWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Virtual Coach — Твой тренер")
        self.setGeometry(100, 100, 1280, 768)

        self.model_df = None
        self.model_video_path = None
        self.model_cap = None

        self.user_analyzer = None
        self.user_cap = None
        self.user_frames = []

        self.model_timer = QTimer()
        self.model_timer.timeout.connect(self.update_model_frame)

        self.user_timer = QTimer()
        self.user_timer.timeout.connect(self.update_user_frame)

        self.score = 0.0
        self.details = {}

        self.init_ui()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout()

        # Splitter: left — model, right — user
        splitter = QSplitter(Qt.Horizontal)

        self.model_label = QLabel("Модельное видео")
        self.model_label.setAlignment(Qt.AlignCenter)
        self.model_label.setStyleSheet("background: #111; color: white;")
        self.model_label.setMinimumSize(600, 400)

        self.user_label = QLabel("Ваше выполнение")
        self.user_label.setAlignment(Qt.AlignCenter)
        self.user_label.setStyleSheet("background: #222; color: white;")
        self.user_label.setMinimumSize(600, 400)

        splitter.addWidget(self.model_label)
        splitter.addWidget(self.user_label)
        main_layout.addWidget(splitter)

        # Кнопки
        btn_layout = QHBoxLayout()

        def _add_btn(text, slot, color):
            btn = QPushButton(text)
            btn.setStyleSheet(f"font-size: 16px; padding: 10px; background: {color}; color: white; border-radius: 8px;")
            btn.clicked.connect(slot)
            btn.setMinimumHeight(50)
            return btn

        btn_layout.addWidget(_add_btn("📁 Загрузить модель", self.load_model_folder, "#2196F3"))
        btn_layout.addWidget(_add_btn("📹 Камера", self.use_camera, "#4CAF50"))
        btn_layout.addWidget(_add_btn("🎞️ Видео спортсмена", self.load_user_video, "#9C27B0"))
        btn_layout.addWidget(_add_btn("▶️ Начать", self.start_coaching, "#FF9800"))
        btn_layout.addWidget(_add_btn("⏹️ Стоп", self.stop_coaching, "#f44336"))

        main_layout.addLayout(btn_layout)

        # === РЕЗУЛЬТАТЫ: крупный % + детали-бары ===
        self.result_widget = QWidget()
        self.result_layout = QVBoxLayout()
        self.result_widget.setLayout(self.result_layout)

        # Крупный общий результат
        self.score_label = QLabel("—")
        self.score_label.setAlignment(Qt.AlignCenter)
        self.score_label.setFont(QFont("Arial", 28, QFont.Bold))
        self.score_label.setStyleSheet("color: #2196F3; margin: 10px;")
        self.result_layout.addWidget(self.score_label)

        # Детали как горизонтальные бары
        self.details_scene = QGraphicsScene()
        self.details_view = QGraphicsView(self.details_scene)
        self.details_view.setFixedHeight(120)
        self.details_view.setStyleSheet("background: #333; border: none;")
        self.details_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.details_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.result_layout.addWidget(self.details_view)

        main_layout.addWidget(self.result_widget)
        self.result_widget.hide()  # скрыт до расчёта

        central.setLayout(main_layout)

    # === МОДЕЛЬ ===
    def load_model_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку с моделью")
        if not folder:
            return
        try:
            self.model_df, self.model_video_path = load_model(folder)
            QMessageBox.information(self, "✅ Модель загружена", f"CSV: {len(self.model_df)} кадров")
        except Exception as e:
            QMessageBox.critical(self, "❌ Ошибка", f"Не удалось загрузить модель:\n{str(e)}")

    def _start_user_source(self, src):
        if self.user_cap:
            self.user_cap.release()
        self.user_cap = cv2.VideoCapture(src)
        if not self.user_cap.isOpened():
            QMessageBox.critical(self, "Ошибка", "Не удалось открыть источник.")
            return
        self.user_analyzer = PoseAnalyzer()
        self.user_frames = []
        self.user_label.setText("Готов к записи")

    def use_camera(self):
        self._start_user_source(0)

    def load_user_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "Видео спортсмена", "", "Video (*.mp4 *.avi)")
        if path:
            self._start_user_source(path)

    def start_coaching(self):
        if self.model_df is None or self.model_video_path is None or self.user_cap is None:
            QMessageBox.warning(self, "⚠️", "Сначала загрузите модель и источник спортсмена.")
            return

        if self.model_cap:
            self.model_cap.release()
        self.model_cap = cv2.VideoCapture(self.model_video_path)

        if self.user_analyzer:
            self.user_analyzer.reset()
            self.user_analyzer.frame_data_log.clear()

        self.model_timer.start(33)
        self.user_timer.start(33)

    def stop_coaching(self):
        self.model_timer.stop()
        self.user_timer.stop()
        if self.model_cap: self.model_cap.release()
        if self.user_cap: self.user_cap.release()
        self.generate_report()

    def update_model_frame(self):
        ret, frame = self.model_cap.read()
        if not ret:
            self.stop_coaching()
            return
        h, w = frame.shape[:2]
        qimg = QImage(frame.data, w, h, w * 3, QImage.Format_BGR888)
        self.model_label.setPixmap(QPixmap.fromImage(qimg).scaled(
            self.model_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def update_user_frame(self):
        ret, frame = self.user_cap.read()
        if not ret:
            self.stop_coaching()
            return

        annot, _ = self.user_analyzer.process_frame(frame)
        self.user_frames.append(annot)

        h, w, ch = annot.shape
        qimg = QImage(annot.data, w, h, ch * w, QImage.Format_BGR888)
        self.user_label.setPixmap(QPixmap.fromImage(qimg).scaled(
            self.user_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def generate_report(self):
        if not self.user_analyzer or not self.user_analyzer.frame_data_log or self.model_df is None:
            QMessageBox.warning(self, "⚠️", "Недостаточно данных для отчёта.")
            return

        user_df = pd.DataFrame(self.user_analyzer.frame_data_log)

        # Очистка
        user_df = user_df.dropna(subset=['frame_number']).reset_index(drop=True)
        angle_cols = [c for c in user_df.columns if c.endswith('_angle')]
        if angle_cols:
            user_df = user_df[~(user_df[angle_cols] == 0).all(axis=1)].reset_index(drop=True)

        if len(user_df) < 10:
            QMessageBox.warning(self, "⚠️", "Слишком мало валидных кадров.")
            return

        try:
            score, details = compare_model_user(self.model_df, user_df)
            self.score = score
            self.details = details

            # === Вывод на экран ===
            color = "#4CAF50" if score >= 80 else "#FF9800" if score >= 60 else "#f44336"
            self.score_label.setText(f"<span style='color:{color}'>{score:.1f}%</span> совпадения с моделью")
            self.score_label.show()

            # Детальные бары
            self.details_scene.clear()
            group_names = {
                "key_angles": "Ключевые углы",
                "other_angles": "Остальные углы",
                "velocities": "Скорости",
                "accelerations": "Ускорения",
                "entropies": "Стабильность"
            }

            y = 10
            for i, (group, (s, w)) in enumerate(details.items()):
                bar_width = 500
                filled = int(bar_width * s)
                rect_full = QGraphicsRectItem(0, y, bar_width, 20)
                rect_full.setBrush(QColor("#555"))
                rect_fill = QGraphicsRectItem(0, y, filled, 20)
                c = QColor("#4CAF50") if s > 0.8 else QColor("#FF9800") if s > 0.6 else QColor("#f44336")
                rect_fill.setBrush(c)

                text = f"{group_names[group]}: {s*100:.1f}%"
                text_item = QGraphicsTextItem(text)
                text_item.setDefaultTextColor(QColor("#fff"))
                text_item.setFont(QFont("Arial", 10))
                text_item.setPos(bar_width + 10, y)

                self.details_scene.addItem(rect_full)
                self.details_scene.addItem(rect_fill)
                self.details_scene.addItem(text_item)
                y += 30

            self.details_view.setSceneRect(self.details_scene.itemsBoundingRect())
            self.result_widget.show()

            # === Отчёт ===
            t = int(time.time())
            report_dir = "coach_output"
            os.makedirs(report_dir, exist_ok=True)

            save_video_with_overlay(f"{report_dir}/user_video_{t}.mp4", self.user_frames)
            save_coach_report(f"{report_dir}/report_{t}", self.model_df, user_df, score, details)

            QMessageBox.information(self, "✅ Готово", f"Отчёт сохранён:\n{report_dir}/report_{t}.pdf")

        except Exception as e:
            import traceback
            QMessageBox.critical(self, "❌ Ошибка", f"{str(e)}\n{traceback.format_exc()}")
