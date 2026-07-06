import os
import re
from datetime import datetime, timezone
import logging

logger = logging.getLogger("EyeMonitor.CalendarDND")

class CalendarDND:
    def __init__(self, ics_path_or_url: str = None):
        self.ics_path = ics_path_or_url
        self.events = []
        self.last_load_time = None
        
    def set_ics_path(self, path: str):
        """Set the path to the local .ics file."""
        self.ics_path = path
        self.events = []
        self.last_load_time = None

    def _parse_ics_date(self, date_str: str) -> datetime:
        """Parse ics format datetime (e.g. 20260706T120000Z or 20260706T120000)."""
        # Remove any parameter properties (like TZID)
        date_str = date_str.split(":")[-1].strip()
        
        # Format: YYYYMMDDTHHMMSS
        is_utc = date_str.endswith("Z")
        clean_str = date_str.replace("Z", "")
        
        try:
            # Parse core datetime
            dt = datetime.strptime(clean_str[:15], "%Y%m%dT%H%M%S")
            if is_utc:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                # Default to local system timezone if no timezone info is parsed
                dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return dt
        except ValueError as e:
            logger.debug(f"Failed to parse date string {date_str}: {e}")
            return None

    def load_events(self):
        """Parse events from the local .ics file."""
        if not self.ics_path or not os.path.exists(self.ics_path):
            self.events = []
            return
            
        try:
            with open(self.ics_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                
            # Find all VEVENT blocks
            vevents = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", content, re.DOTALL)
            parsed_events = []
            
            for event_block in vevents:
                summary = "Meeting"
                dtstart_raw = None
                dtend_raw = None
                
                for line in event_block.splitlines():
                    line = line.strip()
                    if line.startswith("SUMMARY:"):
                        summary = line[8:]
                    elif line.startswith("SUMMARY;"):
                        # Handle fields with properties
                        parts = line.split(":", 1)
                        if len(parts) > 1:
                            summary = parts[1]
                    elif line.startswith("DTSTART"):
                        dtstart_raw = line
                    elif line.startswith("DTEND"):
                        dtend_raw = line
                
                if dtstart_raw and dtend_raw:
                    start_dt = self._parse_ics_date(dtstart_raw)
                    end_dt = self._parse_ics_date(dtend_raw)
                    if start_dt and end_dt:
                        parsed_events.append({
                            "summary": summary,
                            "start": start_dt,
                            "end": end_dt
                        })
                        
            self.events = parsed_events
            self.last_load_time = datetime.now()
            logger.info(f"Loaded {len(self.events)} events from calendar.")
        except Exception as e:
            logger.error(f"Error reading calendar file: {e}")
            self.events = []

    def is_in_meeting(self) -> tuple[bool, str]:
        """Check if current time is inside a calendar meeting. Returns (is_in_meeting, meeting_title)."""
        if not self.ics_path:
            return False, ""
            
        # Reload calendar if it has never been loaded or was loaded > 10 mins ago
        if not self.last_load_time or (datetime.now() - self.last_load_time).total_seconds() > 600:
            self.load_events()
            
        now = datetime.now(timezone.utc)
        for event in self.events:
            # Standardize event times to UTC for comparison
            start_utc = event["start"].astimezone(timezone.utc)
            end_utc = event["end"].astimezone(timezone.utc)
            
            if start_utc <= now <= end_utc:
                return True, event["summary"]
                
        return False, ""
