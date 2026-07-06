import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import time
import math
import os
from collections import deque
import logging

logger = logging.getLogger("EyeMonitor.Detector")

class EyeDetector:
    def __init__(self, baseline_ear_threshold=0.21, baseline_bpm=15.0):
        # MediaPipe Face Landmarker Setup
        model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_landmarker.task")
        base_options = python.BaseOptions(model_asset_path=model_path)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
            num_faces=1
        )
        self.landmarker = vision.FaceLandmarker.create_from_options(options)
        
        # EAR thresholds and blink detection state
        self.ear_threshold = baseline_ear_threshold
        self.consecutive_closed_frames = 0
        self.blink_timestamps = deque()  # Store timestamps of blinks within last 60s
        self.bpm = baseline_bpm
        self.total_blinks = 0
        
        # Calibration state
        self.is_calibrating = False
        self.calibration_start_time = None
        self.calibration_ears = []
        self.calibration_distances = []
        
        # Rolling baseline metrics
        self.rolling_ears = deque(maxlen=300)       # last 300 frames of EAR
        self.resting_bpm = baseline_bpm
        self.baseline_eye_distance = 100.0          # in pixels, calibrated distance
        
        # Real-time state metrics
        self.current_ear = 0.0
        self.current_perclos = 0.0
        self.current_closeness = 1.0                # relative to baseline (1.0 = normal)
        self.head_pitch = 0.0
        self.head_yaw = 0.0
        self.head_roll = 0.0
        self.ambient_brightness = 120.0             # average pixel brightness
        self.face_detected = False
        self.last_face_seen_time = time.time()
        
        # History queue for frames to calculate PERCLOS (last 60 seconds of states)
        # We record 1 if eye is closed (EAR < threshold), 0 if open
        self.eye_state_history = deque(maxlen=900)  # ~30 seconds of frames at 30fps
        
        # Landmark indices (Standard MediaPipe Face Mesh)
        self.LEFT_EYE = [33, 160, 158, 133, 153, 144]
        self.RIGHT_EYE = [362, 385, 387, 263, 373, 380]
        self.latest_frame_thumb = None

    def _euclidean_dist(self, p1, p2) -> float:
        """Calculate Euclidean distance between two 3D coordinates."""
        return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2 + (p1[2] - p2[2])**2)

    def calculate_ear(self, landmarks, eye_indices) -> float:
        """Calculate Eye Aspect Ratio (EAR) for an eye."""
        # Get coordinates for the 6 landmarks
        pts = [landmarks[idx] for idx in eye_indices]
        
        # pts[1] and pts[5] are vertical landmarks (upper and lower)
        # pts[2] and pts[4] are vertical landmarks (upper and lower)
        # pts[0] and pts[3] are horizontal landmarks (inner and outer corners)
        v1 = self._euclidean_dist(pts[1], pts[5])
        v2 = self._euclidean_dist(pts[2], pts[4])
        h = self._euclidean_dist(pts[0], pts[3])
        
        if h == 0.0:
            return 0.0
        return (v1 + v2) / (2.0 * h)

    def process_frame(self, frame) -> bool:
        """
        Processes a single video frame.
        Returns True if a blink occurred in this frame, False otherwise.
        """
        # Convert BGR to RGB for MediaPipe, and Gray for brightness
        h, w, _ = frame.shape
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        self.ambient_brightness = float(np.mean(gray_frame))
        
        # Convert to MediaPipe Image format
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        
        # Process frame landmarks
        results = self.landmarker.detect(mp_image)
        blink_detected = False
        
        # Annotated frame drawing
        annotated_frame = frame.copy()
        
        if results.face_landmarks:
            self.face_detected = True
            self.last_face_seen_time = time.time()
            
            face_landmarks = results.face_landmarks[0]
            
            # Convert normal landmarks to pixel coordinates
            pts_3d = []
            for lm in face_landmarks:
                pts_3d.append([lm.x * w, lm.y * h, lm.z * w])
                
            # Calculate EAR for both eyes
            left_ear = self.calculate_ear(pts_3d, self.LEFT_EYE)
            right_ear = self.calculate_ear(pts_3d, self.RIGHT_EYE)
            avg_ear = (left_ear + right_ear) / 2.0
            self.current_ear = avg_ear
            
            # Update rolling EAR
            self.rolling_ears.append(avg_ear)
            
            # Distance estimation: outer eye corners
            left_corner = pts_3d[33]
            right_corner = pts_3d[263]
            eye_dist = self._euclidean_dist(left_corner, right_corner)
            
            # Closeness ratio (closeness = current_dist / baseline)
            self.current_closeness = eye_dist / self.baseline_eye_distance
            
            # Head Pose estimation
            self._estimate_head_pose(pts_3d, w, h)
            
            # Calibration gathering
            if self.is_calibrating:
                self.calibration_ears.append(avg_ear)
                self.calibration_distances.append(eye_dist)
                
            # Draw landmarks on the eyes for the feed
            for idx in self.LEFT_EYE:
                pt = pts_3d[idx]
                cv2.circle(annotated_frame, (int(pt[0]), int(pt[1])), 2, (196, 216, 79), -1) # Teal
            for idx in self.RIGHT_EYE:
                pt = pts_3d[idx]
                cv2.circle(annotated_frame, (int(pt[0]), int(pt[1])), 2, (196, 216, 79), -1) # Teal
                
            # Blink Detection State Machine
            is_closed = avg_ear < self.ear_threshold
            
            # Record state for PERCLOS calculation
            self.eye_state_history.append(1 if is_closed else 0)
            
            if is_closed:
                self.consecutive_closed_frames += 1
            else:
                # If eyes were closed for 2 to 10 consecutive frames, it's a blink.
                # (10 frames at 30 FPS is ~330ms, which fits typical blink speeds.
                # Longer closure is classified as micro-sleep/fatigue).
                if 2 <= self.consecutive_closed_frames <= 10:
                    blink_detected = True
                    self.blink_timestamps.append(time.time())
                    self.total_blinks += 1
                    logger.debug(f"Blink detected! Total: {self.total_blinks}")
                self.consecutive_closed_frames = 0
                
        else:
            # Face not detected
            if time.time() - self.last_face_seen_time > 5.0:
                self.face_detected = False
            self.current_ear = 0.0
            self.consecutive_closed_frames = 0
            
            # Draw "No Face Detected" text on the frame
            cv2.putText(annotated_frame, "Face Not Detected", (20, 40), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (94, 107, 255), 2)
            
        # Clean up old blinks from queue (older than 60s)
        now = time.time()
        while self.blink_timestamps and (now - self.blink_timestamps[0]) > 60.0:
            self.blink_timestamps.popleft()
            
        # Calculate BPM (blinks per minute)
        if self.face_detected:
            self.bpm = len(self.blink_timestamps)
            
        # Calculate PERCLOS
        if len(self.eye_state_history) > 0:
            self.current_perclos = sum(self.eye_state_history) / len(self.eye_state_history)
        else:
            self.current_perclos = 0.0
            
        # Create thumbnail size (240x180) to send via websocket
        self.latest_frame_thumb = cv2.resize(annotated_frame, (240, 180))
            
        return blink_detected

    def start_calibration(self):
        """Start a 15-second calibration block."""
        self.is_calibrating = True
        self.calibration_start_time = time.time()
        self.calibration_ears = []
        self.calibration_distances = []
        logger.info("Calibration started.")

    def finish_calibration(self) -> tuple[float, float]:
        """
        Finish calibration. Computes adaptive EAR threshold and baseline eye distance.
        Returns (new_ear_threshold, new_eye_distance)
        """
        self.is_calibrating = False
        if not self.calibration_ears:
            logger.warning("Calibration failed: no face landmarks captured.")
            return self.ear_threshold, self.baseline_eye_distance
            
        # Standard calibration:
        # Calculate the median EAR during the open phase.
        # Typically, a blink threshold is set to 70-75% of the median open EAR.
        median_ear = float(np.median(self.calibration_ears))
        self.ear_threshold = max(0.15, min(0.25, median_ear * 0.72))
        
        # Calculate average eye distance
        mean_dist = float(np.mean(self.calibration_distances))
        if mean_dist > 0:
            self.baseline_eye_distance = mean_dist
            
        logger.info(f"Calibration complete: EAR Threshold={self.ear_threshold:.3f}, Eye Distance Baseline={self.baseline_eye_distance:.1f}")
        return self.ear_threshold, self.baseline_eye_distance

    def _estimate_head_pose(self, pts, w, h):
        """Calculate approximate pitch, yaw, and roll (in degrees)."""
        # Landmark indices:
        # Nose Tip: 1
        # Chin: 152
        # Left Eye Corner: 33
        # Right Eye Corner: 263
        # Left Mouth Corner: 61
        # Right Mouth Corner: 291
        
        # 1. Roll: Angle of eye corners line
        dx = pts[263][0] - pts[33][0]
        dy = pts[263][1] - pts[33][1]
        self.head_roll = math.degrees(math.atan2(dy, dx))
        
        # 2. Yaw: Ratio of Nose-to-Left-Eye vs Nose-to-Right-Eye
        nose = pts[1]
        dist_left = self._euclidean_dist(nose, pts[33])
        dist_right = self._euclidean_dist(nose, pts[263])
        if dist_right > 0:
            yaw_ratio = dist_left / dist_right
            # Convert ratio to degrees (rough approximation)
            self.head_yaw = (yaw_ratio - 1.0) * 45.0
            self.head_yaw = max(-90.0, min(90.0, self.head_yaw))
            
        # 3. Pitch: Ratio of (Nose-to-Forehead) vs (Nose-to-Chin)
        # Forehead index: 10
        dist_forehead = self._euclidean_dist(nose, pts[10])
        dist_chin = self._euclidean_dist(nose, pts[152])
        if dist_chin > 0:
            pitch_ratio = dist_forehead / dist_chin
            # Normal ratio is around 1.0. Tilted down increases ratio, tilted up decreases
            self.head_pitch = (pitch_ratio - 1.0) * 35.0
            self.head_pitch = max(-90.0, min(90.0, self.head_pitch))

    def get_strain_score(self) -> int:
        """
        Compute eye strain score (0 - 100).
        Aggregates: Low BPM, High PERCLOS, Closeness, and Poor ambient lighting.
        """
        if not self.face_detected:
            return 0
            
        # 1. Low BPM: Normal resting is ~15. Threshold is 12.
        # Below 12, strain scales up.
        bpm_strain = 0.0
        if self.bpm < 12:
            bpm_strain = (12.0 - self.bpm) / 12.0 * 100.0
            
        # 2. High PERCLOS: Eye closed ratio
        perclos_strain = self.current_perclos * 200.0  # e.g., 20% closed -> 40 strain
        
        # 3. Screen Closeness: normal closeness is <= 1.0. Closeness > 1.25 is too close.
        closeness_strain = 0.0
        if self.current_closeness > 1.25:
            closeness_strain = (self.current_closeness - 1.25) * 150.0
            
        # 4. Ambient brightness: optimal is between 80 and 220. Below 50 is poor.
        brightness_strain = 0.0
        if self.ambient_brightness < 50.0:
            brightness_strain = (50.0 - self.ambient_brightness) / 50.0 * 50.0
            
        score = (0.5 * bpm_strain) + (0.3 * perclos_strain) + (0.1 * closeness_strain) + (0.1 * brightness_strain)
        return int(max(0, min(100, score)))
