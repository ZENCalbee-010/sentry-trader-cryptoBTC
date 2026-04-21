"""
SENTRY_TRADER — AI Model (Random Forest)
==========================================
Mode: Inference-Only บน Hardware (โหลด .joblib)
       Training ทำแยกต่างหากบน PC แล้วโยนไฟล์มา

Workflow:
  [PC] train.py → fit RandomForest → save model.joblib
  [Bot] AIModel.load() → predict() ใช้ confidence score

Features (7 ตัว):
  rsi, ema_slope, atr_ratio, volume_ratio,
  price_to_ema200, rsi_momentum, body_ratio

Label: 1 = Trade นี้ชนะ (price hit TP ก่อน SL)
       0 = Trade นี้แพ้
"""

import os
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from loguru import logger
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report, confusion_matrix

import config

# ============================================================
# Constants
# ============================================================
FEATURE_COLUMNS = [
    "rsi",
    "ema_slope",
    "atr_ratio",
    "volume_ratio",
    "price_to_ema200",
    "rsi_momentum",
    "body_ratio",
]

MODEL_PATH = config.MODELS_DIR / "random_forest.joblib"
SCALER_PATH = config.MODELS_DIR / "scaler.joblib"


# ============================================================
# Label Creation
# ============================================================

def create_labels(
    df: pd.DataFrame,
    atr_series: pd.Series,
    rr_ratio: float = config.RR_RATIO,
    atr_multiplier: float = config.ATR_MULTIPLIER_SL,
    lookahead_bars: int = 24,  # 24 × 15m = 6 ชั่วโมง
) -> pd.Series:
    """
    สร้าง Label สำหรับ Training

    Logic:
        Entry = close[i]
        SL    = Entry - (atr_multiplier × ATR)
        TP    = Entry + (atr_multiplier × ATR × rr_ratio)
        
        ดู lookahead_bars แท่งต่อไป:
          ถ้า High >= TP ก่อน Low <= SL → Label = 1 (Win)
          ถ้า Low <= SL ก่อน High >= TP → Label = 0 (Loss)
          ถ้าไม่มีอะไรถึง → Label = 0 (Timeout = Loss)
    
    Returns:
        pd.Series of {0, 1} aligned with df.index
    """
    labels = []
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    atrs   = atr_series.values

    n = len(df)
    for i in range(n):
        # ต้องมีข้อมูลข้างหน้าพอ
        if i + lookahead_bars >= n or np.isnan(atrs[i]):
            labels.append(np.nan)
            continue

        entry = closes[i]
        sl    = entry - atr_multiplier * atrs[i]
        tp    = entry + atr_multiplier * atrs[i] * rr_ratio

        result = 0  # default: loss (timeout)
        for j in range(i + 1, min(i + lookahead_bars + 1, n)):
            if highs[j] >= tp:
                result = 1  # TP hit first
                break
            if lows[j] <= sl:
                result = 0  # SL hit first
                break

        labels.append(result)

    label_series = pd.Series(labels, index=df.index, name="label")
    return label_series


# ============================================================
# AI Model Class
# ============================================================

class AIModel:
    """
    Random Forest Classifier สำหรับ SENTRY_TRADER

    Training (บน PC เท่านั้น):
        model = AIModel()
        model.train(X_train, y_train)
        model.save()

    Inference (บน Bot / Hardware):
        model = AIModel()
        model.load()
        confidence = model.predict_confidence(feature_dict)
    """

    def __init__(self):
        self.model: Optional[Pipeline] = None
        self.is_loaded = False
        self.feature_importance: dict = {}

    # ----------------------------------------------------------
    # Training (PC only)
    # ----------------------------------------------------------

    def build_pipeline(self) -> Pipeline:
        """สร้าง Pipeline: StandardScaler → RandomForest"""
        rf = RandomForestClassifier(
            n_estimators=config.RF_N_ESTIMATORS,
            max_depth=config.RF_MAX_DEPTH,
            min_samples_leaf=5,
            max_features="sqrt",
            class_weight="balanced",  # จัดการ class imbalance
            random_state=42,
            n_jobs=-1,  # ใช้ทุก CPU core
        )
        return Pipeline([
            ("scaler", StandardScaler()),
            ("rf", rf),
        ])

    def train(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        cv_folds: int = 5,
    ) -> dict:
        """
        Train Random Forest พร้อม Time-Series Cross Validation

        Args:
            X: Feature DataFrame (shape N × 7)
            y: Label Series (0 = loss, 1 = win)
            cv_folds: จำนวน fold สำหรับ CV

        Returns:
            dict of training metrics
        """
        logger.info(f"🧠 เริ่ม Training | Samples={len(X)} | Features={list(X.columns)}")
        logger.info(f"   Win Rate ใน Training Data: {y.mean():.1%}")

        # Time-Series Cross Validation (ห้ามใช้ random CV กับ time series!)
        tscv = TimeSeriesSplit(n_splits=cv_folds)
        pipeline = self.build_pipeline()

        cv_scores = cross_val_score(
            pipeline, X[FEATURE_COLUMNS], y,
            cv=tscv,
            scoring="f1",
            error_score="raise",
        )

        logger.info(f"   CV F1 Scores: {[f'{s:.3f}' for s in cv_scores]}")
        logger.info(f"   CV F1 Mean: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")

        # Train บน Data ทั้งหมด
        pipeline.fit(X[FEATURE_COLUMNS], y)
        self.model = pipeline

        # Feature Importance (จาก RF, ไม่ใช่ Scaler)
        rf_model = pipeline.named_steps["rf"]
        self.feature_importance = dict(
            zip(FEATURE_COLUMNS, rf_model.feature_importances_)
        )

        # Evaluation บน Training data
        y_pred = pipeline.predict(X[FEATURE_COLUMNS])
        report = classification_report(y, y_pred, output_dict=True)

        metrics = {
            "train_samples":  len(X),
            "win_rate_data":  float(y.mean()),
            "cv_f1_mean":     float(cv_scores.mean()),
            "cv_f1_std":      float(cv_scores.std()),
            "train_accuracy": float(report["accuracy"]),
            "feature_importance": self.feature_importance,
        }

        logger.success(f"✅ Training เสร็จ | CV F1={cv_scores.mean():.3f}")
        self._log_feature_importance()

        return metrics

    def _log_feature_importance(self):
        if not self.feature_importance:
            return
        logger.info("📊 Feature Importance:")
        for feat, imp in sorted(self.feature_importance.items(), key=lambda x: -x[1]):
            bar = "█" * int(imp * 40)
            logger.info(f"   {feat:<20} {bar} {imp:.4f}")

    # ----------------------------------------------------------
    # Save / Load
    # ----------------------------------------------------------

    def save(self, path: Optional[Path] = None):
        """บันทึก Model ลงไฟล์ .joblib"""
        if self.model is None:
            raise RuntimeError("ยังไม่ได้ Train Model")

        save_path = path or MODEL_PATH
        save_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, save_path, compress=3)
        logger.success(f"💾 บันทึก Model: {save_path} ({save_path.stat().st_size / 1024:.1f} KB)")

    def load(self, path: Optional[Path] = None) -> bool:
        """
        โหลด Model จากไฟล์ (สำหรับ Inference บน Bot)
        
        Returns:
            True = โหลดสำเร็จ
            False = ไม่มีไฟล์ (Bot จะรันโหมด Indicator-only)
        """
        load_path = path or MODEL_PATH

        if not load_path.exists():
            logger.warning(
                f"⚠️ ไม่พบ Model File: {load_path} → "
                f"Bot รันในโหมด Indicator-Only (ไม่ใช้ AI)"
            )
            self.is_loaded = False
            return False

        self.model = joblib.load(load_path)
        self.is_loaded = True
        logger.success(f"✅ โหลด AI Model: {load_path}")
        return True

    # ----------------------------------------------------------
    # Inference (Real-time)
    # ----------------------------------------------------------

    def predict_confidence(self, feature_dict: dict) -> float:
        """
        ทำนาย Confidence Score จาก Feature ของแท่งล่าสุด
        
        Args:
            feature_dict: {'rsi': 31.2, 'ema_slope': 0.05, ...}
        
        Returns:
            float: ความมั่นใจที่ trade นี้จะชนะ (0.0 - 1.0)
            ถ้าไม่มี Model → returns 1.0 (bypass AI check)
        """
        if not self.is_loaded or self.model is None:
            return 1.0  # Bypass ถ้าไม่มี Model

        # ตรวจสอบว่า Feature ครบ
        missing = [f for f in FEATURE_COLUMNS if f not in feature_dict]
        if missing:
            logger.warning(f"Feature ขาด: {missing} → ไม่ให้สัญญาณ")
            return 0.0

        X = pd.DataFrame([{f: feature_dict[f] for f in FEATURE_COLUMNS}])
        proba = self.model.predict_proba(X)[0]

        # proba[1] = ความน่าจะเป็นที่ class 1 (Win)
        confidence = float(proba[1])

        logger.debug(f"🤖 AI Confidence: {confidence:.1%}")
        return confidence
