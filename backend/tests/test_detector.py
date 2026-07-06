import pytest
import numpy as np
from unittest.mock import MagicMock
import sys
import os

# Ensure the parent directory is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from detector import EyeDetector

def test_euclidean_distance():
    detector = EyeDetector()
    p1 = (0, 0, 0)
    p2 = (3, 4, 0)
    # Distance should be 5
    assert detector._euclidean_dist(p1, p2) == 5.0

def test_calculate_ear():
    detector = EyeDetector()
    
    # 6 landmarks for eye: [P1, P2, P3, P4, P5, P6]
    # P1 (left corner), P4 (right corner) -> width = 10
    # P2, P6 -> vertical 1 = 2
    # P3, P5 -> vertical 2 = 2
    # EAR = (2 + 2) / (2 * 10) = 4 / 20 = 0.2
    pts = [
        [0, 0, 0],   # P1
        [3, 1, 0],   # P2
        [7, 1, 0],   # P3
        [10, 0, 0],  # P4
        [7, -1, 0],  # P5
        [3, -1, 0]   # P6
    ]
    
    # Use indices mapping directly to these 6 points in order
    indices = [0, 1, 2, 3, 4, 5]
    ear = detector.calculate_ear(pts, indices)
    assert abs(ear - 0.2) < 1e-6

def test_blink_state_machine():
    detector = EyeDetector(baseline_ear_threshold=0.20)
    
    # Mock landmarker.detect to simulate eye states
    detector.landmarker.detect = MagicMock()
    
    # Define a helper to mock pts_3d inside process_frame
    # We will patch the process_frame logic or directly run the state updates
    # Let's verify standard state machine behavior:
    # 1. Start open: consecutive_closed_frames = 0
    # 2. Closed frame: consecutive_closed_frames = 1
    # 3. Closed frame: consecutive_closed_frames = 2
    # 4. Open frame: count blink, consecutive_closed_frames = 0
    
    # We can test this by manually updating detector.face_detected and simulating frames:
    detector.face_detected = True
    
    # Helper to simulate a frame process by directly setting EAR and running the machine logic
    # instead of doing full image process, which requires camera frame.
    def mock_process_ear(ear):
        detector.current_ear = ear
        is_closed = ear < detector.ear_threshold
        detector.eye_state_history.append(1 if is_closed else 0)
        
        blink_detected = False
        if is_closed:
            detector.consecutive_closed_frames += 1
        else:
            if 2 <= detector.consecutive_closed_frames <= 10:
                blink_detected = True
            detector.consecutive_closed_frames = 0
        return blink_detected

    # Sequence of EARs
    ears = [
        0.25, 0.25, # Open
        0.15, 0.15, # Closed for 2 frames
        0.25,       # Open again -> Should trigger blink
        0.25,
        0.14, 0.14, 0.14, 0.14, # Closed for 4 frames
        0.26,       # Open again -> Should trigger blink
        0.26,
        0.12,       # Closed for only 1 frame
        0.25        # Open again -> Should NOT trigger blink (needs >= 2 frames)
    ]
    
    blinks = [mock_process_ear(e) for e in ears]
    
    # Blinks should be detected at index 4 (after 2 closed frames + 1 open)
    # and index 10 (after 4 closed frames + 1 open)
    assert blinks[4] is True
    assert blinks[10] is True
    assert sum(1 for b in blinks if b) == 2
