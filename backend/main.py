import asyncio
import json
import threading
import time
import os
import sys
import logging
from datetime import datetime, timezone
import cv2
from PIL import Image, ImageDraw
import websockets

# Import local modules
from detector import EyeDetector
from database import Database
from notifier import Notifier
from window_tracker import get_active_window, is_meeting_active, correlate_focus_app
from calendar_dnd import CalendarDND

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("EyeMonitor.Main")

# Global status and state variables
detector = EyeDetector()
db = Database()
notifier = Notifier()
calendar_dnd = CalendarDND()

# Shared state lock
state_lock = threading.Lock()
is_running = True
monitoring_start_time = time.time()
monitoring_active = True
calibration_seconds_remaining = 0
calibration_in_progress = False

# Active window tracking DND
calendar_path_setting = None

# Timer trackers
last_metric_log_time = time.time()
pomodoro_timer_seconds = 1200   # 20 minutes in seconds
pomodoro_current_remaining = pomodoro_timer_seconds
break_active = False

# Low BPM tracking
low_bpm_start_time = None
critical_bpm_start_time = None
low_bpm_alert_fired = False
critical_bpm_alert_fired = False

# Connected WebSocket clients
connected_clients = set()
ws_loop = None

def get_tray_image():
    """Generate a simple eye icon image for the system tray."""
    # Create a 64x64 image with a transparent background
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Outer eye shape
    draw.chord([4, 16, 60, 48], 0, 360, fill=(79, 216, 196, 255), outline=(10, 12, 16, 255))
    # Iris (circle inside)
    draw.ellipse([20, 20, 44, 44], fill=(232, 163, 61, 255))
    # Pupil
    draw.ellipse([28, 28, 36, 36], fill=(10, 12, 16, 255))
    
    return img

def setup_tray_icon():
    """Setup and run the system tray icon using pystray."""
    try:
        import pystray
        
        def on_toggle_monitoring(icon, item):
            global monitoring_active
            with state_lock:
                monitoring_active = not monitoring_active
            logger.info(f"Monitoring state set to: {monitoring_active}")
            icon.update_menu()

        def on_toggle_dnd(icon, item):
            current = notifier.dnd_active
            notifier.set_dnd(not current)
            logger.info(f"DND state set to: {notifier.dnd_active}")
            icon.update_menu()

        def on_recalibrate(icon, item):
            global calibration_in_progress
            with state_lock:
                if not calibration_in_progress:
                    detector.start_calibration()
                    calibration_in_progress = True
            logger.info("Recalibration started from System Tray.")

        def on_quit(icon, item):
            global is_running
            logger.info("Quitting Eye Monitor background service...")
            with state_lock:
                is_running = False
            icon.stop()

        # Build menu items
        menu = pystray.Menu(
            pystray.MenuItem("Pause / Resume Monitoring", on_toggle_monitoring, 
                             checked=lambda item: monitoring_active),
            pystray.MenuItem("Do Not Disturb (Mute Alerts)", on_toggle_dnd,
                             checked=lambda item: notifier.dnd_active),
            pystray.MenuItem("Run Calibration (15s)", on_recalibrate),
            pystray.MenuItem("Quit", on_quit)
        )
        
        icon = pystray.Icon("eyemonitor", get_tray_image(), "AI Eye Strain Monitor", menu)
        notifier.notify_callback = lambda title, message: icon.notify(message, title)
        icon.run()
    except Exception as e:
        logger.error(f"Failed to start system tray icon: {e}")

def camera_processing_loop():
    """Read webcam frames and perform blink tracking in a separate thread."""
    global calibration_in_progress, calibration_seconds_remaining, is_running, monitoring_active
    
    # Try multiple camera indices (0, 1, etc.)
    cap = None
    for camera_idx in [0, 1, 2]:
        cap = cv2.VideoCapture(camera_idx)
        if cap.isOpened():
            logger.info(f"Successfully opened camera on index {camera_idx}")
            break
        cap.release()
        cap = None

    if not cap:
        logger.error("No webcam could be initialized.")
        # We will keep the service running even if camera fails, so break timer works
        while is_running:
            time.sleep(1)
        return

    # Load baseline if exists
    baseline = db.get_latest_baseline()
    if baseline:
        detector.ear_threshold = baseline["ear_threshold"]
        detector.resting_bpm = baseline["resting_bpm"]
        logger.info(f"Loaded existing baseline settings: EAR={detector.ear_threshold:.3f}")

    frame_counter = 0
    while is_running:
        if not monitoring_active:
            time.sleep(0.1)
            continue
            
        ret, frame = cap.read()
        if not ret:
            logger.warning("Failed to grab camera frame.")
            time.sleep(0.1)
            continue
            
        # Optional horizontal flip for mirror display consistency
        frame = cv2.flip(frame, 1)
        
        # Core processing
        blink_detected = detector.process_frame(frame)
        
        # Stream frame base64 thumbnail at ~10 FPS (every 3 frames)
        frame_counter += 1
        if frame_counter % 3 == 0:
            if detector.latest_frame_thumb is not None:
                import base64
                _, buffer = cv2.imencode('.jpg', detector.latest_frame_thumb, [cv2.IMWRITE_JPEG_QUALITY, 55])
                frame_b64 = base64.b64encode(buffer).decode('utf-8')
                broadcast_ws_event({
                    "event": "frame",
                    "image": frame_b64,
                    "faceDetected": detector.face_detected
                })
        
        # Check calibration progress
        if calibration_in_progress:
            elapsed = time.time() - detector.calibration_start_time
            remaining = 15 - int(elapsed)
            if remaining <= 0:
                ear_thresh, eye_dist = detector.finish_calibration()
                db.log_baseline(detector.bpm, ear_thresh)
                calibration_in_progress = False
                calibration_seconds_remaining = 0
                
                # Broadcast calibration done event
                broadcast_ws_event({
                    "event": "calibration_complete",
                    "earThreshold": ear_thresh,
                    "baselineEyeDistance": eye_dist
                })
            else:
                calibration_seconds_remaining = remaining
                broadcast_ws_event({
                    "event": "calibration_progress",
                    "secondsLeft": remaining
                })
                
        # Handle real-time blink notification to webapp for aperture shutter flutter
        if blink_detected:
            broadcast_ws_event({
                "event": "blink",
                "bpm": detector.bpm
            })
            
        # Control rate: limit processing loop to ~30 FPS to save CPU
        time.sleep(0.03)
        
    cap.release()
    logger.info("Camera processing loop terminated.")

def broadcast_ws_event(payload):
    """Safely post events to the WebSocket broadcasting queue."""
    global ws_loop
    if ws_loop and connected_clients:
        asyncio.run_coroutine_threadsafe(
            send_to_all_clients(json.dumps(payload)),
            ws_loop
        )

async def send_to_all_clients(message: str):
    """Asynchronously send a message to all connected clients."""
    if connected_clients:
        await asyncio.gather(*[client.send(message) for client in connected_clients], return_exceptions=True)

async def state_monitor_loop():
    """Periodically check stats, run notification logic, and log metrics."""
    global low_bpm_start_time, critical_bpm_start_time, pomodoro_current_remaining, break_active, last_metric_log_time, is_running, monitoring_start_time
    global low_bpm_alert_fired, critical_bpm_alert_fired
    
    while is_running:
        await asyncio.sleep(1.0)
        
        if not monitoring_active:
            continue
            
        # Get active app & check DND status
        active_app = correlate_focus_app()
        app_dnd = is_meeting_active()
        cal_dnd, cal_meeting_title = calendar_dnd.is_in_meeting()
        
        # Combined DND state
        notifier.dnd_active = app_dnd or cal_dnd
        
        # Tick the Pomodoro Break Timer (if no break is active)
        if not break_active:
            pomodoro_current_remaining -= 1
            if pomodoro_current_remaining <= 0:
                break_active = True
                pomodoro_current_remaining = 0
                
                # Fire OS Notification for break
                notifier.trigger("break_reminder")
                db.log_alert("break_reminder", detector.bpm, get_active_window())
                
                # Broadcast break alert event
                broadcast_ws_event({
                    "event": "alert",
                    "type": "break_reminder",
                    "bpm": detector.bpm,
                    "message": "Time to rest your eyes (20-20-20 rule)",
                    "timestamp": datetime.now().isoformat()
                })
                
        # Alert analysis check: only when face is detected and warmup passed (120s / 2m)
        current_time = time.time()
        warmup_passed = (current_time - monitoring_start_time) >= 120.0
        
        if detector.face_detected and warmup_passed and not notifier.dnd_active:
            # 1. Low BPM: threshold is 12 BPM, sustained for 5 seconds (5.0s)
            if detector.bpm < 12:
                if not low_bpm_alert_fired:
                    if low_bpm_start_time is None:
                        low_bpm_start_time = time.time()
                    elif time.time() - low_bpm_start_time >= 5.0:
                        # Fire alert
                        fired = notifier.trigger("low_bpm", bpm=detector.bpm)
                        if fired:
                            db.log_alert("low_bpm", detector.bpm, get_active_window())
                            broadcast_ws_event({
                                "event": "alert",
                                "type": "low_bpm",
                                "bpm": detector.bpm,
                                "message": f"Low blink rate ({detector.bpm}/min) detected. Look away for 20s.",
                                "timestamp": datetime.now().isoformat()
                            })
                            low_bpm_alert_fired = True
                            low_bpm_start_time = None
            else:
                low_bpm_start_time = None
                low_bpm_alert_fired = False
                
            # 2. Critical BPM: threshold is 5 BPM, sustained for 10 seconds (10.0s)
            if detector.bpm < 5:
                if not critical_bpm_alert_fired:
                    if critical_bpm_start_time is None:
                        critical_bpm_start_time = time.time()
                    elif time.time() - critical_bpm_start_time >= 10.0:
                        fired = notifier.trigger("critical_bpm", bpm=detector.bpm)
                        if fired:
                            db.log_alert("critical_bpm", detector.bpm, get_active_window())
                            broadcast_ws_event({
                                "event": "alert",
                                "type": "critical_bpm",
                                "bpm": detector.bpm,
                                "message": f"CRITICAL: Extreme low blink rate ({detector.bpm}/min). Take a break!",
                                "timestamp": datetime.now().isoformat()
                            })
                            critical_bpm_alert_fired = True
                            critical_bpm_start_time = None
            else:
                critical_bpm_start_time = None
                critical_bpm_alert_fired = False
        else:
            # Reset alerting accumulators if face is gone
            low_bpm_start_time = None
            critical_bpm_start_time = None

        # Periodically log average metrics to Database (every 60 seconds)
        if time.time() - last_metric_log_time >= 60.0:
            if detector.face_detected:
                db.log_metrics(
                    bpm=detector.bpm,
                    ear=detector.current_ear,
                    perclos=detector.current_perclos,
                    posture_score=100.0 - abs(detector.head_pitch) - abs(detector.head_yaw) # rough posture score
                )
            last_metric_log_time = time.time()

        # Emit second-by-second updates to clients
        payload = {
            "timestamp": datetime.now().isoformat(),
            "bpm": detector.bpm,
            "totalBlinks": detector.total_blinks,
            "ear": round(detector.current_ear, 3),
            "perclos": round(detector.current_perclos, 3),
            "strainScore": detector.get_strain_score(),
            "faceDetected": detector.face_detected,
            "alertActive": (detector.bpm < 12 and detector.face_detected),
            "headPose": {
                "pitch": round(detector.head_pitch, 1),
                "yaw": round(detector.head_yaw, 1),
                "roll": round(detector.head_roll, 1)
            },
            "closeness": round(detector.current_closeness, 2),
            "brightness": round(detector.ambient_brightness, 1),
            "dndActive": notifier.dnd_active,
            "activeApp": active_app,
            "pomodoroRemaining": pomodoro_current_remaining,
            "breakActive": break_active
        }
        await send_to_all_clients(json.dumps(payload))

async def handle_websocket(websocket):
    """Handle incoming WebSocket requests and events from clients."""
    global monitoring_active, calibration_in_progress, break_active, pomodoro_current_remaining, monitoring_start_time
    connected_clients.add(websocket)
    logger.info(f"WebSocket client connected. Total clients: {len(connected_clients)}")
    
    # Send historical data on initial connection
    alerts = db.get_alerts(30)
    metrics_history = db.get_metrics_history(60)
    await websocket.send(json.dumps({
        "event": "history_sync",
        "alerts": alerts,
        "metricsHistory": metrics_history
    }))
    
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                action = data.get("action")
                
                if action == "start_monitoring":
                    with state_lock:
                        monitoring_active = True
                    logger.info("Monitoring started by client request.")
                    
                elif action == "stop_monitoring":
                    with state_lock:
                        monitoring_active = False
                    logger.info("Monitoring stopped by client request.")
                    
                elif action == "calibrate":
                    with state_lock:
                        detector.start_calibration()
                        calibration_in_progress = True
                    logger.info("Calibration started by client request.")
                    
                elif action == "set_threshold":
                    val = float(data.get("threshold", 0.21))
                    detector.ear_threshold = val
                    logger.info(f"EAR Threshold manually set to {val}")

                elif action == "set_cooldown":
                    val = int(data.get("cooldown", 600))
                    notifier.set_cooldown(val)
                    logger.info(f"Notification cooldown set to {val} seconds")
                    
                elif action == "take_break_resolved":
                    break_active = False
                    pomodoro_current_remaining = pomodoro_timer_seconds
                    db.update_last_alert_resolved()
                    logger.info("Break interval completed/resolved by user.")
                    await websocket.send(json.dumps({
                        "event": "break_resolved"
                    }))
                    
                elif action == "clear_logs":
                    db.clear_logs()
                    detector.total_blinks = 0
                    logger.info("Logs cleared by user.")
                    await websocket.send(json.dumps({
                        "event": "logs_cleared"
                    }))

                elif action == "reset_session":
                    db.clear_logs()
                    detector.total_blinks = 0
                    
                    # Reset Pomodoro timer
                    break_active = False
                    pomodoro_current_remaining = pomodoro_timer_seconds
                    
                    # Reset monitoring start time
                    monitoring_start_time = time.time()
                    
                    # Reset detector collections
                    detector.blink_timestamps.clear()
                    detector.eye_state_history.clear()
                    detector.consecutive_closed_frames = 0
                    
                    # Clear alert cooldown history and state flags so alerts can fire immediately
                    notifier.last_notified.clear()
                    low_bpm_alert_fired = False
                    critical_bpm_alert_fired = False
                    
                    logger.info("Entire session reset by user (cooldowns cleared).")
                    await websocket.send(json.dumps({
                        "event": "session_reset"
                    }))
                    
                elif action == "export_csv":
                    # Export relative to workspace / desktop or just in backend directory
                    export_name = "eye_monitor_report.csv"
                    export_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), export_name)
                    path = db.export_csv(export_path)
                    await websocket.send(json.dumps({
                        "event": "export_complete",
                        "filePath": path,
                        "fileName": export_name
                    }))
                    
                elif action == "toggle_dnd":
                    val = bool(data.get("dnd", False))
                    notifier.set_dnd(val)
                    logger.info(f"DND toggled manually to {val}")

                elif action == "set_ics_path":
                    path = data.get("ics_path")
                    calendar_dnd.set_ics_path(path)
                    logger.info(f"Calendar ICS path set to: {path}")

            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received from client: {message}")
            except Exception as e:
                logger.error(f"Error handling client message: {e}")
                
    except websockets.exceptions.ConnectionClosedOK:
        pass
    except Exception as e:
        logger.error(f"WebSocket client connection error: {e}")
    finally:
        connected_clients.remove(websocket)
        logger.info(f"WebSocket client disconnected. Total clients: {len(connected_clients)}")

async def start_websocket_server():
    """Start the WebSocket server on localhost."""
    global ws_loop
    ws_loop = asyncio.get_running_loop()
    
    async with websockets.serve(handle_websocket, "127.0.0.1", 8765):
        logger.info("WebSocket Server started on ws://localhost:8765")
        # Run state monitor loop concurrently
        await state_monitor_loop()

def main():
    # Start tray icon in a companion thread
    tray_thread = threading.Thread(target=setup_tray_icon, daemon=True)
    tray_thread.start()
    
    # Start camera analysis in a companion thread
    cam_thread = threading.Thread(target=camera_processing_loop, daemon=True)
    cam_thread.start()
    
    # Start WebSocket server and logic loop in the main thread (async)
    try:
        asyncio.run(start_websocket_server())
    except KeyboardInterrupt:
        logger.info("Service interrupted by user. Exiting...")
    finally:
        global is_running
        is_running = False
        logger.info("Service shutting down.")

if __name__ == "__main__":
    main()
