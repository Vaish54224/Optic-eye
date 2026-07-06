import time
import logging
from datetime import datetime

logger = logging.getLogger("EyeMonitor.Notifier")

try:
    from plyer import notification
    HAS_PLYER = True
except ImportError:
    logger.warning("plyer not installed, native notifications will be mocked.")
    HAS_PLYER = False

class Notifier:
    def __init__(self):
        self.cooldown_duration = 600  # Default 10 minutes (600 seconds)
        self.last_notified = {}       # Maps notification type to timestamp
        self.sound_enabled = True
        self.dnd_active = False       # Manual DND override
        self.notify_callback = None

    def set_cooldown(self, seconds: int):
        """Set notification cooldown duration in seconds."""
        self.cooldown_duration = seconds

    def set_sound(self, enabled: bool):
        """Enable or disable notification sounds."""
        self.sound_enabled = enabled

    def set_dnd(self, active: bool):
        """Manually toggle Do Not Disturb mode."""
        self.dnd_active = active

    def can_notify(self, alert_type: str) -> bool:
        """Check if we can notify based on DND and cooldown."""
        if self.dnd_active:
            logger.debug(f"Notification suppressed: DND is active.")
            return False

        last_time = self.last_notified.get(alert_type, 0)
        current_time = time.time()
        
        if current_time - last_time < self.cooldown_duration:
            remaining = int(self.cooldown_duration - (current_time - last_time))
            logger.debug(f"Notification suppressed: Cooldown for {alert_type} has {remaining}s left.")
            return False
            
        return True

    def trigger(self, alert_type: str, bpm: float = None, custom_message: str = None) -> bool:
        """Trigger an OS notification if cooldown allows."""
        if not self.can_notify(alert_type):
            return False

        title = "Time to Rest Your Eyes"
        
        if alert_type == "low_bpm":
            title = "Time to Rest Your Eyes"
            msg = f"Your blink rate has dropped to {int(bpm) if bpm else 10}/min. Look at something 20 feet away for 20 seconds (20-20-20 rule) to refresh your tear film."
        elif alert_type == "critical_bpm":
            title = "CRITICAL: Extreme Eye Strain Risk"
            msg = f"Your blink rate is critically low ({int(bpm) if bpm else 4}/min) for over 3 minutes. Please close your eyes and take a screen break now!"
        elif alert_type == "break_reminder":
            title = "20-20-20 Break Time"
            msg = "20 minutes of screen time reached! Look at something 20 feet away for 20 seconds."
        else:
            title = "Eye Monitor Alert"
            msg = custom_message if custom_message else "Take a break to rest your eyes."

        logger.info(f"Firing OS Notification: [{title}] {msg}")

        # Update last notified time
        self.last_notified[alert_type] = time.time()

        # Primary: Native Windows notification via custom show_toast.py (always-on-top)
        import sys
        if sys.platform == "win32":
            try:
                import subprocess
                import os
                
                # Locate show_toast.py relative to notifier.py
                script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "show_toast.py")
                python_exe = sys.executable if sys.executable else "python"
                
                # Launch asynchronously as a separate background process
                subprocess.Popen(
                    [python_exe, script_path, title, msg],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                
                # Sound alert if enabled
                if self.sound_enabled:
                    import winsound
                    if alert_type == "critical_bpm":
                        winsound.Beep(1000, 200)
                        winsound.Beep(1000, 200)
                    else:
                        winsound.Beep(600, 200)
                return True
            except Exception as e:
                logger.error(f"Failed to show Windows OS custom notification: {e}")

        # Fallback 1: Tray icon notification callback
        if self.notify_callback:
            try:
                self.notify_callback(title, msg)
                if self.sound_enabled:
                    import winsound
                    if alert_type == "critical_bpm":
                        winsound.Beep(1000, 200)
                        winsound.Beep(1000, 200)
                    else:
                        winsound.Beep(600, 200)
                return True
            except Exception as e:
                logger.error(f"Failed via notify_callback: {e}")

        # Fallback 2: Plyer notification
        if HAS_PLYER:
            try:
                notification.notify(
                    title=title,
                    message=msg,
                    app_name="AI Eye Strain Monitor",
                    timeout=8,
                    toast=True
                )
                if self.sound_enabled:
                    import winsound
                    if alert_type == "critical_bpm":
                        winsound.Beep(1000, 200)
                        winsound.Beep(1000, 200)
                    else:
                        winsound.Beep(600, 200)
                return True
            except Exception as e:
                logger.error(f"Failed to show OS notification via plyer: {e}")
        else:
            logger.info("[Mock OS Notification] Plyer is unavailable.")
            if self.sound_enabled:
                try:
                    import winsound
                    winsound.Beep(600, 200)
                except ImportError:
                    pass
            return True
            
        return False
