from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, json, current_app, abort, send_from_directory, Response, g
from flask_login import login_required, current_user
from flask_socketio import join_room, leave_room, emit
from datetime import datetime, timedelta, time
from .models import Room, Message, Admin, Visitor, BusinessHours, SiteSettings, QuickResponse, TimeFormat
from . import db, socketio, template_cache, data_cache, cached_view
from .ai_service import ai_service
from better_profanity import profanity
import pytz
import traceback
from sqlalchemy import desc
import requests, bleach, os, time as time_module
from werkzeug.utils import secure_filename
import uuid
from sqlalchemy.exc import SQLAlchemyError
import base64
import pickle
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import random
import string
import logging
from urllib.parse import urlparse
import functools
import threading
import time as time_module
from .utils import format_time, format_time_for_timezone, get_current_time_in_timezone

# Configure profanity filter to use default wordlist but not censor everything
profanity.load_censor_words()

views = Blueprint('views', __name__)

# Global dictionary to track active visitor connections
active_connections = {}

# Global dictionary to track active admin presence in rooms
# Format: {room_id: {admin_id: sid}}
active_admin_rooms = {}

# Connection management for high concurrency
connection_stats = {
    'total_connections': 0,
    'active_connections': 0,
    'peak_connections': 0,
    'connection_rate': 0,
    'last_reset': time_module.time()
}

# Rate limiting for connections
connection_rate_limit = {}  # ip_address -> {'count': int, 'window_start': timestamp}
RATE_LIMIT_WINDOW = 60  # 1 minute window
RATE_LIMIT_MAX_CONNECTIONS = 10  # Max connections per IP per minute

# Connection pool management
MAX_CONCURRENT_CONNECTIONS = 1000  # Maximum total concurrent connections
CONNECTION_CLEANUP_INTERVAL = 300  # 5 minutes

# AUTO-CLEANUP: Configuration for automatic chat removal
INACTIVITY_TIMEOUT_MINUTES = 10  # Remove chat after 10 minutes of inactivity
CLEANUP_CHECK_INTERVAL_SECONDS = 60  # Check every minute

# Flag to control the cleanup thread
cleanup_thread_running = False

def auto_cleanup_inactive_chats():
    """
    Background thread function to automatically remove inactive chats after 10 minutes.
    Runs every minute to check for inactive users.
    """
    global cleanup_thread_running
    cleanup_thread_running = True

    print("🧹 Auto-cleanup thread started - checking for inactive chats every minute")

    while cleanup_thread_running:
        try:
            current_time = datetime.now()
            inactive_visitors = []

            # Check each active connection for inactivity
            for visitor_id, data in list(active_connections.items()):
                last_inactive_time = data.get('last_inactive')
                is_active = data.get('active', True)
                room_id = data.get('room_id')

                # Only check visitors who are marked as inactive and have a last_inactive timestamp
                if not is_active and last_inactive_time and room_id:
                    # Calculate how long they've been inactive
                    inactive_duration = current_time - last_inactive_time
                    inactive_minutes = inactive_duration.total_seconds() / 60

                    print(f"🕐 Visitor {visitor_id} inactive for {inactive_minutes:.1f} minutes")

                    # If inactive for more than 10 minutes, mark for removal
                    if inactive_minutes >= INACTIVITY_TIMEOUT_MINUTES:
                        inactive_visitors.append({
                            'visitor_id': visitor_id,
                            'room_id': room_id,
                            'inactive_minutes': inactive_minutes
                        })

            # Remove inactive visitors
            for visitor_info in inactive_visitors:
                visitor_id = visitor_info['visitor_id']
                room_id = visitor_info['room_id']
                inactive_minutes = visitor_info['inactive_minutes']

                print(f"🗑️  Auto-removing inactive chat: Visitor {visitor_id} (inactive for {inactive_minutes:.1f} minutes)")

                # Remove from active_connections
                if visitor_id in active_connections:
                    del active_connections[visitor_id]

                # Mark room as inactive in database
                try:
                    room = Room.query.get(room_id)
                    if room:
                        room.is_active = False
                        room.last_activity = current_time
                        db.session.commit()
                        print(f"✅ Room {room_id} marked as inactive in database")
                except Exception as e:
                    print(f"❌ Error updating room {room_id} in database: {e}")
                    db.session.rollback()

                # Notify admins about auto-removal
                try:
                    socketio.emit('visitor_auto_removed', {
                        'visitor_id': visitor_id,
                        'room_id': room_id,
                        'reason': 'inactivity_timeout',
                        'inactive_minutes': round(inactive_minutes, 1),
                        'timestamp': current_time.strftime('%H:%M:%S')
                    }, namespace='/')

                    print(f"📢 Notified admins about auto-removal of visitor {visitor_id}")
                except Exception as e:
                    print(f"❌ Error notifying admins about auto-removal: {e}")

            if inactive_visitors:
                print(f"🧹 Auto-cleanup completed: Removed {len(inactive_visitors)} inactive chats")

        except Exception as e:
            print(f"❌ Error in auto-cleanup thread: {e}")
            import traceback
            traceback.print_exc()

        # Wait for the next check interval
        time_module.sleep(CLEANUP_CHECK_INTERVAL_SECONDS)

    print("🛑 Auto-cleanup thread stopped")

def start_auto_cleanup_thread():
    """Start the auto-cleanup background thread if not already running"""
    global cleanup_thread_running

    if not cleanup_thread_running:
        cleanup_thread = threading.Thread(target=auto_cleanup_inactive_chats, daemon=True)
        cleanup_thread.start()
        print("🚀 Auto-cleanup thread started")
    else:
        print("ℹ️  Auto-cleanup thread already running")

def check_rate_limit(ip_address):
    """Check if IP address is within rate limits for connections"""
    current_time = time_module.time()

    # Clean up old entries
    for ip in list(connection_rate_limit.keys()):
        if current_time - connection_rate_limit[ip]['window_start'] > RATE_LIMIT_WINDOW:
            del connection_rate_limit[ip]

    # Check current IP
    if ip_address not in connection_rate_limit:
        connection_rate_limit[ip_address] = {
            'count': 1,
            'window_start': current_time
        }
        return True

    # Check if within rate limit
    if connection_rate_limit[ip_address]['count'] < RATE_LIMIT_MAX_CONNECTIONS:
        connection_rate_limit[ip_address]['count'] += 1
        return True

    return False

def update_connection_stats(connected=True):
    """Update global connection statistics"""
    global connection_stats

    current_time = time_module.time()

    if connected:
        connection_stats['total_connections'] += 1
        connection_stats['active_connections'] += 1

        # Update peak connections
        if connection_stats['active_connections'] > connection_stats['peak_connections']:
            connection_stats['peak_connections'] = connection_stats['active_connections']
    else:
        connection_stats['active_connections'] = max(0, connection_stats['active_connections'] - 1)

    # Calculate connection rate (connections per minute)
    time_diff = current_time - connection_stats['last_reset']
    if time_diff >= 60:  # Reset every minute
        connection_stats['connection_rate'] = connection_stats['total_connections'] / (time_diff / 60)
        connection_stats['last_reset'] = current_time

def check_connection_limits():
    """Check if we're within connection limits"""
    return connection_stats['active_connections'] < MAX_CONCURRENT_CONNECTIONS

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

def generate_meet_link(meeting_title="Customer Support Meeting", minutes_from_now=5, duration_minutes=30):
    """Generate a Google Meet link by creating a Calendar event."""
    try:
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
    except Exception as e:
        print(f'Error generating Google Meet link: {e}')
        # Fallback to a placeholder link if API fails
        return f"https://meet.google.com/{uuid.uuid4().hex[:12]}"

@views.after_request
def add_hsts_header(response):
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
    return response

@socketio.on('connect')
def handle_connect():
    client_ip = request.environ.get('REMOTE_ADDR', 'unknown')
    print(f'Client connected: {request.sid} from IP: {client_ip}')

    # Check connection limits
    if not check_connection_limits():
        print(f'Connection limit exceeded. Rejecting connection from {client_ip}')
        socketio.emit('connection_rejected', {
            'reason': 'server_full',
            'message': 'Server is currently at capacity. Please try again later.'
        }, room=request.sid)
        return False

    # Check rate limiting
    if not check_rate_limit(client_ip):
        print(f'Rate limit exceeded for IP: {client_ip}')
        socketio.emit('connection_rejected', {
            'reason': 'rate_limit',
            'message': 'Too many connection attempts. Please wait before trying again.'
        }, room=request.sid)
        return False

    # Update connection statistics
    update_connection_stats(connected=True)

    # AUTO-CLEANUP: Start the auto-cleanup thread when first client connects
    try:
        start_auto_cleanup_thread()
        print(f"Auto-cleanup thread startup attempted for client: {request.sid}")
    except Exception as e:
        print(f"Error starting auto-cleanup thread: {e}")
        import traceback
        traceback.print_exc()

    # Send connection confirmation with server info
    socketio.emit('connection_confirmed', {
        'server_time': time_module.time(),
        'connection_id': request.sid,
        'max_connections': MAX_CONCURRENT_CONNECTIONS,
        'current_connections': connection_stats['active_connections']
    }, room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    print(f'Client disconnected: {request.sid}')

    # Update connection statistics
    update_connection_stats(connected=False)

    # Find if this was a visitor connection
    for visitor_id, data in list(active_connections.items()):
        if request.sid in data['sids']:
            # Remove this sid from the visitor's connection list
            active_connections[visitor_id]['sids'].remove(request.sid)

            # If no more connections for this visitor, notify admins
            if not active_connections[visitor_id]['sids']:
                room_id = active_connections[visitor_id].get('room_id')
                if room_id:
                    # Notify admins that visitor has disconnected
                    socketio.emit('visitor_disconnected', {
                        'visitor_id': visitor_id,
                        'room_id': room_id
                    }, to=None)

                    # Clean up tracking
                    del active_connections[visitor_id]

            break

    # Check if this was an admin connection
    from .models import Admin, Room
    from . import db

    # Try to find admin by session
    admin_id = None
    if hasattr(session, 'get') and session.get('_user_id'):
        admin_id = session.get('_user_id')

    if admin_id:
        admin = Admin.query.get(admin_id)
        if admin:
            # Set admin as offline
            admin.is_online = False
            admin.last_seen = datetime.utcnow()
            db.session.commit()

            # Check if this admin was in any rooms
            for room_id, admins in list(active_admin_rooms.items()):
                if admin_id in admins and admins[admin_id] == request.sid:
                    # Remove admin from this room
                    del active_admin_rooms[room_id][admin_id]

                    # If no more admins in this room, update the room's has_admin flag
                    if not active_admin_rooms[room_id]:
                        room = Room.query.get(room_id)
                        if room and room.has_admin:
                            room.has_admin = False
                            db.session.commit()

                            # Emit admin left event
                            socketio.emit('admin_left_chat', {
                                'room_id': room_id,
                                'admin_id': admin_id,
                                'admin_name': admin.name
                            }, to=None)

@socketio.on('join_room')
def handle_join_room(data):
    """Handle a user joining a room with session-based isolation"""
    room_id = data.get('room_id')
    browser_uuid = data.get('browser_uuid')
    stored_visitor_id = data.get('stored_visitor_id')
    stored_room_id = data.get('stored_room_id')

    # SESSION-BASED CHAT ISOLATION: Use browser UUID as primary session identifier
    if browser_uuid:
        current_session_id = browser_uuid  # Use browser UUID as session ID
    else:
        # Fallback: generate a unique session ID for this request
        current_session_id = str(uuid.uuid4())

    print(f"SocketIO join_room - Session ID: {current_session_id}")

    # SESSION-BASED ROOM VERIFICATION: Verify the room belongs to this session
    if room_id:
        room = Room.query.get(room_id)
        if room and room.session_id != current_session_id:
            print(f"Room {room_id} doesn't belong to session {current_session_id}, denying access")
            return

    # If we have a stored room ID, verify it belongs to this session
    if stored_room_id and stored_room_id != room_id:
        stored_room = Room.query.get(stored_room_id)
        if (stored_room and stored_room.is_active and
            stored_room.session_id == current_session_id):
            room_id = stored_room_id
            print(f"Using stored room ID: {room_id} for session: {current_session_id}")

    if room_id:
        join_room(room_id)
        print(f'Client {request.sid} joined room: {room_id} (session: {current_session_id})')

        # Update visitor record with browser UUID if available
        if browser_uuid:
            visitor_id = None

            # First try to find visitor by stored_visitor_id if available
            if stored_visitor_id:
                visitor = Visitor.query.filter_by(visitor_id=stored_visitor_id).first()
                if visitor:
                    visitor_id = visitor.visitor_id
                    # Update session with the correct visitor ID
                    if session:
                        session['visitor_id'] = visitor_id

            # If no visitor found by stored_visitor_id, get from session
            if not visitor_id and session and 'visitor_id' in session:
                visitor_id = session['visitor_id']

            # If we have a visitor ID, update the visitor record
            if visitor_id:
                visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
                if visitor:
                    # Update browser UUID if it's not set
                    if not visitor.browser_uuid:
                        visitor.browser_uuid = browser_uuid

                    # Update last_seen timestamp
                    visitor.last_seen = datetime.utcnow()
                    db.session.commit()

                    # Track this connection
                    if visitor_id not in active_connections:
                        active_connections[visitor_id] = {
                            'sids': [request.sid],
                            'room_id': room_id,
                            'active': True
                        }
                    else:
                        if request.sid not in active_connections[visitor_id]['sids']:
                            active_connections[visitor_id]['sids'].append(request.sid)
                        active_connections[visitor_id]['room_id'] = room_id
                        active_connections[visitor_id]['active'] = True

                    # Get room info to check if it's in human mode
                    room = Room.query.get(room_id)
                    if room and room.chat_mode == 'human':
                        # Notify admins about active visitor in human mode
                        socketio.emit('visitor_connected', {
                            'visitor_id': visitor_id,
                            'room_id': room_id,
                            'visitor_name': visitor.name,
                            'visitor_email': visitor.email,
                            'visitor_ip': visitor.ip_address,
                            'chat_mode': 'human'
                        }, to=None)
                else:
                    # Try to find visitor by browser UUID
                    visitor = Visitor.query.filter_by(browser_uuid=browser_uuid).first()
                    if visitor:
                        # Update session with correct visitor ID
                        if session:
                            session['visitor_id'] = visitor.visitor_id

        socketio.emit('room_joined', {
            'room_id': room_id
        }, room=request.sid)
    else:
        print(f'Error: Client {request.sid} tried to join room but no room_id provided')
        socketio.emit('error', {
            'message': 'No room ID provided'
        }, room=request.sid)

@socketio.on('join_admin_room')
def handle_join_admin_room(data):
    room_id = data.get('room_id')
    admin_id = data.get('admin_id')
    admin_name = data.get('admin_name', 'Admin')

    if room_id:
        # Make sure the admin is properly joined to the room
        join_room(room_id)
        print(f'Admin {admin_id} ({admin_name}) joined room: {room_id}')

        # Track this admin's presence in the room
        if room_id not in active_admin_rooms:
            active_admin_rooms[room_id] = {}
        active_admin_rooms[room_id][admin_id] = request.sid

        # Update the room's has_admin flag in the database
        room = Room.query.get(room_id)
        if room and not room.has_admin:
            room.has_admin = True
            db.session.commit()

        # Send a confirmation message to the admin
        socketio.emit('admin_joined', {
            'room_id': room_id,
            'message': 'You have successfully joined this chat room'
        }, room=request.sid)

        # Notify everyone in the room that an admin has joined (including the admin)
        socketio.emit('system_message', {
            'room_id': room_id,
            'content': f'Admin {admin_name} has joined the chat',
            'type': 'admin_joined',
            'admin_id': admin_id,
            'timestamp': datetime.utcnow().strftime('%H:%M')
        }, room=room_id)

        # Also emit admin_joined_chat event for dashboard updates
        socketio.emit('admin_joined_chat', {
            'room_id': room_id,
            'admin_id': admin_id,
            'admin_name': admin_name
        }, to=None)
    else:
        print(f'Error: Admin {admin_id} tried to join room but no room_id provided')
        socketio.emit('error', {
            'message': 'No room ID provided'
        }, room=request.sid)

@socketio.on('admin_leave_room')
def handle_admin_leave_room(data):
    room_id = data.get('room_id')
    admin_id = data.get('admin_id')
    admin_name = data.get('admin_name', 'Admin')

    if room_id and admin_id:
        # Remove admin from the room
        leave_room(room_id)
        print(f'Admin {admin_id} ({admin_name}) left room: {room_id}')

        # Remove this admin from our tracking
        if room_id in active_admin_rooms and admin_id in active_admin_rooms[room_id]:
            del active_admin_rooms[room_id][admin_id]

            # If no more admins in this room, update the room's has_admin flag
            if not active_admin_rooms[room_id]:
                room = Room.query.get(room_id)
                if room and room.has_admin:
                    room.has_admin = False
                    db.session.commit()

                # Emit admin left event
                socketio.emit('admin_left_chat', {
                    'room_id': room_id,
                    'admin_id': admin_id,
                    'admin_name': admin_name
                }, to=None)

@socketio.on('leave_room')
def handle_leave_room(data):
    room_id = data['room_id']
    leave_room(room_id)
    print(f'Client left room: {room_id}')

@socketio.on("new_message")
def handle_new_message(data):
    if current_user.is_authenticated:
        room_id = data['room_id']
        user = current_user
        message = data['message']
        message = bleach.clean(message, tags=['img', 'strong', 'em', 'u', 'b', 'mark', 'del', 'sub', 'sup'], attributes=['src', 'alt'])
        message = profanity.censor(message)
        name = current_user.nickname
        recipient_id = data.get('recipient_id')  # Add this line to get the recipient id from the client
        if message.startswith("/"):
            room = Room.query.get(room_id)  # Fetch the room object
            response_message = handle_command(message, room)
            img = "https://cdn.pixabay.com/photo/2018/11/13/21/43/avatar-3814049_1280.png"
            emit('message', {'msg': response_message, 'user': 'System', 'id': user.id, 'time_sent': "System", 'img': img}, room=room_id)
        elif recipient_id:
            # Handle private messages
            db_private_message = PrivateMessage(data=message, sender_id=user.id, recipient_id=recipient_id)
            db.session.add(db_private_message)
            db.session.commit()
        else:
            # Handle regular chat messages
            db_message = Message(data=message, user_id=user.id, room_id=room_id)
            # Add the message to the database
            db.session.add(db_message)
            db.session.commit()
            print(f"New message from {name} in room {room_id}: {str(message).encode('utf-8')}")
            time_sent = str(db_message.timestamp.strftime("%H:%M"))  # Format the timestamp nicely
            if not current_user.img:
                img = "https://cdn.pixabay.com/photo/2018/11/13/21/43/avatar-3814049_1280.png"
            else:
                img = current_user.img
            emit('message', {'msg': message, 'user': user.nickname, 'id': user.id, 'time_sent': time_sent, 'img': img}, room=room_id)  # Newly added 'time_sent' field to the output
    else:
        print("Error sending message")

@socketio.on("visitor_message")
def handle_visitor_message(data):
    room_id = data['room_id']
    visitor_id = data['visitor_id']
    browser_uuid = data.get('browser_uuid')
    message_content = data['message']

    # Sanitize the message
    message_content = bleach.clean(message_content, tags=['b', 'i', 'u'], strip=True)

    # Only censor actual profanity, not the entire message
    # The issue is that profanity.censor() is replacing all text with asterisks
    # We'll configure it to only replace actual profane words
    censored_message = profanity.censor(message_content)

    # Find the visitor by browser UUID first if available
    visitor = None
    if browser_uuid:
        visitor = Visitor.query.filter_by(browser_uuid=browser_uuid).first()

        # If found by browser UUID but visitor_id doesn't match, use the one from the database
        if visitor and visitor.visitor_id != visitor_id:
            visitor_id = visitor.visitor_id

    # If not found by browser UUID, try by visitor_id
    if not visitor and visitor_id:
        visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()

        # Update browser_uuid if available and not set
        if visitor and browser_uuid and not visitor.browser_uuid:
            visitor.browser_uuid = browser_uuid
            db.session.commit()

    # Update room activity timestamp
    room = Room.query.get(room_id)
    if room:
        room.last_activity = datetime.utcnow()

        # Store the message in the database
        message = Message(
            content=censored_message,
            room_id=room_id,
            is_from_visitor=True,
            sender_id=visitor_id,
            is_read=False
        )
        db.session.add(message)
        db.session.commit()

        # Format the time
        time_sent = format_time(message.timestamp)

        # Get visitor info for better display
        visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
        visitor_name = visitor.name if visitor and visitor.name else 'Visitor'
        visitor_ip = visitor.ip_address if visitor else 'Unknown'
        visitor_display_name = visitor_name if visitor_name != 'Visitor' else f"Visitor ({visitor_ip})"

        # Debug info
        print(f"Visitor message from {visitor_name} in room {room_id}: {censored_message}")

        # Emit to the visitor (with "You" as sender name)
        socketio.emit('new_message', {
            'room_id': room_id,
            'content': censored_message,
            'sender_name': 'You',
            'sender_id': visitor_id,
            'time_sent': time_sent,
            'timestamp': message.timestamp.isoformat(),  # Add ISO timestamp for frontend
            'is_from_visitor': True,
            'id': message.id
        }, room=request.sid)

        # Instead of using client_sid loops, broadcast directly to the room
        # This ensures all clients in the room (including admins) receive the message
        socketio.emit('new_message', {
            'room_id': room_id,
            'content': censored_message,
            'sender_name': visitor_name,
            'sender_id': visitor_id,
            'time_sent': time_sent,
            'timestamp': message.timestamp.isoformat(),  # Add ISO timestamp for frontend
            'is_from_visitor': True,
            'id': message.id
        }, room=room_id, skip_sid=request.sid)  # Skip the sender

        # Emit notification to all admins for real-time dashboard updates
        # BUT ONLY if the chat is in human mode (direct admin communication)
        # Don't notify admins when users are talking to AI
        if room.chat_mode == 'human':
            socketio.emit('new_message_notification', {
                'room_id': room_id,
                'visitor_id': visitor_id,
                'visitor_name': visitor_display_name,
                'message': censored_message,
                'is_from_visitor': True,
                'timestamp': datetime.utcnow().isoformat()
            }, namespace='/')

        # Only respond with AI if chat mode is AI and there's no human admin in the chat yet
        # This fixes the issue of AI responding when in human mode
        if room.chat_mode == 'ai':
            # Get conversation history
            conversation_history = Message.query.filter_by(room_id=room_id).order_by(Message.timestamp).all()
            history = [{'content': msg.content, 'is_from_visitor': msg.is_from_visitor} for msg in conversation_history]

            # Generate AI response with room_id for typing indicator
            ai_response = ai_service.get_ai_response(censored_message, history, room_id=room_id)

            # Store AI response in database
            ai_message = Message(
                content=ai_response,
                room_id=room_id,
                is_from_visitor=False,
                sender_name="AI Assistant",
                is_read=True,
                is_ai_generated=True
            )
            db.session.add(ai_message)
            db.session.commit()

            # Format the time
            ai_time_sent = format_time(ai_message.timestamp)

            # Emit AI response to the room
            socketio.emit('new_message', {
                'room_id': room_id,
                'content': ai_response,
                'sender_name': 'AI Assistant',
                'sender_id': 'ai',
                'time_sent': ai_time_sent,
                'timestamp': ai_message.timestamp.isoformat(),  # Add ISO timestamp for frontend
                'is_from_visitor': False,
                'is_ai_generated': True
            }, room=room_id)

@socketio.on("admin_message")
def handle_admin_message(data):
    room_id = data.get('room_id')
    admin_id = data.get('admin_id')
    admin_name = data.get('admin_name')
    admin_profile_image = data.get('admin_profile_image', '')
    message_content = data.get('message', data.get('content', ''))  # Try both 'message' and 'content' keys

    # Check if we have the required data
    if not room_id or not admin_id or not message_content:
        print(f"Missing required data for admin message: {data}")
        socketio.emit('error', {
            'message': 'Missing required data for message'
        }, room=request.sid)
        return

    # If admin_name is missing or undefined, get it from the database
    if not admin_name or admin_name == 'undefined' or admin_name == 'null':
        admin = Admin.query.get(admin_id)
        if admin:
            admin_name = admin.name
        else:
            admin_name = "Admin"

    # Sanitize and clean the message
    message_content = bleach.clean(message_content, tags=['b', 'i', 'u'], strip=True)

    # Update room activity timestamp
    room = Room.query.get(room_id)
    if room:
        room.last_activity = datetime.utcnow()

        # Store the message in the database
        message = Message(
            content=message_content,
            room_id=room_id,
            is_from_visitor=False,
            sender_id=admin_id,
            sender_name=admin_name,
            is_read=True,  # Admin messages are always marked as read
            admin_profile_image=admin_profile_image
        )
        db.session.add(message)
        db.session.commit()

        # Emit the message to all clients in the room
        socketio.emit('new_message', {
            'room_id': room_id,
            'content': message_content,
            'is_from_visitor': False,
            'sender_id': admin_id,
            'sender_name': admin_name,
            'is_read': True,
            'time_sent': format_time(message.timestamp),
            'timestamp': message.timestamp.isoformat(),  # Add ISO timestamp for frontend
            'admin_profile_image': admin_profile_image
        }, room=room_id)
    else:
        # If room doesn't exist, notify the admin
        socketio.emit('error', {
            'message': 'Room not found'
        }, room=request.sid)

@socketio.on('update_visitor_info')
def handle_visitor_info(data):
    """Handle update of visitor information"""
    visitor_id = data.get('visitor_id')
    browser_uuid = data.get('browser_uuid')
    current_page = data.get('current_page')

    visitor = None

    # First try to find visitor by browser UUID if available
    if browser_uuid:
        visitor = Visitor.query.filter_by(browser_uuid=browser_uuid).first()

    # If not found by browser UUID, try by visitor_id
    if not visitor and visitor_id:
        visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()

    if visitor:
        visitor.last_seen = datetime.utcnow()
        visitor.current_page = current_page

        # Update browser_uuid if it's available and not set
        if browser_uuid and not visitor.browser_uuid:
            visitor.browser_uuid = browser_uuid

        # Find the visitor's active rooms
        rooms = Room.query.filter_by(visitor_id=visitor.visitor_id, is_active=True).all()
        for room in rooms:
            room.current_page = current_page

        db.session.commit()

@socketio.on('visitor_typing')
def handle_visitor_typing(data):
    """Handle notification that a visitor is typing"""
    room_id = data.get('room_id')
    visitor_id = data.get('visitor_id')

    if room_id and visitor_id:
        # Get visitor info for name
        visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
        visitor_name = None
        visitor_ip = None

        if visitor:
            visitor_name = visitor.name
            visitor_ip = visitor.ip_address

        # Broadcast to the room (admins will receive this)
        # Don't skip the sender to ensure consistent behavior
        socketio.emit('visitor_typing', {
            'room_id': room_id,
            'visitor_id': visitor_id,
            'visitor_name': visitor_name or f"Visitor ({visitor_ip})"
        }, room=room_id)  # Removed skip_sid parameter

@socketio.on('visitor_stopped_typing')
def handle_visitor_stopped_typing(data):
    """Handle notification that a visitor stopped typing"""
    room_id = data.get('room_id')
    visitor_id = data.get('visitor_id')

    if room_id and visitor_id:
        # Broadcast to the room (admins will receive this)
        # Don't skip the sender to ensure consistent behavior
        socketio.emit('visitor_stopped_typing', {
            'room_id': room_id,
            'visitor_id': visitor_id
        }, room=room_id)  # Removed skip_sid parameter

@socketio.on('admin_typing')
def handle_admin_typing(data):
    """Handle notification that an admin is typing"""
    room_id = data.get('room_id')
    admin_id = data.get('admin_id')
    admin_name = data.get('admin_name', 'Admin')
    admin_profile_image = data.get('admin_profile_image')
    timestamp = data.get('timestamp', datetime.utcnow().timestamp())

    print(f"Admin typing: room_id={room_id}, admin_id={admin_id}, admin_name={admin_name}, timestamp={timestamp}")

    if room_id and admin_id:
        # If admin profile image wasn't provided, try to get it from the database
        if not admin_profile_image:
            admin = Admin.query.get(admin_id)
            if admin and admin.profile_image:
                admin_profile_image = admin.profile_image

        # Broadcast to everyone in the room (including visitors)
        # Don't skip the sender's sid to ensure consistent behavior
        socketio.emit('admin_typing', {
            'room_id': room_id,
            'admin_id': admin_id,
            'admin_name': admin_name,
            'admin_profile_image': admin_profile_image,
            'timestamp': timestamp  # Pass along the timestamp to ensure uniqueness
        }, room=room_id)  # Removed skip_sid parameter to ensure all clients receive the event

        print(f"Emitted admin_typing event to room {room_id} with timestamp {timestamp}")

@socketio.on('admin_stopped_typing')
def handle_admin_stopped_typing(data):
    """Handle notification that an admin stopped typing"""
    room_id = data.get('room_id')
    admin_id = data.get('admin_id')
    timestamp = data.get('timestamp', datetime.utcnow().timestamp())

    print(f"Admin stopped typing: room_id={room_id}, admin_id={admin_id}, timestamp={timestamp}")

    if room_id and admin_id:
        # Broadcast to everyone in the room (including visitors)
        # Don't skip the sender's sid to ensure consistent behavior
        socketio.emit('admin_stopped_typing', {
            'room_id': room_id,
            'admin_id': admin_id,
            'timestamp': timestamp  # Pass along the timestamp to ensure uniqueness
        }, room=room_id)  # Removed skip_sid parameter

        print(f"Emitted admin_stopped_typing event to room {room_id} with timestamp {timestamp}")

@socketio.on('ai_typing')
def handle_ai_typing(data):
    """Handle notification that the AI is preparing a response"""
    room_id = data.get('room_id')

    if room_id:
        # Broadcast to everyone in the room
        socketio.emit('ai_typing', {
            'room_id': room_id
        }, room=room_id)

@socketio.on('ai_stopped_typing')
def handle_ai_stopped_typing(data):
    """Handle notification that the AI has completed its response"""
    room_id = data.get('room_id')

    if room_id:
        # Broadcast to everyone in the room
        socketio.emit('ai_stopped_typing', {
            'room_id': room_id
        }, room=room_id)

@views.route('/api/toggle_chat_mode', methods=['POST'])
def toggle_chat_mode_api():
    """API endpoint to toggle chat mode between human and AI"""
    data = request.json
    room_id = data.get('room_id')

    if not room_id:
        return jsonify({'success': False, 'error': 'Room ID is required'}), 400

    room = Room.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'error': 'Room not found'}), 404

    # Get the requested mode or toggle the current mode
    requested_mode = data.get('mode')
    new_mode = requested_mode if requested_mode else ('human' if room.chat_mode == 'ai' else 'ai')

    # If switching to human mode, check if it's available
    if new_mode == 'human':
        # Check if any admins are online
        online_admins = Admin.query.filter_by(is_online=True).all()
        has_online_admins = len(online_admins) > 0

        # Check if we're within business hours
        within_business_hours = False
        try:
            # Get current time in site timezone
            from datetime import datetime
            import pytz

            # Get timezone from settings with safe fallback
            timezone_str = 'UTC'
            try:
                timezone_str = SiteSettings.get_setting('timezone', timezone_str)
            except:
                pass

            try:
                timezone = pytz.timezone(timezone_str)
            except:
                timezone = pytz.UTC

            # Get current time in site timezone
            now = datetime.now(timezone)

            # Get day of week (0-6, Monday-Sunday)
            day_of_week = now.weekday()

            # Check if today is a business day
            business_hours_today = BusinessHours.query.filter_by(
                day_of_week=day_of_week,
                is_active=True
            ).first()

            if business_hours_today:
                # Convert business hours to timezone-aware times
                start_time = datetime.combine(now.date(), business_hours_today.start_time)
                start_time = timezone.localize(start_time) if hasattr(timezone, 'localize') else start_time.replace(tzinfo=timezone)

                end_time = datetime.combine(now.date(), business_hours_today.end_time)
                end_time = timezone.localize(end_time) if hasattr(timezone, 'localize') else end_time.replace(tzinfo=timezone)

                # Compare full datetime objects instead of just time components
                within_business_hours = (start_time <= now <= end_time)
        except Exception as e:
            # Log error but continue
            current_app.logger.error(f"Error checking business hours: {str(e)}")

        # Human mode is available only if admins are online AND we're within business hours
        human_mode_available = has_online_admins and within_business_hours

        if not human_mode_available:
            # Determine the reason
            reason = None
            if not has_online_admins and not within_business_hours:
                reason = "No admin is available and outside of business hours"
            elif not has_online_admins:
                reason = "No admin is available right now"
            elif not within_business_hours:
                reason = "Outside of business hours"

            return jsonify({
                'success': False,
                'error': reason or "Human mode is not available",
                'current_mode': room.chat_mode
            }), 400

    # Update the chat mode
    room.chat_mode = new_mode

    # If switching to human mode, notify admins
    if new_mode == 'human':
        # Check if this visitor is in active_connections
        visitor_id = room.visitor_id
        if visitor_id in active_connections and active_connections[visitor_id]['sids']:
            # Get visitor information from database
            visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
            visitor_name = visitor.name if visitor else None
            visitor_email = visitor.email if visitor else None

            # Emit event to notify admins about this human mode chat
            socketio.emit('visitor_connected', {
                'visitor_id': visitor_id,
                'room_id': room_id,
                'visitor_name': visitor_name,
                'visitor_email': visitor_email,
                'visitor_ip': room.visitor_ip,
                'chat_mode': new_mode
            }, to=None)
    else:
        # If switching to AI mode, notify admins to remove from dashboard
        socketio.emit('visitor_disconnected', {
            'visitor_id': room.visitor_id,
            'room_id': room_id
        }, to=None)

    # Create a system message about the mode change
    message = Message(
        content=f"Chat mode changed to {new_mode.upper()} ({new_mode.capitalize()} {'Assistant' if new_mode == 'ai' else 'Support'})",
        room_id=room_id,
        is_from_visitor=False,
        sender_name="System",
        is_read=True,
        is_system_message=True
    )
    db.session.add(message)
    db.session.commit()

    # Emit system message to all clients in the room
    from datetime import datetime
    socketio.emit('system_message', {
        'room_id': room_id,
        'content': message.content,
        'type': 'mode_change',
        'timestamp': message.timestamp.isoformat()  # Use ISO timestamp for frontend
    }, room=room_id)

    return jsonify({
        'success': True,
        'new_mode': new_mode
    })

@views.route('/api/format_time_for_timezone', methods=['POST'])
def format_time_for_timezone_api():
    """API endpoint to format timestamps for user's timezone using Python/pytz"""
    data = request.json

    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400

    timestamp_str = data.get('timestamp')
    user_timezone = data.get('timezone')
    time_format = data.get('format', '12h')

    if not timestamp_str or not user_timezone:
        return jsonify({'success': False, 'error': 'Timestamp and timezone are required'}), 400

    try:
        # Parse the timestamp
        if timestamp_str.endswith('Z'):
            # ISO format with Z
            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        else:
            # Try to parse as ISO format
            timestamp = datetime.fromisoformat(timestamp_str)

        # Format using our Python function
        formatted_time = format_time_for_timezone(timestamp, user_timezone, time_format)

        return jsonify({
            'success': True,
            'formatted_time': formatted_time,
            'timezone': user_timezone
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error formatting timestamp: {str(e)}'
        }), 400

@views.route('/api/get_current_time', methods=['POST'])
def get_current_time_api():
    """API endpoint to get current time in user's timezone"""
    data = request.json

    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400

    user_timezone = data.get('timezone')
    time_format = data.get('format', '12h')

    if not user_timezone:
        return jsonify({'success': False, 'error': 'Timezone is required'}), 400

    try:
        # Get current time in user's timezone
        current_time = get_current_time_in_timezone(user_timezone)

        # Format it
        formatted_time = format_time_for_timezone(current_time, user_timezone, time_format)

        return jsonify({
            'success': True,
            'current_time': formatted_time,
            'timezone': user_timezone,
            'iso_timestamp': current_time.isoformat()
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error getting current time: {str(e)}'
        }), 400

#########################################################
@views.route('/')
@cached_view(timeout=60)  # Cache the homepage for 1 minute
def index():
    # Generate a unique ID for the visitor if they don't have one
    if 'visitor_id' not in session:
        # Check for stored visitor ID in cookies
        stored_visitor_id = request.cookies.get('visitor_id')
        if stored_visitor_id:
            session['visitor_id'] = stored_visitor_id
        else:
            session['visitor_id'] = str(uuid.uuid4())

    # Get the visitor's IP address and session ID
    visitor_ip = request.remote_addr
    visitor_id = session['visitor_id']
    user_agent = request.headers.get('User-Agent')

    # Get browser UUID from headers if available
    browser_uuid = request.headers.get('X-Browser-UUID')

    # SESSION-BASED CHAT ISOLATION: Use browser UUID as primary session identifier
    if browser_uuid:
        session_id = browser_uuid  # Use browser UUID as session ID
    else:
        # Fallback: generate a unique session ID for this request
        session_id = str(uuid.uuid4())

    print(f"Index route - Session ID: {session_id}")

    # Update or create visitor record
    visitor = None

    # First, try to find visitor by browser UUID if available
    if browser_uuid:
        visitor = Visitor.query.filter_by(browser_uuid=browser_uuid).first()

    # If not found by browser UUID, try by email (if available in session)
    visitor_email = session.get('visitor_email')
    if not visitor and visitor_email:
        visitor = Visitor.query.filter_by(email=visitor_email).first()

    # If not found by browser UUID, try by visitor_id
    if not visitor:
        visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()

    # If visitor found, update their record
    if visitor:
        visitor.last_seen = datetime.utcnow()
        visitor.visit_count += 1
        visitor.ip_address = visitor_ip
        visitor.user_agent = user_agent
        visitor.session_id = session_id

        # Update browser_uuid if it's available and not set
        if browser_uuid and not visitor.browser_uuid:
            visitor.browser_uuid = browser_uuid

        # Ensure session has the correct visitor_id and email
        if visitor.visitor_id != visitor_id:
            session['visitor_id'] = visitor.visitor_id
            visitor_id = visitor.visitor_id

        if visitor.email:
            session['visitor_email'] = visitor.email
    else:
        # Create a new visitor record - don't try to match by IP
        visitor = Visitor(
            visitor_id=visitor_id,
            ip_address=visitor_ip,
            user_agent=user_agent,
            browser_uuid=browser_uuid,
            session_id=session_id
        )
        db.session.add(visitor)

    db.session.commit()

    # Check for stored room ID in cookies
    stored_room_id = request.cookies.get('room_id')
    existing_room = None

    # First try to find room by stored room ID
    if stored_room_id:
        existing_room = Room.query.filter_by(id=stored_room_id, is_active=True).first()
        # Make sure this room belongs to our visitor
        if existing_room and existing_room.visitor_id != visitor_id:
            existing_room = None

    # SESSION-BASED ROOM ISOLATION: Look for room by session ID first
    if not existing_room:
        existing_room = Room.query.filter_by(
            visitor_id=visitor_id,
            session_id=session_id,
            is_active=True
        ).first()

    if not existing_room:
        new_room = Room(
            visitor_id=visitor_id,
            visitor_ip=visitor_ip,
            session_id=session_id
        )
        db.session.add(new_room)
        db.session.commit()

        # Emit new chat notification to all admins
        socketio.emit('new_chat', {
            'room_id': new_room.id,
            'visitor_ip': visitor_ip,
            'session_id': session_id,
            'timestamp': datetime.utcnow().isoformat()
        }, namespace='/')

    # Create response with the blank page
    response = Response(render_template('blank.html', title='Welcome'))

    # Set cookies for visitor_id and room_id if we have a room
    if existing_room:
        response.set_cookie('visitor_id', visitor_id, max_age=60*60*24*365)  # 1 year
        response.set_cookie('room_id', existing_room.id, max_age=60*60*24*365)  # 1 year
    elif 'new_room' in locals():
        response.set_cookie('visitor_id', visitor_id, max_age=60*60*24*365)  # 1 year
        response.set_cookie('room_id', new_room.id, max_age=60*60*24*365)  # 1 year

    return response


@views.route('/dashboard')
@login_required
def dashboard():
    public_rooms = get_public_rooms()
    my_rooms = current_user.rooms
    return render_template('dashboard.html', user=current_user, rooms=get_rooms_count(), public_rooms=public_rooms, my_rooms=my_rooms)

@views.route('/chat/<string:room_id>', methods=['GET', 'POST'])
def chat(room_id):
    # Check if the room exists
    room = Room.query.get_or_404(room_id)

    # Get visitor ID from session
    visitor_id = session.get('visitor_id')

    # Get browser UUID from headers if available
    browser_uuid = request.headers.get('X-Browser-UUID')

    # Get stored visitor ID and room ID from cookies if available
    stored_visitor_id = request.cookies.get('visitor_id')
    stored_room_id = request.cookies.get('room_id')

    visitor = None

    # First try to find visitor by stored_visitor_id if available
    if stored_visitor_id:
        visitor = Visitor.query.filter_by(visitor_id=stored_visitor_id).first()
        if visitor:
            # Update session with the correct visitor ID
            session['visitor_id'] = visitor.visitor_id
            visitor_id = visitor.visitor_id

    # If not found by stored_visitor_id, try by browser UUID if available
    if not visitor and browser_uuid:
        visitor = Visitor.query.filter_by(browser_uuid=browser_uuid).first()
        if visitor:
            # Update session with the correct visitor ID
            session['visitor_id'] = visitor.visitor_id
            visitor_id = visitor.visitor_id
            if visitor.email:
                session['visitor_email'] = visitor.email

    # If not found by browser UUID, try by email if available in session
    visitor_email = session.get('visitor_email')
    if not visitor and visitor_email:
        visitor = Visitor.query.filter_by(email=visitor_email).first()
        if visitor:
            session['visitor_id'] = visitor.visitor_id
            visitor_id = visitor.visitor_id

    # If not found by browser UUID or email, try by visitor_id
    if not visitor and visitor_id:
        visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()

    # If still not found, or visitor ID doesn't match the room's visitor ID
    if not visitor or room.visitor_id != visitor_id:
        # If this is a valid room, update the session with the correct visitor ID
        session['visitor_id'] = room.visitor_id
        visitor_id = room.visitor_id

    # Get visitor information
        visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
        if not visitor:
            visitor = Visitor(
                visitor_id=visitor_id,
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent'),
                browser_uuid=browser_uuid,
                session_id=session.get('session_id', str(uuid.uuid4()))
            )
            db.session.add(visitor)
            db.session.commit()
    else:
        # Update visitor information
        visitor.last_seen = datetime.utcnow()
        visitor.ip_address = request.remote_addr
        visitor.user_agent = request.headers.get('User-Agent')

        # Update browser_uuid if it's available and not set
        if browser_uuid and not visitor.browser_uuid:
            visitor.browser_uuid = browser_uuid
            db.session.commit()

    # Get all messages for the room
    messages = Message.query.filter_by(room_id=room_id).order_by(Message.timestamp).all()

    # Check if there are any online admins
    online_admins = Admin.query.filter_by(is_online=True).count()
    has_online_admins = online_admins > 0

    # Get timezone from settings
    timezone_str = SiteSettings.get_setting('timezone', 'UTC')
    try:
        import pytz
        timezone = pytz.timezone(timezone_str)
    except (ImportError, pytz.exceptions.UnknownTimeZoneError):
        from datetime import timezone as tz
        timezone = tz.utc

    # Get time format from settings
    try:
        from .models import TimeFormat
        time_format = TimeFormat.get_format()
    except:
        time_format = '24h'  # Default to 24h format

    # Check if we're within business hours
    now = datetime.now(timezone)
    day_of_week = now.weekday()  # 0-6 (Monday-Sunday)
    current_time = now.time()

    business_hours_today = BusinessHours.query.filter_by(
        day_of_week=day_of_week,
        is_active=True
    ).first()

    within_business_hours = False
    if business_hours_today:
        # Convert business hours to timezone-aware times
        start_time = datetime.combine(now.date(), business_hours_today.start_time)
        start_time = timezone.localize(start_time) if hasattr(timezone, 'localize') else start_time.replace(tzinfo=timezone)

        end_time = datetime.combine(now.date(), business_hours_today.end_time)
        end_time = timezone.localize(end_time) if hasattr(timezone, 'localize') else end_time.replace(tzinfo=timezone)

        # Compare full datetime objects instead of just time components
        within_business_hours = (start_time <= now <= end_time)

        # Add debug logging
        current_app.logger.info(f"Business hours check: start={start_time}, now={now}, end={end_time}, within={within_business_hours}")

    # Get settings with safe defaults
    away_message = "We're currently closed. Please leave a message and we'll get back to you during business hours."
    welcome_message = "Welcome to our customer support chat. How can we help you today?"
    ai_welcome_message = "Hello! I'm your AI assistant. How can I help you today?"
    company_name = "Our Company"

    try:
        # Try to get from SiteSettings but catch any database errors
        try:
            away_message = SiteSettings.get_setting('away_message', away_message)
            welcome_message = SiteSettings.get_setting('welcome_message', welcome_message)
            ai_welcome_message = SiteSettings.get_setting('ai_welcome_message', ai_welcome_message)
            company_name = SiteSettings.get_setting('company_name', company_name)
        except SQLAlchemyError:
            # Table likely doesn't exist yet, use default message
            pass
    except:
        # Any other errors, use default
        pass

    # Default to AI mode outside business hours or when no admins are online
    if not has_online_admins or not within_business_hours:
        room.chat_mode = 'ai'
        db.session.commit()

    # Create response with template
    response = Response(render_template('chat.html',
                          room=room,
                          messages=messages,
                          visitor_id=visitor_id,
                          visitor=visitor,
                          has_online_admins=has_online_admins,
                          within_business_hours=within_business_hours,
                          away_message=away_message,
                          welcome_message=welcome_message,
                          ai_welcome_message=ai_welcome_message,
                          company_name=company_name,
                          timezone=timezone_str,
                          time_format=time_format))

    # Set cookies for visitor_id and room_id
    response.set_cookie('visitor_id', visitor_id, max_age=60*60*24*365)  # 1 year
    response.set_cookie('room_id', room_id, max_age=60*60*24*365)  # 1 year

    return response

@views.route('/api/send_meeting_link', methods=['POST'])
@login_required
def send_meeting_link():
    """API endpoint to send a Google Meet link to a visitor"""
    data = request.json
    room_id = data.get('room_id')

    if not room_id:
        return jsonify({'success': False, 'error': 'Room ID is required'})

    room = Room.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'error': 'Room not found'})

    # Get visitor info for personalized meeting title
    visitor = Visitor.query.filter_by(visitor_id=room.visitor_id).first()
    visitor_name = visitor.name if visitor and visitor.name else "Customer"

    # Generate a Google Meet link using the Calendar API
    try:
        meeting_title = f"Support Meeting with {visitor_name}"
        meet_link = generate_meet_link(
            meeting_title=meeting_title,
            minutes_from_now=5,  # Meeting starts in 5 minutes
            duration_minutes=30   # 30 minute meeting
        )
    except Exception as e:
        print(f"Error generating Google Meet link: {e}")
        # Fallback to a placeholder link if API fails
        meet_link = f"https://meet.google.com/{uuid.uuid4().hex[:12]}"

    # Format the message with just the link - the UI will format it properly
    message_content = meet_link

    # Send the message to the room
    message = Message(
        content=message_content,
        room_id=room_id,
        is_from_visitor=False,
        sender_id=current_user.id,
        sender_name=current_user.name,
        is_read=True
    )
    db.session.add(message)
    db.session.commit()

    # Emit the message to the room
    socketio.emit('new_message', {
        'content': message_content,
        'sender_name': current_user.name,
        'sender_id': current_user.id,
        'time_sent': format_time(message.timestamp),
        'is_from_visitor': False,
        'room_id': room_id,
        'admin_profile_image': current_user.profile_image if hasattr(current_user, 'profile_image') else None
    }, room=room_id)

    return jsonify({'success': True, 'meet_link': meet_link})

@views.route('/chat_iframe/<string:room_id>', methods=['GET'])
def chat_iframe(room_id):
    """Route for the chat iframe that doesn't extend base.html"""
    # Check if the room exists
    room = Room.query.get_or_404(room_id)

    # Get visitor ID from session
    visitor_id = session.get('visitor_id')

    # Get browser UUID from headers if available
    browser_uuid = request.headers.get('X-Browser-UUID')

    # Get stored visitor ID and room ID from cookies if available
    stored_visitor_id = request.cookies.get('visitor_id')
    stored_room_id = request.cookies.get('room_id')

    visitor = None

    # First try to find visitor by stored_visitor_id if available
    if stored_visitor_id:
        visitor = Visitor.query.filter_by(visitor_id=stored_visitor_id).first()
        if visitor:
            # Update session with the correct visitor ID
            session['visitor_id'] = visitor.visitor_id
            visitor_id = visitor.visitor_id

    # If not found by stored_visitor_id, try by browser UUID if available
    if not visitor and browser_uuid:
        visitor = Visitor.query.filter_by(browser_uuid=browser_uuid).first()
        if visitor:
            # Update session with the correct visitor ID
            session['visitor_id'] = visitor.visitor_id
            visitor_id = visitor.visitor_id
            if visitor.email:
                session['visitor_email'] = visitor.email

    # If not found by browser UUID, try by email if available in session
    visitor_email = session.get('visitor_email')
    if not visitor and visitor_email:
        visitor = Visitor.query.filter_by(email=visitor_email).first()
        if visitor:
            session['visitor_id'] = visitor.visitor_id
            visitor_id = visitor.visitor_id

    # If not found by browser UUID or email, try by visitor_id
    if not visitor and visitor_id:
        visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()

    # If still not found, or visitor ID doesn't match the room's visitor ID
    if not visitor or room.visitor_id != visitor_id:
        # If this is a valid room, update the session with the correct visitor ID
        session['visitor_id'] = room.visitor_id
        visitor_id = room.visitor_id

    # Get visitor information - MOVED OUTSIDE THE IF BLOCK TO ENSURE IT'S ALWAYS DEFINED
    visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
    if not visitor:
        visitor = Visitor(
            visitor_id=visitor_id,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent'),
            browser_uuid=browser_uuid,
            session_id=session.get('session_id', str(uuid.uuid4()))
        )
        db.session.add(visitor)
        db.session.commit()
    else:
        # Update visitor information
        visitor.last_seen = datetime.utcnow()
        visitor.ip_address = request.remote_addr
        visitor.user_agent = request.headers.get('User-Agent')

        # Update browser_uuid if it's available and not set
        if browser_uuid and not visitor.browser_uuid:
            visitor.browser_uuid = browser_uuid
            db.session.commit()

    # Get all messages for the room
    messages = Message.query.filter_by(room_id=room_id).order_by(Message.timestamp).all()

    # Check if there are any online admins
    online_admins = Admin.query.filter_by(is_online=True).count()
    has_online_admins = online_admins > 0

    # Get timezone from settings
    timezone_str = SiteSettings.get_setting('timezone', 'UTC')
    try:
        import pytz
        timezone = pytz.timezone(timezone_str)
    except (ImportError, pytz.exceptions.UnknownTimeZoneError):
        from datetime import timezone as tz
        timezone = tz.utc

    # Get time format from settings
    try:
        from .models import TimeFormat
        time_format = TimeFormat.get_format()
    except:
        time_format = '24h'  # Default to 24h format

    # Check if we're within business hours
    now = datetime.now(timezone)
    day_of_week = now.weekday()  # 0-6 (Monday-Sunday)

    business_hours_today = BusinessHours.query.filter_by(
        day_of_week=day_of_week,
        is_active=True
    ).first()

    within_business_hours = False
    if business_hours_today:
        # Convert business hours to timezone-aware times
        start_time = datetime.combine(now.date(), business_hours_today.start_time)
        start_time = timezone.localize(start_time) if hasattr(timezone, 'localize') else start_time.replace(tzinfo=timezone)

        end_time = datetime.combine(now.date(), business_hours_today.end_time)
        end_time = timezone.localize(end_time) if hasattr(timezone, 'localize') else end_time.replace(tzinfo=timezone)

        # Compare full datetime objects instead of just time components
        within_business_hours = (start_time <= now <= end_time)

        # Add debug logging
        current_app.logger.info(f"Business hours check: start={start_time}, now={now}, end={end_time}, within={within_business_hours}")

    # Get settings with safe defaults
    away_message = "We're currently closed. Please leave a message and we'll get back to you during business hours."
    welcome_message = "Welcome to our customer support chat. How can we help you today?"
    ai_welcome_message = "Hello! I'm your AI assistant. How can I help you today?"
    company_name = "Our Company"

    try:
        # Try to get from SiteSettings but catch any database errors
        try:
            away_message = SiteSettings.get_setting('away_message', away_message)
            welcome_message = SiteSettings.get_setting('welcome_message', welcome_message)
            ai_welcome_message = SiteSettings.get_setting('ai_welcome_message', ai_welcome_message)
            company_name = SiteSettings.get_setting('company_name', company_name)
        except SQLAlchemyError:
            # Table likely doesn't exist yet, use default message
            pass
    except:
        # Any other errors, use default
        pass

    # Default to AI mode outside business hours or when no admins are online
    if not has_online_admins or not within_business_hours:
        room.chat_mode = 'ai'
    db.session.commit()

    # Create response with template
    response = Response(render_template('chat_iframe.html',
                          room=room,
                          visitor_id=visitor_id,
                          messages=messages,
                          visitor=visitor,
                          has_online_admins=has_online_admins,
                          within_business_hours=within_business_hours,
                          away_message=away_message,
                          welcome_message=welcome_message,
                          ai_welcome_message=ai_welcome_message,
                          company_name=company_name,
                          timezone=timezone_str,
                          time_format=time_format))

    # Set cookies for visitor_id and room_id
    response.set_cookie('visitor_id', visitor_id, max_age=60*60*24*365)  # 1 year
    response.set_cookie('room_id', room_id, max_age=60*60*24*365)  # 1 year

    return response

@socketio.on('update_visitor_details')
def handle_visitor_details_update(*args):
    """Handle updating visitor details - accepts variable arguments for compatibility"""
    # Extract data from args - could be (data) or (event_name, data)
    data = args[-1] if args else None  # Take the last argument as data

    if not data or not isinstance(data, dict):
        print(f"Error: Invalid data provided to handle_visitor_details_update: {args}")
        return {'success': False, 'message': 'Invalid data provided'}

    visitor_id = data.get('visitor_id')
    browser_uuid = data.get('browser_uuid')
    name = data.get('name')
    email = data.get('email')

    visitor = None

    # First try to find visitor by browser UUID if available
    if browser_uuid:
        visitor = Visitor.query.filter_by(browser_uuid=browser_uuid).first()
        print(f"Looking up visitor by browser_uuid: {browser_uuid}, found: {visitor is not None}")

    # If not found by browser UUID, try by visitor_id
    if not visitor and visitor_id:
        visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
        print(f"Looking up visitor by visitor_id: {visitor_id}, found: {visitor is not None}")

    if visitor:
        # Update visitor information
        visitor.name = name
        visitor.email = email

        # Update browser_uuid if it's available and not set
        if browser_uuid and not visitor.browser_uuid:
            visitor.browser_uuid = browser_uuid

        # Store email and name in session if provided
        if email:
            session['visitor_email'] = email
        if name:
            session['visitor_name'] = name

        # Make sure to commit the changes
        db.session.commit()

        # Log the update
        print(f"Updated visitor details for {visitor_id}: name={name}, email={email}, browser_uuid={browser_uuid}")

        # Find all rooms for this visitor
        rooms = Room.query.filter_by(visitor_id=visitor.visitor_id).all()

        # Emit visitor details updated event to all rooms
        for room in rooms:
            socketio.emit('visitor_details_updated', {
                'room_id': room.id,
                'visitor_id': visitor.visitor_id,
                'name': name,
                'email': email
            }, room=room.id)

        # Also emit directly to the requester
        socketio.emit('visitor_details_updated', {
            'success': True,
            'visitor_id': visitor.visitor_id,
            'name': name,
            'email': email
        }, room=request.sid)

        # Broadcast visitor details update to all admins for dashboard updates
        socketio.emit('visitor_details_updated_admin', {
            'visitor_id': visitor.visitor_id,
            'name': name,
            'email': email,
            'rooms': [room.id for room in rooms]  # Include room IDs for dashboard updates
        }, namespace='/')

        return {'success': True}
    else:
        print(f"Visitor not found: {visitor_id}")

        # Create a new visitor if not found
        if visitor_id:
            new_visitor = Visitor(
                visitor_id=visitor_id,
                name=name,
                email=email,
                browser_uuid=browser_uuid,
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent')
            )
            db.session.add(new_visitor)
            db.session.commit()

            print(f"Created new visitor: {visitor_id}, name={name}, email={email}")

            # Store in session
            if email:
                session['visitor_email'] = email
            if name:
                session['visitor_name'] = name

            socketio.emit('visitor_details_updated', {
                'success': True,
                'visitor_id': visitor_id,
                'name': name,
                'email': email
            }, room=request.sid)

            # Broadcast visitor details update to all admins for dashboard updates
            socketio.emit('visitor_details_updated_admin', {
                'visitor_id': visitor_id,
                'name': name,
                'email': email,
                'rooms': []  # New visitor, no rooms yet
            }, namespace='/')

            return {'success': True}

    return {'success': False, 'message': 'Failed to update visitor details'}

@views.route('/api/quick_responses', methods=['GET'])
@login_required
def get_quick_responses():
    """API endpoint to get all quick responses for the current admin"""
    try:
        print(f"DEBUG: Getting quick responses for admin {current_user.id} ({current_user.name})")

        # Get all quick responses for the current admin
        quick_responses = QuickResponse.query.filter_by(admin_id=current_user.id).all()
        print(f"DEBUG: Found {len(quick_responses)} quick responses")

        # Convert to list of dictionaries
        responses_data = [response.to_dict() for response in quick_responses]
        print(f"DEBUG: Converted to dict: {responses_data}")

        return jsonify({'quick_responses': responses_data})
    except Exception as e:
        print(f"ERROR in get_quick_responses: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'quick_responses': []}), 500

@views.route('/api/quick_responses', methods=['POST'])
@login_required
def create_quick_response():
    """API endpoint to create a new quick response"""
    data = request.json

    title = data.get('title')
    content = data.get('content')

    if not title or not content:
        return jsonify({'success': False, 'error': 'Title and content are required'}), 400

    # Create new quick response
    quick_response = QuickResponse(
        title=title,
        content=content,
        admin_id=current_user.id
    )
    db.session.add(quick_response)
    db.session.commit()

    return jsonify({'success': True, 'quick_response': quick_response.to_dict()})

@views.route('/api/quick_responses/<int:response_id>', methods=['PUT'])
@login_required
def update_quick_response(response_id):
    """API endpoint to update an existing quick response"""
    quick_response = QuickResponse.query.get_or_404(response_id)

    # Check if the quick response belongs to the current admin
    if quick_response.admin_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    data = request.json

    title = data.get('title')
    content = data.get('content')

    if title:
        quick_response.title = title
    if content:
        quick_response.content = content

    db.session.commit()

    return jsonify({'success': True, 'quick_response': quick_response.to_dict()})

@views.route('/api/quick_responses/<int:response_id>', methods=['DELETE'])
@login_required
def delete_quick_response(response_id):
    """API endpoint to delete a quick response"""
    quick_response = QuickResponse.query.get_or_404(response_id)

    # Check if the quick response belongs to the current admin
    if quick_response.admin_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    db.session.delete(quick_response)
    db.session.commit()

    return jsonify({'success': True})

@views.route('/api/quick_responses/send', methods=['POST'])
@login_required
def send_quick_response():
    """API endpoint to send a quick response to a chat room"""
    data = request.json

    room_id = data.get('room_id')
    response_id = data.get('response_id')

    if not room_id or not response_id:
        return jsonify({'success': False, 'error': 'Room ID and Response ID are required'}), 400

    # Get the quick response
    quick_response = QuickResponse.query.get_or_404(response_id)

    # Check if the quick response belongs to the current admin
    if quick_response.admin_id != current_user.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    # Get the room
    room = Room.query.get_or_404(room_id)

    # Create a new message
    message = Message(
        content=quick_response.content,
        room_id=room_id,
        is_from_visitor=False,
        sender_id=current_user.id,
        sender_name=current_user.name,
        is_read=True,
        admin_profile_image=current_user.profile_image
    )
    db.session.add(message)
    db.session.commit()

    # Emit the message to the room using socketio.emit with the room parameter
    socketio.emit('new_message', {
        'room_id': room_id,  # Add room_id to the data
        'content': quick_response.content,
        'sender_name': current_user.name,
        'sender_id': current_user.id,
        'time_sent': message.timestamp.strftime('%H:%M'),
        'is_from_visitor': False,
        'admin_profile_image': current_user.profile_image if hasattr(current_user, 'profile_image') else None
    }, room=room_id)

    return jsonify({'success': True})

# NEW QUICK RESPONSES IMPLEMENTATION
@views.route('/api/v2/quick_responses', methods=['GET'])
@login_required
def get_quick_responses_v2():
    """NEW API endpoint to get all quick responses for the current admin"""
    try:
        print(f"[QUICK_RESPONSES_V2] Getting responses for admin {current_user.id} ({current_user.name})")

        # Get all quick responses for the current admin
        quick_responses = QuickResponse.query.filter_by(admin_id=current_user.id).order_by(QuickResponse.created_at.desc()).all()
        print(f"[QUICK_RESPONSES_V2] Found {len(quick_responses)} responses")

        # Convert to list of dictionaries
        responses_data = []
        for response in quick_responses:
            response_dict = {
                'id': response.id,
                'title': response.title,
                'content': response.content,
                'admin_id': response.admin_id,
                'created_at': response.created_at.isoformat() if response.created_at else None,
                'updated_at': response.updated_at.isoformat() if response.updated_at else None
            }
            responses_data.append(response_dict)
            print(f"[QUICK_RESPONSES_V2] Response: {response_dict}")

        result = {'success': True, 'quick_responses': responses_data, 'count': len(responses_data)}
        print(f"[QUICK_RESPONSES_V2] Returning: {result}")
        return jsonify(result)

    except Exception as e:
        print(f"[QUICK_RESPONSES_V2] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e), 'quick_responses': []}), 500

@views.route('/api/v2/quick_responses', methods=['POST'])
@login_required
def create_quick_response_v2():
    """NEW API endpoint to create a new quick response"""
    try:
        data = request.get_json()
        print(f"[QUICK_RESPONSES_V2] Creating response with data: {data}")

        title = data.get('title', '').strip()
        content = data.get('content', '').strip()

        if not title or not content:
            return jsonify({'success': False, 'error': 'Title and content are required'}), 400

        # Create new quick response
        quick_response = QuickResponse(
            title=title,
            content=content,
            admin_id=current_user.id
        )
        db.session.add(quick_response)
        db.session.commit()

        response_dict = {
            'id': quick_response.id,
            'title': quick_response.title,
            'content': quick_response.content,
            'admin_id': quick_response.admin_id,
            'created_at': quick_response.created_at.isoformat() if quick_response.created_at else None,
            'updated_at': quick_response.updated_at.isoformat() if quick_response.updated_at else None
        }

        print(f"[QUICK_RESPONSES_V2] Created response: {response_dict}")
        return jsonify({'success': True, 'quick_response': response_dict})

    except Exception as e:
        print(f"[QUICK_RESPONSES_V2] CREATE ERROR: {e}")
        import traceback
        traceback.print_exc()
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@views.route('/api/v2/quick_responses/send', methods=['POST'])
@login_required
def send_quick_response_v2():
    """NEW API endpoint to send a quick response to a chat room"""
    try:
        data = request.get_json()
        print(f"[QUICK_RESPONSES_V2] Sending response with data: {data}")

        room_id = data.get('room_id')
        response_id = data.get('response_id')

        if not room_id or not response_id:
            return jsonify({'success': False, 'error': 'Room ID and Response ID are required'}), 400

        # Get the quick response
        quick_response = QuickResponse.query.get(response_id)
        if not quick_response:
            return jsonify({'success': False, 'error': 'Quick response not found'}), 404

        # Check if the quick response belongs to the current admin
        if quick_response.admin_id != current_user.id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        # Get the room
        room = Room.query.get(room_id)
        if not room:
            return jsonify({'success': False, 'error': 'Room not found'}), 404

        # Create a new message
        message = Message(
            content=quick_response.content,
            room_id=room_id,
            is_from_visitor=False,
            sender_id=current_user.id,
            sender_name=current_user.name,
            is_read=True,
            admin_profile_image=current_user.profile_image
        )
        db.session.add(message)
        db.session.commit()

        # Emit the message to the room using socketio.emit with the room parameter
        socketio.emit('new_message', {
            'room_id': room_id,
            'content': quick_response.content,
            'sender_name': current_user.name,
            'sender_id': current_user.id,
            'time_sent': message.timestamp.strftime('%H:%M'),
            'is_from_visitor': False,
            'admin_profile_image': current_user.profile_image if hasattr(current_user, 'profile_image') else None
        }, room=room_id)

        print(f"[QUICK_RESPONSES_V2] Sent response: {quick_response.title}")
        return jsonify({'success': True})

    except Exception as e:
        print(f"[QUICK_RESPONSES_V2] SEND ERROR: {e}")
        import traceback
        traceback.print_exc()
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@views.route('/api/v2/quick_responses/create_test', methods=['POST'])
@login_required
def create_test_quick_response():
    """Create a test quick response for the current admin"""
    try:
        print(f"[QUICK_RESPONSES_V2] Creating test response for admin {current_user.id}")

        # Check if test response already exists
        existing = QuickResponse.query.filter_by(
            admin_id=current_user.id,
            title="Test Response"
        ).first()

        if existing:
            print(f"[QUICK_RESPONSES_V2] Test response already exists")
            return jsonify({'success': True, 'message': 'Test response already exists'})

        # Create test quick response
        test_response = QuickResponse(
            title="Test Response",
            content="Hello! This is a test quick response. How can I help you today?",
            admin_id=current_user.id
        )
        db.session.add(test_response)
        db.session.commit()

        print(f"[QUICK_RESPONSES_V2] Created test response with ID: {test_response.id}")
        return jsonify({'success': True, 'message': 'Test response created successfully'})

    except Exception as e:
        print(f"[QUICK_RESPONSES_V2] ERROR creating test response: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

@views.route('/quick_responses')
@login_required
def quick_responses_page():
    """Page for managing quick responses"""
    # Get all quick responses for the current admin
    quick_responses = QuickResponse.query.filter_by(admin_id=current_user.id).all()
    return render_template('quick_responses.html', user=current_user, quick_responses=quick_responses)

@views.route('/api/check_visitor', methods=['POST'])
def check_visitor():
    """API endpoint to check if a visitor has an active room, and create one if not"""
    data = request.json or {}
    visitor_id = data.get('visitor_id')
    browser_uuid = data.get('browser_uuid')
    visitor_email = data.get('email')
    stored_visitor_id = data.get('stored_visitor_id')
    stored_room_id = data.get('stored_room_id')

    # Get the visitor's IP address and session ID
    visitor_ip = request.remote_addr
    user_agent = request.headers.get('User-Agent')

    # SESSION-BASED CHAT ISOLATION: Use browser UUID as primary session identifier
    # This ensures each browser tab/window gets its own chat room
    if browser_uuid:
        current_session_id = browser_uuid  # Use browser UUID as session ID
    else:
        # Fallback: generate a unique session ID for this request
        current_session_id = str(uuid.uuid4())

    print(f"Session ID for this request: {current_session_id}")

    visitor = None

    # Try to find visitor by stored_visitor_id first if available
    if stored_visitor_id:
        visitor = Visitor.query.filter_by(visitor_id=stored_visitor_id).first()
        if visitor:
            session['visitor_id'] = visitor.visitor_id
            visitor_id = visitor.visitor_id
            print(f"Found visitor by stored_visitor_id: {visitor_id}")

    # If not found by stored_visitor_id, try by browser UUID if available
    if not visitor and browser_uuid:
        visitor = Visitor.query.filter_by(browser_uuid=browser_uuid).first()

        # If found by browser UUID, update session with the correct visitor ID
        if visitor:
            session['visitor_id'] = visitor.visitor_id
            visitor_id = visitor.visitor_id
            print(f"Found visitor by browser_uuid: {visitor_id}")

    # If not found by browser UUID, try by email if available
    if not visitor and visitor_email:
        visitor = Visitor.query.filter_by(email=visitor_email).first()
        if visitor:
            session['visitor_id'] = visitor.visitor_id
            visitor_id = visitor.visitor_id
            session['visitor_email'] = visitor_email
            print(f"Found visitor by email: {visitor_id}")

    # If not found by browser UUID or email, try by visitor_id
    if not visitor and visitor_id:
        visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
        if visitor:
            print(f"Found visitor by visitor_id: {visitor_id}")

    # If visitor still not found, create a new one
    if not visitor:
        # Generate a new visitor ID if one wasn't provided
        if not visitor_id:
            visitor_id = str(uuid.uuid4())
            session['visitor_id'] = visitor_id

        # Check one more time to avoid duplicate visitor_id
        existing_visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
        if existing_visitor:
            visitor = existing_visitor
        else:
            # Create a new visitor
            visitor = Visitor(
                visitor_id=visitor_id,
                ip_address=visitor_ip,
                user_agent=user_agent,
                browser_uuid=browser_uuid,
                email=visitor_email,
                session_id=current_session_id
            )
            db.session.add(visitor)
            db.session.commit()
            print(f"Created new visitor: {visitor_id}")
    else:
        # Update existing visitor
        visitor.last_seen = datetime.utcnow()
        visitor.visit_count += 1
        visitor.ip_address = visitor_ip
        visitor.user_agent = user_agent
        visitor.session_id = current_session_id

        # Update browser_uuid if it's available and not set
        if browser_uuid and not visitor.browser_uuid:
            visitor.browser_uuid = browser_uuid

        # Update email if it's available and not set
        if visitor_email and not visitor.email:
            visitor.email = visitor_email
            session['visitor_email'] = visitor_email

        db.session.commit()

    # SESSION-BASED ROOM ISOLATION: Look for room by session ID first
    # This ensures each browser session gets its own chat room
    existing_room = Room.query.filter_by(
        session_id=current_session_id,
        is_active=True
    ).first()

    # If no room found by session ID, check stored room ID but verify it belongs to this session
    if not existing_room and stored_room_id:
        stored_room = Room.query.get(stored_room_id)
        if (stored_room and stored_room.is_active and
            stored_room.visitor_id == visitor.visitor_id and
            stored_room.session_id == current_session_id):
            existing_room = stored_room
            print(f"Using stored room with session verification: {existing_room.id}")

    if not existing_room:
        # Create a new room for this specific session
        new_room = Room(
            visitor_id=visitor.visitor_id,
            visitor_ip=visitor_ip,
            session_id=current_session_id
        )
        db.session.add(new_room)
        db.session.commit()

        # Emit new chat notification to all admins
        socketio.emit('new_chat', {
            'room_id': new_room.id,
            'visitor_ip': visitor_ip,
            'session_id': current_session_id,
            'timestamp': datetime.utcnow().isoformat()
        }, namespace='/')

        room_id = new_room.id
        print(f"Created new session-based room: {room_id} for session: {current_session_id}")
    else:
        room_id = existing_room.id
        print(f"Using existing session-based room: {room_id} for session: {current_session_id}")

    return jsonify({
        'success': True,
        'visitor_id': visitor.visitor_id,
        'room_id': room_id,
        'session_id': current_session_id
    })

@socketio.on('visitor_inactive')
def handle_visitor_inactive(data):
    """Handle visitor becoming inactive (tab switched or minimized)"""
    room_id = data.get('room_id')
    visitor_id = data.get('visitor_id')

    if not room_id or not visitor_id:
        return

    print(f"Visitor {visitor_id} became inactive in room {room_id}")

    # Update visitor status in active_connections
    if visitor_id in active_connections:
        active_connections[visitor_id]['active'] = False
        active_connections[visitor_id]['last_inactive'] = datetime.utcnow()

        # Notify admins about visitor inactive status
        socketio.emit('visitor_status_changed', {
            'visitor_id': visitor_id,
            'room_id': room_id,
            'status': 'inactive',
            'timestamp': datetime.utcnow().strftime('%H:%M:%S')
        }, to=None)

@socketio.on('visitor_active')
def handle_visitor_active(data):
    """Handle visitor becoming active again (tab focused)"""
    room_id = data.get('room_id')
    visitor_id = data.get('visitor_id')

    if not room_id or not visitor_id:
        return

    print(f"Visitor {visitor_id} became active in room {room_id}")

    # Update visitor status in active_connections
    if visitor_id in active_connections:
        active_connections[visitor_id]['active'] = True

        # Notify admins about visitor active status
        socketio.emit('visitor_status_changed', {
            'visitor_id': visitor_id,
            'room_id': room_id,
            'status': 'active',
            'timestamp': datetime.utcnow().strftime('%H:%M:%S')
        }, to=None)

@socketio.on('visitor_disconnect')
def handle_visitor_disconnect(data):
    """Handle visitor explicitly disconnecting (closing tab/window)"""
    room_id = data.get('room_id')
    visitor_id = data.get('visitor_id')

    if not room_id or not visitor_id:
        return

    print(f"Visitor {visitor_id} explicitly disconnected from room {room_id}")

    # Remove visitor from active_connections
    if visitor_id in active_connections:
        # Remove all SIDs for this visitor
        active_connections[visitor_id]['sids'] = []

        # Notify admins that visitor has disconnected
        socketio.emit('visitor_disconnected', {
            'visitor_id': visitor_id,
            'room_id': room_id,
            'timestamp': datetime.utcnow().strftime('%H:%M:%S')
        }, to=None)

        # Clean up tracking
        del active_connections[visitor_id]

@views.route('/api/visitor_disconnect', methods=['POST'])
def visitor_disconnect_beacon():
    """API endpoint for receiving disconnect notifications via navigator.sendBeacon"""
    room_id = request.form.get('room_id')
    visitor_id = request.form.get('visitor_id')

    if not room_id or not visitor_id:
        return jsonify({'success': False, 'error': 'Missing required parameters'}), 400

    print(f"Beacon: Visitor {visitor_id} disconnected from room {room_id}")

    # Remove visitor from active_connections
    if visitor_id in active_connections:
        # Remove all SIDs for this visitor
        active_connections[visitor_id]['sids'] = []

        # Notify admins that visitor has disconnected
        socketio.emit('visitor_disconnected', {
            'visitor_id': visitor_id,
            'room_id': room_id,
            'timestamp': datetime.utcnow().strftime('%H:%M:%S')
        }, to=None)

        # Clean up tracking
        del active_connections[visitor_id]

    # Return a 200 OK response
    return '', 200

@views.route('/api/connection_stats')
def api_connection_stats():
    """API endpoint to get connection statistics"""
    try:
        # Calculate additional stats
        total_rooms = Room.query.filter_by(is_active=True).count()
        total_visitors = len(active_connections)

        stats = {
            'server_stats': connection_stats.copy(),
            'active_visitors': total_visitors,
            'active_rooms': total_rooms,
            'admin_rooms': len(active_admin_rooms),
            'rate_limit_entries': len(connection_rate_limit),
            'timestamp': time_module.time()
        }

        return jsonify({
            'success': True,
            'stats': stats
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

@views.route('/api/business_hours', methods=['GET', 'POST'])
def api_business_hours():
    """API endpoint for managing business hours"""
    try:
        if request.method == 'POST':
            data = request.json

            # Handle enabled/disabled toggle
            if 'enabled' in data:
                enabled = data['enabled']

                # If disabling, deactivate all business hours
                if not enabled:
                    BusinessHours.query.update({BusinessHours.is_active: False})
                    db.session.commit()
                    # Clear cache when business hours are updated
                    try:
                        if hasattr(data_cache, 'get') and 'business_hours_data' in data_cache:
                            del data_cache['business_hours_data']
                    except Exception as e:
                        print(f"Warning: Could not clear cache: {str(e)}")
                    return jsonify({'success': True, 'message': 'Business hours disabled'})
                else:
                    # If enabling, activate all business hours with default times if none exist
                    existing_hours = BusinessHours.query.all()
                    if not existing_hours:
                        # Create default hours (9am-5pm Monday-Friday)
                        for day in range(7):  # 0-6 for Monday-Sunday
                            is_weekday = day < 5  # Monday-Friday
                            new_hours = BusinessHours(
                                day_of_week=day,
                                start_time=time(9, 0),  # 9:00 AM
                                end_time=time(17, 0),   # 5:00 PM
                                is_active=is_weekday    # Active on weekdays
                            )
                            db.session.add(new_hours)
                    else:
                        # Activate existing hours for weekdays
                        for day in range(5):  # 0-4 for Monday-Friday
                            hours = BusinessHours.query.filter_by(day_of_week=day).first()
                            if hours:
                                hours.is_active = True

                    db.session.commit()
                    # Clear cache when business hours are updated
                    try:
                        if hasattr(data_cache, 'get') and 'business_hours_data' in data_cache:
                            del data_cache['business_hours_data']
                    except Exception as e:
                        print(f"Warning: Could not clear cache: {str(e)}")
                    return jsonify({'success': True, 'message': 'Business hours enabled'})

            # Handle timezone update
            if 'timezone' in data:
                timezone = data['timezone']
                try:
                    # Import pytz here to ensure it's available in this scope
                    import pytz
                    if timezone in pytz.all_timezones:
                        SiteSettings.set_setting('timezone', timezone)
                        # Clear cache when timezone is updated
                        try:
                            if hasattr(data_cache, 'get') and 'business_hours_data' in data_cache:
                                del data_cache['business_hours_data']
                        except Exception as e:
                            print(f"Warning: Could not clear cache: {str(e)}")
                        return jsonify({'success': True, 'message': 'Timezone updated'})
                    else:
                        return jsonify({'success': False, 'message': 'Invalid timezone'})
                except ImportError:
                    # If pytz is not available, just accept the timezone
                    SiteSettings.set_setting('timezone', timezone)
                    # Clear cache when timezone is updated
                    try:
                        if hasattr(data_cache, 'get') and 'business_hours_data' in data_cache:
                            del data_cache['business_hours_data']
                    except Exception as e:
                        print(f"Warning: Could not clear cache: {str(e)}")
                    return jsonify({'success': True, 'message': 'Timezone updated'})

            # Handle time format update
            if 'time_format' in data:
                time_format = data['time_format']
                if time_format in ['12h', '24h']:
                    try:
                        TimeFormat.set_format(time_format)
                        # Clear cache when time format is updated
                        try:
                            if hasattr(data_cache, 'get') and 'business_hours_data' in data_cache:
                                del data_cache['business_hours_data']
                        except Exception as e:
                            print(f"Warning: Could not clear cache: {str(e)}")
                        return jsonify({'success': True, 'message': 'Time format updated'})
                    except Exception as e:
                        return jsonify({'success': False, 'message': f'Error updating time format: {str(e)}'})
                else:
                    return jsonify({'success': False, 'message': 'Invalid time format'})

            # Handle full business hours update
            if 'hours' in data:
                hours_data = data['hours']
                away_message = data.get('away_message')

                # Update away message if provided
                if away_message:
                    SiteSettings.set_setting('away_message', away_message)

                # Map day codes to day numbers
                days_map = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}

                # Process each day's hours
                for day_code, day_data in hours_data.items():
                    if day_code in days_map:
                        day_number = days_map[day_code]

                        # Get or create hours for this day
                        hours = BusinessHours.query.filter_by(day_of_week=day_number).first()
                        if not hours:
                            hours = BusinessHours(day_of_week=day_number)
                            db.session.add(hours)

                        # Update hours data - Fix: Explicitly handle closed as boolean
                        is_closed = False
                        if 'closed' in day_data:
                            # Handle different possible formats
                            if isinstance(day_data['closed'], bool):
                                is_closed = day_data['closed']
                            elif isinstance(day_data['closed'], str):
                                is_closed = day_data['closed'].lower() in ('true', 'yes', 'on', '1')
                            else:
                                is_closed = bool(day_data['closed'])

                        # Log for debugging
                        current_app.logger.info(f"Day {day_code} (#{day_number}): is_closed={is_closed}, raw value={day_data.get('closed')}")
                        print(f"DEBUG: Processing day {day_code}: is_closed={is_closed}, raw value={day_data.get('closed')}")

                        # Explicitly set is_active based on closed status
                        hours.is_active = not is_closed

                        # Debug: Log the database value that will be saved
                        current_app.logger.info(f"Setting hours.is_active={hours.is_active} for day {day_code}")
                        print(f"DEBUG: Setting hours.is_active={hours.is_active} for day {day_code}")

                        # Only update times if not closed
                        if not is_closed:
                            # Parse time strings
                            try:
                                start_time_str = day_data.get('open', '09:00')
                                end_time_str = day_data.get('close', '17:00')

                                # Parse HH:MM format
                                hours.start_time = datetime.strptime(start_time_str, '%H:%M').time()
                                hours.end_time = datetime.strptime(end_time_str, '%H:%M').time()
                            except ValueError as e:
                                return jsonify({'success': False, 'message': f'Invalid time format: {str(e)}'})

                db.session.commit()
                # Clear cache when business hours are updated
                try:
                    if hasattr(data_cache, 'get') and 'business_hours_data' in data_cache:
                        del data_cache['business_hours_data']
                except Exception as e:
                    print(f"Warning: Could not clear cache: {str(e)}")
                return jsonify({'success': True, 'message': 'Business hours updated'})

            # If we got here, no recognized action was taken
            return jsonify({'success': False, 'message': 'No valid action specified'})

        else:  # GET request
            # Try to get from cache first
            cached_data = data_cache.get('business_hours_data')
            if cached_data:
                return jsonify(cached_data)

            # If not in cache, generate the data
            # Get all business hours
            hours = BusinessHours.query.all()
            hours_data = []

            for hour in hours:
                hours_data.append({
                    'day_of_week': hour.day_of_week,
                    'day_name': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][hour.day_of_week],
                    'start_time': format_time(hour.start_time),
                    'end_time': format_time(hour.end_time),
                    'is_active': hour.is_active
                })

            # If no hours exist, create default data
            if not hours_data:
                for i in range(7):  # 0-6 for Monday-Sunday
                    is_weekday = i < 5  # Monday-Friday
                    hours_data.append({
                        'day_of_week': i,
                        'day_name': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][i],
                        'start_time': '09:00',
                        'end_time': '17:00',
                        'is_active': is_weekday
                    })

            # Check if any business hours are active
            has_active_hours = any(hour['is_active'] for hour in hours_data)

            # Get timezone and away message from settings
            timezone = SiteSettings.get_setting('timezone', 'UTC')
            away_message = SiteSettings.get_setting('away_message', "We're currently closed. Please leave a message and we'll get back to you during business hours.")

            # Get time format
            try:
                time_format = TimeFormat.get_format()
            except:
                time_format = '24h'  # Default to 24h

            # Get all available timezones
            try:
                # Import pytz here to ensure it's available in this scope
                import pytz
                all_timezones = pytz.all_timezones
            except ImportError:
                all_timezones = [
                    'UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
                    'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow',
                    'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Kolkata', 'Asia/Dubai',
                    'Australia/Sydney', 'Pacific/Auckland'
                ]

            # Format hours data for the template with explicit closed state
            formatted_hours = {}
            days_map = {0: 'mon', 1: 'tue', 2: 'wed', 3: 'thu', 4: 'fri', 5: 'sat', 6: 'sun'}

            for hour in hours_data:
                day_code = days_map[hour['day_of_week']]
                is_closed = not hour['is_active']  # Explicitly calculate is_closed from is_active

                formatted_hours[day_code] = {
                    'open': hour['start_time'],
                    'close': hour['end_time'],
                    'closed': is_closed  # Explicitly set closed based on is_active
                }

                # Log for debugging
                current_app.logger.info(f"GET response: Day {day_code}: is_active={hour['is_active']}, closed={is_closed}")
                print(f"DEBUG GET: Day {day_code}: is_active={hour['is_active']}, closed={is_closed}")

            # Prepare response data
            response_data = {
                'business_hours': hours_data,
                'formatted_hours': formatted_hours,  # Add formatted hours for template
                'enabled': has_active_hours,
                'timezone': timezone,
                'away_message': away_message,
                'time_format': time_format,
                'all_timezones': all_timezones
            }

            # Cache the data
            data_cache['business_hours_data'] = response_data

            return jsonify(response_data)

    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@socketio.on('change_chat_mode')
def handle_change_chat_mode(data):
    """Handle chat mode change from socket"""
    room_id = data.get('room_id')
    visitor_id = data.get('visitor_id')
    new_mode = data.get('mode', 'ai')  # Default to AI if not specified

    if not room_id:
        return

    # Get room
    room = Room.query.get(room_id)
    if not room:
        return

    # If switching to human mode, check if it's available
    if new_mode == 'human':
        # Check if any admins are online
        online_admins = Admin.query.filter_by(is_online=True).all()
        has_online_admins = len(online_admins) > 0

        # Check if we're within business hours
        within_business_hours = False
        try:
            # Get current time in site timezone
            import pytz

            # Get timezone from settings with safe fallback
            timezone_str = 'UTC'
            try:
                timezone_str = SiteSettings.get_setting('timezone', timezone_str)
            except:
                pass

            try:
                timezone = pytz.timezone(timezone_str)
            except:
                timezone = pytz.UTC

            # Get current time in site timezone
            now = datetime.now(timezone)

            # Get day of week (0-6, Monday-Sunday)
            day_of_week = now.weekday()

            # Check if today is a business day
            business_hours_today = BusinessHours.query.filter_by(
                day_of_week=day_of_week,
                is_active=True
            ).first()

            if business_hours_today:
                # Convert business hours to timezone-aware times
                start_time = datetime.combine(now.date(), business_hours_today.start_time)
                start_time = timezone.localize(start_time) if hasattr(timezone, 'localize') else start_time.replace(tzinfo=timezone)

                end_time = datetime.combine(now.date(), business_hours_today.end_time)
                end_time = timezone.localize(end_time) if hasattr(timezone, 'localize') else end_time.replace(tzinfo=timezone)

                # Compare full datetime objects instead of just time components
                within_business_hours = (start_time <= now <= end_time)

                # Add debug logging
                current_app.logger.info(f"Business hours check: start={start_time}, now={now}, end={end_time}, within={within_business_hours}")
        except Exception as e:
            # Log error but continue
            current_app.logger.error(f"Error checking business hours: {str(e)}")

        # Human mode is available only if admins are online AND we're within business hours
        human_mode_available = has_online_admins and within_business_hours

        if not human_mode_available:
            # Determine the reason
            reason = None
            if not has_online_admins and not within_business_hours:
                reason = "No admin is available and outside of business hours"
            elif not has_online_admins:
                reason = "No admin is available right now"
            elif not within_business_hours:
                reason = "Outside of business hours"

            # Emit error event back to the client
            emit('chat_mode_error', {
                'room_id': room_id,
                'error': reason or "Human mode is not available",
                'current_mode': room.chat_mode
            })
            return

    # Update room chat mode
    room.chat_mode = new_mode
    db.session.commit()

    # If switching to human mode, notify admins
    if new_mode == 'human':
        # Get visitor information from database
        visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
        visitor_name = visitor.name if visitor else None
        visitor_email = visitor.email if visitor else None

        # Emit event to notify admins about this human mode chat
        emit('visitor_connected', {
            'visitor_id': visitor_id,
            'room_id': room_id,
            'visitor_name': visitor_name,
            'visitor_email': visitor_email,
            'visitor_ip': room.visitor_ip,
            'chat_mode': new_mode
        }, broadcast=True)
    else:
        # If switching to AI mode, notify admins to remove from dashboard
        emit('visitor_disconnected', {
            'visitor_id': visitor_id,
            'room_id': room_id
        }, broadcast=True)

    # Create a system message about the mode change
    message = Message(
        content=f"Chat mode changed to {new_mode.upper()} ({new_mode.capitalize()} {'Assistant' if new_mode == 'ai' else 'Support'})",
        room_id=room_id,
        is_from_visitor=False,
        sender_name="System",
        is_read=True,
        is_system_message=True
    )
    db.session.add(message)
    db.session.commit()

    # Emit system message to all clients in the room
    socketio.emit('system_message', {
        'room_id': room_id,
        'content': message.content,
        'type': 'mode_change',
        'timestamp': format_time(datetime.utcnow())
    }, room=room_id)

    # Notify all clients about the mode change
    emit('chat_mode_changed', {
        'room_id': room_id,
        'mode': new_mode
    }, room=room_id)

@views.route('/api/debug/business_hours', methods=['GET'])
def debug_business_hours():
    """Debug endpoint to check the current state of business hours"""
    # Only allow in development mode
    if not current_app.debug:
        return jsonify({'error': 'Debug endpoints only available in debug mode'}), 403

    # Get all business hours
    hours = BusinessHours.query.all()
    hours_data = []

    for hour in hours:
        hours_data.append({
            'day_of_week': hour.day_of_week,
            'day_name': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][hour.day_of_week],
            'start_time': format_time(hour.start_time),
            'end_time': format_time(hour.end_time),
            'is_active': hour.is_active,
            'closed': not hour.is_active
        })

    # Map to day codes for easier debugging
    days_map = {0: 'mon', 1: 'tue', 2: 'wed', 3: 'thu', 4: 'fri', 5: 'sat', 6: 'sun'}
    formatted_hours = {}

    for hour in hours_data:
        day_code = days_map[hour['day_of_week']]
        formatted_hours[day_code] = {
            'open': hour['start_time'],
            'close': hour['end_time'],
            'closed': not hour['is_active']
        }

    # Check if we're within business hours right now
    within_business_hours = False
    current_time_info = {}

    try:
        # Get timezone from settings
        timezone_str = SiteSettings.get_setting('timezone', 'UTC')
        try:
            import pytz
            timezone = pytz.timezone(timezone_str)
        except:
            from datetime import timezone as tz
            timezone = tz.utc

        # Get current time in site timezone
        now = datetime.now(timezone)
        day_of_week = now.weekday()

        # Get today's business hours
        business_hours_today = BusinessHours.query.filter_by(
            day_of_week=day_of_week,
            is_active=True
        ).first()

        current_time_info = {
            'now': now.isoformat(),
            'day_of_week': day_of_week,
            'day_name': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][day_of_week],
            'timezone': timezone_str,
            'has_business_hours_today': business_hours_today is not None
        }

        if business_hours_today:
            # Convert business hours to timezone-aware times
            start_time = datetime.combine(now.date(), business_hours_today.start_time)
            start_time = timezone.localize(start_time) if hasattr(timezone, 'localize') else start_time.replace(tzinfo=timezone)

            end_time = datetime.combine(now.date(), business_hours_today.end_time)
            end_time = timezone.localize(end_time) if hasattr(timezone, 'localize') else end_time.replace(tzinfo=timezone)

            # Compare full datetime objects
            within_business_hours = (start_time <= now <= end_time)

            current_time_info.update({
                'start_time': start_time.isoformat(),
                'end_time': end_time.isoformat(),
                'within_business_hours': within_business_hours
            })
    except Exception as e:
        current_time_info['error'] = str(e)

    return jsonify({
        'raw_hours': hours_data,
        'formatted_hours': formatted_hours,
        'current_time_check': current_time_info
    })

@views.route('/embed.js')
def embed_script():
    """Serve the embed script with the correct configuration"""
    # Get server URL from request
    server_url = request.url_root.rstrip('/')

    # Get settings from database
    primary_color = SiteSettings.get_setting('primary_color', '#4674C6')
    widget_icon_color = SiteSettings.get_setting('widget_icon_color', '#4674C6')
    company_name = SiteSettings.get_setting('company_name', 'Customer Support')
    welcome_message = SiteSettings.get_setting('welcome_message', 'Welcome to our customer support chat. How can we help you today?')

    # Get logo URL if available
    logo_url = SiteSettings.get_setting('logo_url', '')

    # Get widget position (default to right)
    position = SiteSettings.get_setting('widget_position', 'right')

    # Read the embed.js template
    with open(os.path.join(current_app.static_folder, 'js', 'embed.js'), 'r') as file:
        js_content = file.read()

    # Replace placeholders with actual values
    js_content = js_content.replace('{{server_url}}', server_url)
    js_content = js_content.replace('{{primary_color}}', primary_color)
    js_content = js_content.replace('{{widget_icon_color}}', widget_icon_color)
    js_content = js_content.replace('{{position}}', position)
    js_content = js_content.replace('{{welcome_message}}', welcome_message)
    js_content = js_content.replace('{{company_name}}', company_name)
    js_content = js_content.replace('{{logo_url}}', logo_url)

    # Serve as JavaScript
    response = Response(js_content, mimetype='application/javascript')

    # Set cache control headers
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'

    return response

@views.route('/standalone-demo')
def standalone_demo():
    """Serve the standalone embed demo page"""
    return send_from_directory(
        os.path.join(current_app.static_folder, 'examples'),
        'standalone_embed_demo.html'
    )

@views.route('/external-host-example')
def external_host_example():
    """Serve the external host integration example"""
    return send_from_directory(
        os.path.join(current_app.static_folder, 'examples'),
        'external_host_example.html'
    )

@views.route('/simple-test')
def simple_test():
    """Serve the simple test file (works without live server)"""
    return send_from_directory(
        os.path.join(current_app.static_folder, 'examples'),
        'simple_test.html'
    )

def get_business_hours_data():
    """Get business hours data for templates"""
    # Get all business hours
    hours = BusinessHours.query.all()
    hours_data = []

    for hour in hours:
        hours_data.append({
            'day_of_week': hour.day_of_week,
            'day_name': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][hour.day_of_week],
            'start_time': hour.start_time.strftime('%H:%M'),
            'end_time': hour.end_time.strftime('%H:%M'),
            'is_active': hour.is_active
        })

    # If no hours exist, create default data
    if not hours_data:
        for i in range(7):  # 0-6 for Monday-Sunday
            is_weekday = i < 5  # Monday-Friday
            hours_data.append({
                'day_of_week': i,
                'day_name': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][i],
                'start_time': '09:00',
                'end_time': '17:00',
                'is_active': is_weekday
            })

    # Check if any business hours are active
    has_active_hours = any(hour['is_active'] for hour in hours_data)

    # Get timezone and away message from settings
    timezone = SiteSettings.get_setting('timezone', 'UTC')
    away_message = SiteSettings.get_setting('away_message', "We're currently closed. Please leave a message and we'll get back to you during business hours.")

    # Get time format
    try:
        time_format = TimeFormat.get_format()
    except:
        time_format = '24h'  # Default to 24h

    # Format hours data for the template with explicit closed state
    formatted_hours = {}
    days_map = {0: 'mon', 1: 'tue', 2: 'wed', 3: 'thu', 4: 'fri', 5: 'sat', 6: 'sun'}

    for hour in hours_data:
        day_code = days_map[hour['day_of_week']]
        is_closed = not hour['is_active']  # Explicitly calculate is_closed from is_active

        formatted_hours[day_code] = {
            'open': hour['start_time'],
            'close': hour['end_time'],
            'closed': is_closed  # Explicitly set closed based on is_active
        }

    # Prepare response data
    response_data = {
        'business_hours': hours_data,
        'formatted_hours': formatted_hours,  # Add formatted hours for template
        'hours': formatted_hours,  # For backward compatibility
        'enabled': has_active_hours,
        'timezone': timezone,
        'away_message': away_message,
        'time_format': time_format
    }

    return response_data