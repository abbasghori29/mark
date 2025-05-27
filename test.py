import os
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import uuid

# Define OAuth scopes
SCOPES = ['https://www.googleapis.com/auth/calendar']

# File to store the token
TOKEN_FILE = 'token.json'
CREDENTIALS_FILE = 'credentials.json'

def get_credentials():
    """Load or generate OAuth credentials, reusing token if available."""
    creds = None
    # Load token if it exists
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)
    
    # If no valid credentials, refresh or authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for future runs
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)
    
    return creds

def generate_meet_link(meeting_title="Automated Meeting", minutes_from_now=10, duration_minutes=30):
    """Generate a Google Meet link by creating a Calendar event."""
    # Get credentials
    creds = get_credentials()
    
    # Build the Calendar API service
    service = build('calendar', 'v3', credentials=creds)
    
    # Define event details
    start_time = datetime.now() + timedelta(minutes=minutes_from_now)
    end_time = start_time + timedelta(minutes=duration_minutes)
    
    event = {
        'summary': meeting_title,
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': 'UTC',
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'UTC',
        },
        'conferenceData': {
            'createRequest': {
                'requestId': str(uuid.uuid4()),  # Unique ID for the request
                'conferenceSolutionKey': {'type': 'hangoutsMeet'}
            }
        }
    }
    
    # Create the event
    event = service.events().insert(
        calendarId='primary',
        body=event,
        conferenceDataVersion=1
    ).execute()
    
    # Return the Google Meet link
    return event.get('hangoutLink')

# Example usage
if __name__ == '__main__':
    try:
        meet_link = generate_meet_link(
            meeting_title="Test Meeting",
            minutes_from_now=10,
            duration_minutes=30
        )
        print(f'Google Meet Link: {meet_link}')
    except Exception as e:
        print(f'Error: {e}')