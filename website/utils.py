from datetime import datetime
import pytz
from .models import TimeFormat, SiteSettings

def format_time(time_obj):
    """Format a time object according to the site's time format setting"""
    if not time_obj:
        return ""

    try:
        time_format = TimeFormat.get_format()
        if time_format == '12h':
            if isinstance(time_obj, datetime):
                return time_obj.strftime('%I:%M %p')
            else:
                # Handle time objects
                return datetime.strptime(f"{time_obj.hour}:{time_obj.minute}", "%H:%M").strftime('%I:%M %p')
        else:
            # Default to 24h format
            if isinstance(time_obj, datetime):
                return time_obj.strftime('%H:%M')
            else:
                return f"{time_obj.hour:02d}:{time_obj.minute:02d}"
    except Exception:
        # If any error occurs, default to 24h format
        if isinstance(time_obj, datetime):
            return time_obj.strftime('%H:%M')
        else:
            return f"{time_obj.hour:02d}:{time_obj.minute:02d}"

def format_time_for_timezone(timestamp, user_timezone, time_format='12h'):
    """
    Format a timestamp for a specific user timezone using Python/pytz.

    Args:
        timestamp: datetime object to format
        user_timezone: User's timezone string (e.g., 'Asia/Karachi')
        time_format: Time format ('12h' or '24h')

    Returns:
        Formatted time string in user's timezone
    """
    try:
        # Convert to user's timezone
        timezone = pytz.timezone(user_timezone)

        # If timestamp is naive, assume it's UTC
        if timestamp.tzinfo is None:
            timestamp = pytz.UTC.localize(timestamp)

        # Convert to user's timezone
        local_time = timestamp.astimezone(timezone)

        # Format according to preference
        if time_format == '12h':
            return local_time.strftime('%I:%M %p')
        else:
            return local_time.strftime('%H:%M')

    except Exception as e:
        print(f"Error formatting time for timezone {user_timezone}: {e}")
        # Fallback to UTC formatting
        if time_format == '12h':
            return timestamp.strftime('%I:%M %p')
        else:
            return timestamp.strftime('%H:%M')

def get_current_time_in_timezone(timezone_str):
    """
    Get current time in a specific timezone.

    Args:
        timezone_str: Timezone string (e.g., 'Asia/Karachi')

    Returns:
        Current datetime in the specified timezone
    """
    try:
        timezone = pytz.timezone(timezone_str)
        return datetime.now(timezone)
    except Exception as e:
        print(f"Error getting current time for timezone {timezone_str}: {e}")
        return datetime.utcnow()

def detect_user_timezone_from_request(request):
    """
    Detect user timezone from request headers or IP geolocation.
    For now, we'll use a simple approach and can enhance later.

    Args:
        request: Flask request object

    Returns:
        Detected timezone string or None
    """
    # Check if timezone is provided in headers
    user_timezone = request.headers.get('X-User-Timezone')
    if user_timezone:
        try:
            # Validate timezone
            pytz.timezone(user_timezone)
            return user_timezone
        except:
            pass

    # For now, return None and let frontend handle detection
    # In the future, we could add IP-based geolocation here
    return None