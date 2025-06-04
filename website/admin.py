from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app, make_response
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from . import db
from . import data_cache
from .models import Admin, Room, Message, Visitor, SiteSettings, BusinessHours, admin_rooms_association, TimeFormat
from .utils import format_time
from datetime import datetime, timedelta
from sqlalchemy import desc
import os
from werkzeug.utils import secure_filename
import requests
from sqlalchemy.exc import SQLAlchemyError
import time
import uuid
from functools import wraps
from sqlalchemy import or_

admin_bp = Blueprint('admin', __name__)

# Define admin_required decorator
def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not hasattr(current_user, 'is_online'):
            return redirect(url_for('auth.admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# Redirecting /admin/login to auth.admin_login
@admin_bp.route('/login')
def login_redirect():
    return redirect(url_for('auth.admin_login'))

@admin_bp.route('/logout')
@login_required
def logout():
    if hasattr(current_user, 'is_online'):
        current_user.is_online = False
        current_user.last_seen = datetime.utcnow()
        db.session.commit()
    logout_user()
    return redirect(url_for('auth.admin_login'))

@admin_bp.route('/dashboard')
@login_required
def dashboard():
    if not hasattr(current_user, 'is_online'):
        logout_user()
        return redirect(url_for('auth.admin_login'))

    # Get all active rooms in human mode with real-time connection
    from . import views
    active_rooms = []

    # Get rooms that have active connections
    active_visitor_ids = []
    for visitor_id, data in views.active_connections.items():
        if data['sids']:  # Only consider visitors with active socket connections
            active_visitor_ids.append(visitor_id)
            room_id = data.get('room_id')
            if room_id:
                room = Room.query.filter_by(id=room_id, is_active=True, chat_mode='human').first()
                if room:
                    active_rooms.append(room)

    # Count unread messages for each room and get visitor info
    rooms_with_data = []
    for room in active_rooms:
        unread_count = Message.query.filter_by(
            room_id=room.id,
            is_read=False,
            is_from_visitor=True,
            is_system_message=False  # Exclude system messages from unread count
        ).count()

        last_message = Message.query.filter_by(room_id=room.id).order_by(desc(Message.timestamp)).first()

        # Get visitor information
        visitor = Visitor.query.filter_by(visitor_id=room.visitor_id).first()
        visitor_name = visitor.name if visitor and visitor.name else None
        visitor_email = visitor.email if visitor and visitor.email else None

        # Check if this visitor is in the active_connections dictionary from views.py
        is_connected = room.visitor_id in active_visitor_ids

        # Only include connected visitors
        if is_connected:
            rooms_with_data.append({
                'room': room,
                'unread_count': unread_count,
                'last_message': last_message,
                'assigned_to_me': current_user in room.admins,
                'visitor_name': visitor_name,
                'visitor_email': visitor_email
            })

    # Get recent visitors with active connections only
    recent_visitors = Visitor.query.filter(Visitor.visitor_id.in_(active_visitor_ids)).order_by(desc(Visitor.last_seen)).limit(10).all()

    # Count active visitors and admins
    active_visitors_count = len(active_visitor_ids)
    total_admins = Admin.query.count()
    online_status = "Online" if current_user.is_online else "Offline"

    return render_template('admin/dashboard.html',
                           rooms=rooms_with_data,
                           admin=current_user,
                           visitors=recent_visitors,
                           active_visitors_count=active_visitors_count,
                           total_admins=total_admins,
                           online_status=online_status)

@admin_bp.route('/chat/<room_id>')
@login_required
def chat(room_id):
    if not hasattr(current_user, 'is_online'):
        logout_user()
        return redirect(url_for('auth.admin_login'))

    room = Room.query.get_or_404(room_id)

    # Assign admin to room if not already assigned
    if current_user not in room.admins:
        room.admins.append(current_user)

        # Only set has_admin to True if the admin is actually online
        if current_user.is_online:
            room.has_admin = True

        # Switch to human mode when admin joins
        room.chat_mode = 'human'
        db.session.commit()

        # Emit event that admin joined this chat
        from . import socketio
        socketio.emit('admin_joined_chat', {
            'room_id': room_id,
            'admin_id': current_user.id,
            'admin_name': current_user.name
        }, to=None)
    else:
        # If admin is already assigned, ensure has_admin is set correctly
        if current_user.is_online and not room.has_admin:
            room.has_admin = True
            db.session.commit()

            # Emit event that admin joined this chat
            from . import socketio
            socketio.emit('admin_joined_chat', {
                'room_id': room_id,
                'admin_id': current_user.id,
                'admin_name': current_user.name
            }, to=None)

    # Mark all unread visitor messages as read
    unread_messages = Message.query.filter_by(
        room_id=room.id,
        is_read=False,
        is_from_visitor=True
    ).all()

    for message in unread_messages:
        message.is_read = True

    db.session.commit()

    # Get all messages for the room
    messages = Message.query.filter_by(room_id=room.id).order_by(Message.timestamp).all()

    # Get visitor info
    visitor = Visitor.query.filter_by(visitor_id=room.visitor_id).first()

    return render_template('admin/chat.html',
                           room=room,
                           messages=messages,
                           admin=current_user,
                           visitor=visitor)

@admin_bp.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    # Only allow super admins to register new admins
    if not hasattr(current_user, 'is_online') or not current_user.is_super_admin:
        flash('Only super admins can register new admins', 'error')
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        name = request.form.get('name')
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')

        # Handle profile image upload
        profile_image_url = None
        if 'profile_image' in request.files:
            profile_image = request.files['profile_image']
            if profile_image and profile_image.filename:
                # Generate a unique filename
                admin_id = str(uuid.uuid4())  # Generate a temp ID for the filename
                filename = secure_filename(f"{admin_id}_{int(time.time())}_{profile_image.filename}")
                image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                profile_image.save(image_path)

                # Get the correct URL path for the image
                url_path = current_app.config.get('UPLOAD_URL_PATH', '/static/uploads')

                # Set profile image URL
                profile_image_url = f"{url_path}/{filename}"
                print(f"New admin profile image URL: {profile_image_url}")

        if not all([name, username, email, password, password_confirm]):
            flash('All fields are required', 'error')
            return redirect(url_for('admin.register'))

        if password != password_confirm:
            flash('Passwords do not match', 'error')
            return redirect(url_for('admin.register'))

        existing_admin = Admin.query.filter_by(email=email).first()
        if existing_admin:
            flash('Email already registered', 'error')
            return redirect(url_for('admin.register'))

        existing_username = Admin.query.filter_by(username=username).first()
        if existing_username:
            flash('Username already taken', 'error')
            return redirect(url_for('admin.register'))

        new_admin = Admin(
            name=name,
            username=username,
            email=email,
            password=generate_password_hash(password, method='pbkdf2:sha256'),
            is_online=False,
            profile_image=profile_image_url,
            push_enabled=request.form.get('push_enabled') == 'on',
            is_super_admin=False  # Regular admins are not super admins
        )

        db.session.add(new_admin)
        db.session.commit()

        flash(f'Admin {name} has been registered successfully', 'success')
        return redirect(url_for('admin.register'))

    # Get all admins to display in the management table
    admins = Admin.query.all()

    return render_template('admin/register.html', admin=current_user, admins=admins)

@admin_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if not hasattr(current_user, 'is_online'):
        logout_user()
        return redirect(url_for('auth.admin_login'))

    if request.method == 'POST':
        name = request.form.get('name')
        username = request.form.get('username')
        email = request.form.get('email')
        push_enabled = request.form.get('push_enabled') == 'on'

        # Check if username is already taken by another admin
        if username != current_user.username:
            existing_username = Admin.query.filter_by(username=username).first()
            if existing_username:
                flash('Username already taken', 'error')
                return redirect(url_for('admin.profile'))

        # Update profile image if provided
        if 'profile_image' in request.files:
            profile_image = request.files['profile_image']
            if profile_image and profile_image.filename:
                # Save the image
                filename = secure_filename(f"{current_user.id}_{int(time.time())}_{profile_image.filename}")
                image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                profile_image.save(image_path)

                # Get the correct URL path for the image
                url_path = current_app.config.get('UPLOAD_URL_PATH', '/static/uploads')

                # Update profile image URL
                current_user.profile_image = f"{url_path}/{filename}"
                print(f"Updated profile image URL: {current_user.profile_image}")

        # Update password only if new password is provided
        new_password = request.form.get('new_password')
        if new_password:
            current_password = request.form.get('current_password')
            confirm_password = request.form.get('confirm_password')

            if not current_password:
                flash('Current password is required to change password', 'error')
                return redirect(url_for('admin.profile'))

            if not check_password_hash(current_user.password, current_password):
                flash('Current password is incorrect', 'error')
                return redirect(url_for('admin.profile'))

            if new_password != confirm_password:
                flash('New passwords do not match', 'error')
                return redirect(url_for('admin.profile'))

            current_user.password = generate_password_hash(new_password, method='pbkdf2:sha256')

        # Update other fields
        current_user.name = name
        current_user.username = username
        current_user.email = email
        current_user.push_enabled = push_enabled

        db.session.commit()
        flash('Profile updated successfully', 'success')
        return redirect(url_for('admin.profile'))

    return render_template('admin/profile.html', admin=current_user)

@admin_bp.route('/api/rooms')
@login_required
def api_get_rooms():
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    rooms = Room.query.filter_by(is_active=True).all()
    room_data = []

    for room in rooms:
        unread_count = Message.query.filter_by(
            room_id=room.id,
            is_read=False,
            is_from_visitor=True,
            is_system_message=False  # Exclude system messages from unread count
        ).count()

        last_message = Message.query.filter_by(room_id=room.id).order_by(desc(Message.timestamp)).first()
        last_message_text = last_message.content if last_message else ""
        last_message_time = last_message.timestamp if last_message else room.last_activity

        room_data.append({
            'id': room.id,
            'visitor_ip': room.visitor_ip or 'Unknown',
            'created_at': room.created_at.isoformat(),
            'last_activity': room.last_activity.isoformat(),
            'unread_count': unread_count,
            'has_admin': room.has_admin,
            'assigned_to_me': current_user in room.admins,
            'last_message': last_message_text,
            'last_message_time': last_message_time.isoformat() if last_message_time else "",
            'last_message_timestamp': last_message_time.timestamp() if last_message_time else 0,
            'chat_mode': room.chat_mode,
            'current_page': room.current_page,
            'time_on_site': room.time_on_site,
            'visit_count': room.visit_count
        })

    # Sort room_data by last_message_timestamp in descending order (most recent first)
    room_data.sort(key=lambda x: x['last_message_timestamp'], reverse=True)

    return jsonify({'rooms': room_data})

@admin_bp.route('/api/close_chat/<room_id>', methods=['POST'])
@login_required
def close_chat(room_id):
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    room = Room.query.get_or_404(room_id)

    # Only admins assigned to the room can mark it as inactive
    if current_user not in room.admins:
        return jsonify({'error': 'Not authorized to modify this chat'}), 403

    # We're just marking the room as inactive but keeping all associations
    # This ensures the chat history is preserved and accessible
    room.is_active = False

    # Remove admin from room
    room.admins.remove(current_user)

    # Check if any active admins remain in the room
    active_admins = [admin for admin in room.admins if admin.is_online]

    # If no more active admins, update has_admin flag
    if not active_admins:
        room.has_admin = False

        # Emit event that admin left this chat
        from . import socketio
        socketio.emit('admin_left_chat', {
            'room_id': room_id,
            'admin_id': current_user.id,
            'admin_name': current_user.name
        }, to=None)

    db.session.commit()

    return jsonify({'success': True})

@admin_bp.route('/api/toggle_online_status', methods=['POST'])
@login_required
def toggle_online_status():
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    try:
        data = request.json
        is_online = data.get('is_online', not current_user.is_online)  # Toggle if not provided

        # Only update if the status is actually changing
        if current_user.is_online != is_online:
            current_user.is_online = is_online
            current_user.last_seen = datetime.utcnow()
            db.session.commit()

            # Emit socket event for admin status change
            try:
                from . import socketio
                socketio.emit('admin_status_changed', {
                    'admin_id': current_user.id,
                    'admin_name': current_user.name,
                    'is_online': current_user.is_online,
                    'timestamp': format_time(datetime.utcnow())
                })

                # Also emit admin_status_change event for the handler in __init__.py
                socketio.emit('admin_status_change', {
                    'admin_id': current_user.id,
                    'is_online': current_user.is_online
                })
            except Exception as e:
                # Log socket error but don't fail the request
                current_app.logger.error(f"Socket error in toggle_online_status: {str(e)}")

        # Always return success, even if no change was needed
        return jsonify({
            'success': True,
            'is_online': current_user.is_online
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in toggle_online_status: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@admin_bp.route('/visitors')
@login_required
def visitors():
    if not hasattr(current_user, 'is_online'):
        logout_user()
        return redirect(url_for('auth.admin_login'))

    # Get all active visitors (seen in the last 30 minutes)
    recent_time = datetime.utcnow() - timedelta(minutes=30)
    visitors = Visitor.query.filter(Visitor.last_seen >= recent_time).order_by(desc(Visitor.last_seen)).all()

    # Get all visitors with active chats
    visitors_with_chats = []
    visitors_without_chats = []

    for visitor in visitors:
        room = Room.query.filter_by(visitor_id=visitor.visitor_id, is_active=True).first()
        if room:
            visitors_with_chats.append({
                'visitor': visitor,
                'room': room
            })
        else:
            visitors_without_chats.append({
                'visitor': visitor,
                'room': None
            })

    return render_template('admin/visitors.html',
                          admin=current_user,
                          visitors_with_chats=visitors_with_chats,
                          visitors_without_chats=visitors_without_chats)

@admin_bp.route('/chat_history')
@login_required
def chat_history():
    if not hasattr(current_user, 'is_online'):
        logout_user()
        return redirect(url_for('auth.admin_login'))

    # Get search parameters
    search_term = request.args.get('search', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    status_filter = request.args.get('status', '')
    admin_filter = request.args.get('admin', '')

    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Set to show 10 users per page

    # Base query
    query = Room.query

    # Apply search filter
    if search_term:
        # Join with Visitor to search by name and email
        query = query.join(Visitor, Room.visitor_id == Visitor.visitor_id, isouter=True)
        query = query.filter(
            or_(
                Visitor.name.ilike(f'%{search_term}%'),
                Visitor.email.ilike(f'%{search_term}%'),
                Room.visitor_ip.ilike(f'%{search_term}%')
            )
        )

    # Apply date filters
    if start_date:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
            query = query.filter(Room.created_at >= start_date_obj)
        except ValueError:
            # Invalid date format, ignore filter
            pass

    if end_date:
        try:
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
            # Add one day to include the entire end date
            end_date_obj = end_date_obj + timedelta(days=1)
            query = query.filter(Room.created_at < end_date_obj)
        except ValueError:
            # Invalid date format, ignore filter
            pass

    # Apply status filter
    if status_filter:
        if status_filter == 'active':
            query = query.filter(Room.is_active == True)
        elif status_filter == 'closed':
            query = query.filter(Room.is_active == False)

    # Apply admin filter
    if admin_filter and admin_filter != 'all':
        try:
            admin_id = admin_filter
            query = query.join(admin_rooms_association).filter(admin_rooms_association.c.admin_id == admin_id)
        except ValueError:
            pass

    # Get rooms with at least one message and paginate
    rooms_with_messages = query.join(Message).group_by(Room.id).order_by(desc(Room.last_activity))
    pagination = rooms_with_messages.paginate(page=page, per_page=per_page, error_out=False)
    rooms = pagination.items

    # Prepare room data with message counts
    room_data = []
    for room in rooms:
        message_count = Message.query.filter_by(room_id=room.id).count()
        ai_message_count = Message.query.filter_by(room_id=room.id, is_ai_generated=True).count()
        human_message_count = Message.query.filter_by(room_id=room.id, is_from_visitor=False, is_ai_generated=False).count()
        visitor_message_count = Message.query.filter_by(room_id=room.id, is_from_visitor=True).count()

        # Get assigned admins
        admin_names = [admin.name for admin in room.admins]

        # Get visitor information
        visitor = Visitor.query.filter_by(visitor_id=room.visitor_id).first()

        room_data.append({
            'room': room,
            'message_count': message_count,
            'ai_message_count': ai_message_count,
            'human_message_count': human_message_count,
            'visitor_message_count': visitor_message_count,
            'admin_names': admin_names,
            'visitor': visitor
        })

    # Get all admins for the filter dropdown
    all_admins = Admin.query.all()

    return render_template('admin/chat_history.html',
                          admin=current_user,
                          rooms=room_data,
                          search_term=search_term,
                          start_date=start_date,
                          end_date=end_date,
                          status_filter=status_filter,
                          admin_filter=admin_filter,
                          admins=all_admins,
                          pagination=pagination)

@admin_bp.route('/business_hours', methods=['GET'])
@login_required
def business_hours_page():
    if not hasattr(current_user, 'is_online'):
        logout_user()
        return redirect(url_for('auth.admin_login'))

    # Get business hours settings
    try:
        from .views import get_business_hours_data
        settings = get_business_hours_data()
    except Exception as e:
        current_app.logger.error(f"Error getting business hours data: {str(e)}")
        settings = {
            'hours': {},
            'away_message': "We're currently closed. Please leave a message and we'll get back to you during business hours."
        }

    # Define days of the week
    days = [
        {'code': 'mon', 'name': 'Monday'},
        {'code': 'tue', 'name': 'Tuesday'},
        {'code': 'wed', 'name': 'Wednesday'},
        {'code': 'thu', 'name': 'Thursday'},
        {'code': 'fri', 'name': 'Friday'},
        {'code': 'sat', 'name': 'Saturday'},
        {'code': 'sun', 'name': 'Sunday'}
    ]

    return render_template('admin/business_hours.html',
                          admin=current_user,
                          settings=settings,
                          days=days)

@admin_bp.route('/save_business_hours', methods=['POST'])
@login_required
def save_business_hours():
    if not hasattr(current_user, 'is_online'):
        logout_user()
        return redirect(url_for('auth.admin_login'))

    try:
        # Get away message
        away_message = request.form.get('away_message', "We're currently closed. Please leave a message and we'll get back to you during business hours.")
        SiteSettings.set_setting('away_message', away_message)

        # Process each day's hours
        days_map = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}

        # Debug: Log form data
        for key, value in request.form.items():
            current_app.logger.info(f"Form data: {key}={value}")

        # First, clear existing business hours
        BusinessHours.query.delete()

        # Then add new hours for each day
        for day_code, day_index in days_map.items():
            # Check if day is closed - handle both 'on' and 'off' values
            checkbox_value = request.form.get(f'{day_code}_closed')
            is_closed = checkbox_value == 'on'

            # Debug log
            current_app.logger.info(f"Processing day {day_code}: checkbox value={checkbox_value}, is_closed={is_closed}")

            # Get open and close times
            open_time = request.form.get(f'{day_code}_open', '09:00')
            close_time = request.form.get(f'{day_code}_close', '17:00')

            # Parse times to datetime.time objects
            from datetime import datetime
            try:
                start_time = datetime.strptime(open_time, '%H:%M').time()
                end_time = datetime.strptime(close_time, '%H:%M').time()
            except ValueError:
                # If time parsing fails, use default times
                start_time = datetime.strptime('09:00', '%H:%M').time()
                end_time = datetime.strptime('17:00', '%H:%M').time()

            # Add business hours entry
            business_hours = BusinessHours(
                day_of_week=day_index,
                start_time=start_time,
                end_time=end_time,
                is_active=not is_closed  # Set active to opposite of closed
            )
            db.session.add(business_hours)

            # Debug log
            current_app.logger.info(f"Added business hours for {day_code}: is_active={not is_closed}")

        # Commit changes
        db.session.commit()

        # Debug: Check what was saved in the database
        saved_hours = BusinessHours.query.all()
        for hour in saved_hours:
            current_app.logger.info(f"Saved in DB: day={hour.day_of_week}, is_active={hour.is_active}")

        # Clear cache
        try:
            if hasattr(data_cache, 'get') and 'business_hours_data' in data_cache:
                del data_cache['business_hours_data']
        except Exception as e:
            print(f"Warning: Could not clear cache: {str(e)}")

        flash('Business hours updated successfully!', 'success')
        return redirect(url_for('admin.business_hours_page', success=True))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving business hours: {str(e)}")
        flash(f'Error saving business hours: {str(e)}', 'error')
        return redirect(url_for('admin.business_hours_page', error=True))

@admin_bp.route('/widget_settings', methods=['GET', 'POST'])
@login_required
def widget_settings():
    """Page to configure the chat widget for embedding"""
    if not hasattr(current_user, 'is_online'):
        logout_user()
        return redirect(url_for('auth.admin_login'))

    # Get current settings
    primary_color = SiteSettings.get_setting('primary_color', '#4674C6')
    widget_icon_color = SiteSettings.get_setting('widget_icon_color', '#4674C6')
    company_name = SiteSettings.get_setting('company_name', 'Customer Support')
    welcome_message = SiteSettings.get_setting('welcome_message', 'Welcome to our customer support chat. How can we help you today?')
    logo_url = SiteSettings.get_setting('logo_url', '')
    widget_position = SiteSettings.get_setting('widget_position', 'right')

    # Handle form submission
    if request.method == 'POST':
        try:
            # Update settings
            primary_color = request.form.get('primary_color', primary_color)
            widget_icon_color = request.form.get('widget_icon_color', widget_icon_color)
            company_name = request.form.get('company_name', company_name)
            welcome_message = request.form.get('welcome_message', welcome_message)
            logo_url = request.form.get('logo_url', logo_url)
            widget_position = request.form.get('widget_position', 'right')

            # Save settings
            SiteSettings.set_setting('primary_color', primary_color)
            SiteSettings.set_setting('widget_icon_color', widget_icon_color)
            SiteSettings.set_setting('company_name', company_name)
            SiteSettings.set_setting('welcome_message', welcome_message)
            SiteSettings.set_setting('logo_url', logo_url)
            SiteSettings.set_setting('widget_position', widget_position)

            # Handle logo upload if provided
            if 'logo_file' in request.files:
                logo_file = request.files['logo_file']
                if logo_file and logo_file.filename:
                    # Generate a unique filename
                    filename = secure_filename(f"logo_{int(time.time())}_{logo_file.filename}")
                    image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                    logo_file.save(image_path)

                    # Get the correct URL path for the image
                    url_path = current_app.config.get('UPLOAD_URL_PATH', '/static/uploads')

                    # Set logo URL
                    logo_url = f"{url_path}/{filename}"
                    SiteSettings.set_setting('logo_url', logo_url)

            flash('Widget settings updated successfully!', 'success')
            return redirect(url_for('admin.widget_settings'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error saving widget settings: {str(e)}")
            flash(f'Error saving widget settings: {str(e)}', 'error')
            return redirect(url_for('admin.widget_settings'))

    # Get server URL for embed code
    server_url = request.url_root.rstrip('/')
    embed_code = f'<script src="{server_url}/embed.js"></script>'

    return render_template('admin/widget_settings.html',
                          admin=current_user,
                          primary_color=primary_color,
                          widget_icon_color=widget_icon_color,
                          company_name=company_name,
                          welcome_message=welcome_message,
                          logo_url=logo_url,
                          widget_position=widget_position,
                          embed_code=embed_code)

@admin_bp.route('/api/transcript/<room_id>')
@login_required
def get_transcript(room_id):
    """API endpoint to get chat transcript"""
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    room = Room.query.get_or_404(room_id)
    messages = Message.query.filter_by(room_id=room_id).order_by(Message.timestamp).all()

    # Check if text format is requested (for download)
    if request.args.get('format') == 'text':
        # Generate plain text transcript
        transcript = f"Chat Transcript - Room ID: {room_id}\n"
        transcript += f"Visitor IP: {room.visitor_ip or 'Unknown'}\n"
        transcript += f"Started: {room.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
        transcript += f"Last Activity: {room.last_activity.strftime('%Y-%m-%d %H:%M:%S')}\n"
        transcript += f"Status: {'Active' if room.is_active else 'Closed'}\n\n"
        transcript += "=" * 50 + "\n\n"

        for message in messages:
            sender = "Visitor"
            if not message.is_from_visitor:
                sender = "AI Assistant" if message.is_ai_generated else (message.sender_name or "Admin")

            transcript += f"[{message.timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {sender}:\n"
            transcript += f"{message.content}\n\n"

        # Create response with text file download
        response = make_response(transcript)
        response.headers['Content-Disposition'] = f'attachment; filename=chat-transcript-{room_id}.txt'
        response.headers['Content-Type'] = 'text/plain'
        return response

    # Return JSON format for display in UI
    messages_data = []
    for message in messages:
        messages_data.append({
            'id': message.id,
            'content': message.content,
            'timestamp': message.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'is_from_visitor': message.is_from_visitor,
            'is_ai_generated': message.is_ai_generated,
            'sender_name': message.sender_name
        })

    return jsonify({
        'room_id': room_id,
        'visitor_ip': room.visitor_ip,
        'created_at': room.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'last_activity': room.last_activity.strftime('%Y-%m-%d %H:%M:%S'),
        'is_active': room.is_active,
        'messages': messages_data
    })

@admin_bp.route('/api/admins', methods=['GET'])
@login_required
def api_get_admins():
    """API endpoint to get all admins"""
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    # Only super admins can see all admins
    if not current_user.is_super_admin:
        # Regular admins can only see their own info
        admin_data = [{
            'id': current_user.id,
            'name': current_user.name,
            'username': current_user.username,
            'email': current_user.email,
            'is_online': current_user.is_online,
            'last_seen': current_user.last_seen.isoformat() if current_user.last_seen else None,
            'date_joined': current_user.date_joined.isoformat() if current_user.date_joined else None,
            'active_chats_count': current_user.active_chats_count,
            'profile_image': current_user.profile_image,
            'push_enabled': current_user.push_enabled,
            'is_super_admin': current_user.is_super_admin
        }]
        return jsonify({'admins': admin_data})

    # Super admins can see all admins except themselves
    admins = Admin.query.filter(Admin.id != current_user.id).all()
    admin_data = []

    for admin in admins:
        admin_data.append({
            'id': admin.id,
            'name': admin.name,
            'username': admin.username,
            'email': admin.email,
            'is_online': admin.is_online,
            'last_seen': admin.last_seen.isoformat() if admin.last_seen else None,
            'date_joined': admin.date_joined.isoformat() if admin.date_joined else None,
            'active_chats_count': admin.active_chats_count,
            'profile_image': admin.profile_image,
            'push_enabled': admin.push_enabled,
            'is_super_admin': admin.is_super_admin
        })

    return jsonify({'admins': admin_data})

@admin_bp.route('/edit_admin/<admin_id>', methods=['GET', 'POST'])
@login_required
def edit_admin(admin_id):
    """Edit an admin's details"""
    # Only super admins can edit other admins
    if not hasattr(current_user, 'is_online') or not current_user.is_super_admin:
        flash('Only super admins can edit admin details', 'error')
        return redirect(url_for('admin.dashboard'))

    admin_to_edit = Admin.query.get_or_404(admin_id)

    if request.method == 'POST':
        name = request.form.get('name')
        username = request.form.get('username')
        email = request.form.get('email')
        push_enabled = request.form.get('push_enabled') == 'on'

        # Check if username is already taken by another admin
        if username != admin_to_edit.username:
            existing_username = Admin.query.filter_by(username=username).first()
            if existing_username and existing_username.id != admin_id:
                flash('Username already taken', 'error')
                return redirect(url_for('admin.edit_admin', admin_id=admin_id))

        # Check if email is already taken by another admin
        if email != admin_to_edit.email:
            existing_email = Admin.query.filter_by(email=email).first()
            if existing_email and existing_email.id != admin_id:
                flash('Email already registered', 'error')
                return redirect(url_for('admin.edit_admin', admin_id=admin_id))

        # Update profile image if provided
        if 'profile_image' in request.files:
            profile_image = request.files['profile_image']
            if profile_image and profile_image.filename:
                # Save the image
                filename = secure_filename(f"{admin_id}_{int(time.time())}_{profile_image.filename}")
                image_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
                profile_image.save(image_path)

                # Get the correct URL path for the image
                url_path = current_app.config.get('UPLOAD_URL_PATH', '/static/uploads')

                # Update profile image URL
                admin_to_edit.profile_image = f"{url_path}/{filename}"
                print(f"Updated profile image URL: {admin_to_edit.profile_image}")

        # Update password if provided
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if new_password and confirm_password:
            if new_password != confirm_password:
                flash('New passwords do not match', 'error')
                return redirect(url_for('admin.edit_admin', admin_id=admin_id))

            admin_to_edit.password = generate_password_hash(new_password, method='pbkdf2:sha256')

        # Update super admin status if applicable
        is_super_admin = request.form.get('is_super_admin') == 'on'
        if current_user.is_super_admin and current_user.id != admin_to_edit.id:
            admin_to_edit.is_super_admin = is_super_admin

        # Update other fields
        admin_to_edit.name = name
        admin_to_edit.username = username
        admin_to_edit.email = email
        admin_to_edit.push_enabled = push_enabled

        db.session.commit()
        flash('Admin updated successfully', 'success')
        return redirect(url_for('admin.register'))

    return render_template('admin/edit_admin.html', admin=current_user, admin_to_edit=admin_to_edit)

@admin_bp.route('/delete_admin/<admin_id>', methods=['POST'])
@login_required
def delete_admin(admin_id):
    """Delete an admin"""
    # Only super admins can delete admins
    if not hasattr(current_user, 'is_online') or not current_user.is_super_admin:
        return jsonify({'error': 'Only super admins can delete admins'}), 403

    # Cannot delete yourself
    if current_user.id == admin_id:
        return jsonify({'error': 'Cannot delete your own account'}), 403

    # Cannot delete another super admin
    admin_to_delete = Admin.query.get_or_404(admin_id)
    if admin_to_delete.is_super_admin:
        return jsonify({'error': 'Cannot delete a super admin'}), 403

    # Remove admin from all rooms
    for room in admin_to_delete.assigned_rooms:
        room.admins.remove(admin_to_delete)

    # Delete the admin
    db.session.delete(admin_to_delete)
    db.session.commit()

    return jsonify({'success': True})

@admin_bp.route('/api/initiate_chat', methods=['POST'])
@login_required
def initiate_chat():
    """API endpoint to initiate a chat with a visitor"""
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    data = request.json
    visitor_id = data.get('visitor_id')

    if not visitor_id:
        return jsonify({'success': False, 'error': 'Visitor ID is required'}), 400

    # Check if visitor exists
    visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
    if not visitor:
        return jsonify({'success': False, 'error': 'Visitor not found'}), 404

    # Check if there's already an active room for this visitor
    existing_room = Room.query.filter_by(visitor_id=visitor_id, is_active=True).first()
    if existing_room:
        # Just assign admin to existing room
        if current_user not in existing_room.admins:
            existing_room.admins.append(current_user)
            existing_room.has_admin = True
            existing_room.chat_mode = 'human'  # Switch to human mode
            db.session.commit()

        return jsonify({
            'success': True,
            'room_id': existing_room.id,
            'message': 'Joined existing chat room'
        })

    # Create a new room
    new_room = Room(
        visitor_id=visitor_id,
        visitor_ip=visitor.ip_address,
        has_admin=True,
        chat_mode='human'
    )
    new_room.admins.append(current_user)
    db.session.add(new_room)

    # Add system message
    system_message = Message(
        content="Chat initiated by support agent.",
        room_id=new_room.id,
        is_from_visitor=False,
        sender_name="System",
        is_read=True
    )
    db.session.add(system_message)
    db.session.commit()

    # Notify visitor about new chat
    from . import socketio
    socketio.emit('chat_initiated', {
        'room_id': new_room.id,
        'message': 'A support agent would like to chat with you.'
    }, room=visitor_id)

    return jsonify({
        'success': True,
        'room_id': new_room.id,
        'message': 'New chat room created'
    })

@admin_bp.route('/site_settings', methods=['GET', 'POST'])
@login_required
def site_settings_page():
    """Site settings configuration page"""
    if not hasattr(current_user, 'is_online'):
        logout_user()
        return redirect(url_for('auth.admin_login'))

    if request.method == 'POST':
        # Get form data
        company_name = request.form.get('company_name', '')
        welcome_message = request.form.get('welcome_message', '')
        ai_welcome_message = request.form.get('ai_welcome_message', '')
        away_message = request.form.get('away_message', '')
        timezone = request.form.get('timezone', 'UTC')
        time_format = request.form.get('time_format', '24h')

        # Check if time_format changed
        old_time_format = TimeFormat.get_format()
        time_format_changed = old_time_format != time_format

        # Save settings
        SiteSettings.set_setting('company_name', company_name)
        SiteSettings.set_setting('welcome_message', welcome_message)
        SiteSettings.set_setting('ai_welcome_message', ai_welcome_message)
        SiteSettings.set_setting('away_message', away_message)
        SiteSettings.set_setting('timezone', timezone)

        # Save time format
        TimeFormat.set_format(time_format)

        # Clear cache for business hours data
        try:
            if hasattr(data_cache, 'get') and 'business_hours_data' in data_cache:
                del data_cache['business_hours_data']
        except Exception as e:
            print(f"Warning: Could not clear cache: {str(e)}")

        # If time format changed, emit event to all connected clients to update their displays
        if time_format_changed:
            try:
                from .views import socketio
                socketio.emit('settings_updated', {
                    'time_format': time_format,
                    'timezone': timezone
                }, broadcast=True)
            except Exception as e:
                current_app.logger.error(f"Error emitting settings_updated event: {str(e)}")

        flash('Settings updated successfully!', 'success')
        return redirect(url_for('admin.site_settings_page'))

    # Get current settings
    settings = {
        'company_name': SiteSettings.get_setting('company_name', 'Your Company'),
        'welcome_message': SiteSettings.get_setting('welcome_message', 'Welcome to our chat! How can we help you today?'),
        'ai_welcome_message': SiteSettings.get_setting('ai_welcome_message', 'Hello! I\'m your AI assistant. How can I help you today?'),
        'away_message': SiteSettings.get_setting('away_message', 'We\'re currently unavailable. Please leave a message and we\'ll get back to you as soon as possible.'),
        'timezone': SiteSettings.get_setting('timezone', 'UTC'),
        'time_format': TimeFormat.get_format()
    }

    # Get all timezones
    import pytz
    timezones = pytz.all_timezones

    return render_template('admin/site_settings.html',
                          admin=current_user,
                          settings=settings,
                          timezones=timezones)

@admin_bp.route('/api/update_chat_tag', methods=['POST'])
@login_required
def update_chat_tag():
    """API endpoint to update the tag for a chat room"""
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    data = request.json
    room_id = data.get('room_id')
    tag = data.get('tag')

    if not room_id:
        return jsonify({'success': False, 'error': 'Room ID is required'}), 400

    room = Room.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'error': 'Room not found'}), 404

    # Update the chat tag
    room.chat_tag = tag
    db.session.commit()

    return jsonify({'success': True})

@admin_bp.route('/update-chat-mode', methods=['POST'])
@login_required
def update_chat_mode():
    """API endpoint for admin to update chat mode"""
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    data = request.json
    room_id = data.get('room_id')
    chat_mode = data.get('chat_mode')

    if not room_id:
        return jsonify({'success': False, 'error': 'Room ID is required'}), 400

    if chat_mode not in ['ai', 'human']:
        return jsonify({'success': False, 'error': 'Invalid chat mode'}), 400

    room = Room.query.get(room_id)
    if not room:
        return jsonify({'success': False, 'error': 'Room not found'}), 404

    # Update the chat mode
    room.chat_mode = chat_mode
    db.session.commit()

    # Emit socket event to notify all clients about the mode change
    from . import socketio
    socketio.emit('chat_mode_changed', {
        'room_id': room_id,
        'mode': chat_mode
    }, room=room_id)

    return jsonify({'success': True, 'mode': chat_mode})

@admin_bp.route('/api/chat_history_for_visitor/<room_id>', methods=['GET'])
@login_required
def chat_history_for_visitor(room_id):
    """API endpoint to get chat history for a visitor based on the current room"""
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    # Get the current room to find the visitor_id
    current_room = Room.query.get(room_id)
    if not current_room:
        return jsonify({'success': False, 'error': 'Room not found'}), 404

    visitor_id = current_room.visitor_id

    # Get all rooms for this visitor
    rooms = Room.query.filter_by(visitor_id=visitor_id).order_by(Room.created_at.desc()).all()

    if not rooms:
        return jsonify({'success': False, 'error': 'No chat history found for this visitor'}), 404

    chats_data = []
    for room in rooms:
        messages = Message.query.filter_by(room_id=room.id).order_by(Message.timestamp).all()

        # Skip rooms with no messages
        if not messages:
            continue

        # Get visitor information for this room
        visitor = Visitor.query.filter_by(visitor_id=room.visitor_id).first()
        visitor_name = visitor.name if visitor and visitor.name else None

        messages_data = []
        for message in messages:
            messages_data.append({
                'id': message.id,
                'content': message.content,
                'timestamp': message.timestamp.isoformat(),
                'is_from_visitor': message.is_from_visitor,
                'is_ai_generated': message.is_ai_generated,
                'is_system_message': message.is_system_message,
                'sender_name': message.sender_name,
                'admin_profile_image': message.admin_profile_image
            })

        chats_data.append({
            'room_id': room.id,
            'created_at': room.created_at.isoformat(),
            'last_activity': room.last_activity.isoformat(),
            'is_active': room.is_active,
            'message_count': len(messages),
            'chat_tag': room.chat_tag,
            'visitor_name': visitor_name,
            'messages': messages_data
        })

    return jsonify({
        'success': True,
        'visitor_id': visitor_id,
        'chats': chats_data
    })

@admin_bp.route('/api/knowledge_base/upload', methods=['POST'])
@admin_required
def api_knowledge_base_upload():
    """API for uploading text to the AI knowledge base"""
    try:
        data = request.json

        if not data or 'text' not in data:
            return jsonify({'success': False, 'error': 'No text provided'}), 400

        text = data['text']
        source = data.get('source', 'admin_upload')

        if not text or not isinstance(text, str):
            return jsonify({'success': False, 'error': 'Invalid text format'}), 400

        # Process the text using AIService
        from website.ai_service import ai_service
        chunk_count, message = ai_service.process_text_for_knowledge_base(text, source)

        if chunk_count > 0:
            return jsonify({
                'success': True,
                'chunk_count': chunk_count,
                'message': message
            })
        else:
            return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        current_app.logger.error(f"Error in knowledge base upload: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@admin_bp.route('/api/knowledge_base/upload_csv', methods=['POST'])
@admin_required
def api_knowledge_base_upload_csv():
    """API for uploading CSV file to the AI knowledge base"""
    try:
        # Check if file was uploaded
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400

        # Check file extension
        if not file.filename.lower().endswith('.csv'):
            return jsonify({'success': False, 'error': 'Only CSV files are allowed'}), 400

        # Get upload mode (add or rebuild)
        upload_mode = request.form.get('mode', 'add')  # 'add' or 'rebuild'

        # Create uploads directory if it doesn't exist
        upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'csv')
        os.makedirs(upload_dir, exist_ok=True)

        # Save the uploaded file
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{filename}"
        file_path = os.path.join(upload_dir, filename)
        file.save(file_path)

        try:
            # Process the CSV file using AIService
            from website.ai_service import ai_service

            source_name = f"csv_upload_{filename}"

            if upload_mode == 'rebuild':
                chunk_count, message = ai_service.rebuild_knowledge_base_from_csv(file_path, source_name)
            else:
                chunk_count, message = ai_service.process_csv_for_knowledge_base(file_path, source_name)

            # Clean up the uploaded file
            try:
                os.remove(file_path)
            except:
                pass

            if chunk_count > 0:
                return jsonify({
                    'success': True,
                    'chunk_count': chunk_count,
                    'message': message,
                    'mode': upload_mode,
                    'filename': file.filename
                })
            else:
                return jsonify({'success': False, 'error': message}), 500

        except Exception as e:
            # Clean up the uploaded file on error
            try:
                os.remove(file_path)
            except:
                pass
            raise e

    except Exception as e:
        current_app.logger.error(f"Error in CSV knowledge base upload: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@admin_bp.route('/knowledge_base')
@admin_required
def knowledge_base_management():
    """Knowledge Base Management Page"""
    return render_template('admin/knowledge_base.html')

@admin_bp.route('/api/chat_preview/<room_id>')
@login_required
def chat_preview(room_id):
    """API endpoint to get a preview of chat messages"""
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    # Get the current room
    current_room = Room.query.get_or_404(room_id)
    visitor_id = current_room.visitor_id

    # Get visitor information
    visitor = Visitor.query.filter_by(visitor_id=visitor_id).first()
    visitor_name = visitor.name if visitor and visitor.name else None
    visitor_email = visitor.email if visitor and visitor.email else None

    # Get all rooms for this visitor (both current and previous chats)
    all_rooms = Room.query.filter_by(visitor_id=visitor_id).order_by(Room.created_at.desc()).all()

    # Prepare data for all chats
    chats_data = []

    for room in all_rooms:
        # Get messages for this room, excluding system messages
        messages = Message.query.filter_by(
            room_id=room.id,
            is_system_message=False
        ).order_by(Message.timestamp).all()

        # Skip rooms with no messages
        if not messages:
            continue

        # Format messages for the response
        messages_data = []
        for message in messages:
            messages_data.append({
                'id': message.id,
                'content': message.content,
                'timestamp': message.timestamp.isoformat(),
                'is_from_visitor': message.is_from_visitor,
                'is_ai_generated': message.is_ai_generated,
                'sender_name': message.sender_name,
                'room_id': room.id,
                'is_current_chat': room.id == room_id
            })

        chats_data.append({
            'room_id': room.id,
            'created_at': room.created_at.isoformat(),
            'is_active': room.is_active,
            'is_current_chat': room.id == room_id,
            'messages': messages_data
        })

    return jsonify({
        'success': True,
        'room_id': room_id,
        'visitor_id': visitor_id,
        'visitor_name': visitor_name,
        'visitor_email': visitor_email,
        'visitor_ip': current_room.visitor_ip,
        'chats': chats_data
    })

@admin_bp.route('/api/check_admin_presence/<room_id>', methods=['GET'])
@login_required
def check_admin_presence(room_id):
    """API endpoint to check if any admin is still present in the room"""
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    # Check our real-time tracking first
    from . import views
    has_active_admins = False
    admin_count = 0

    if hasattr(views, 'active_admin_rooms'):
        if room_id in views.active_admin_rooms:
            admin_count = len(views.active_admin_rooms[room_id])
            has_active_admins = admin_count > 0

    # Get the room object
    room = Room.query.get_or_404(room_id)

    # If the has_admin flag doesn't match our real-time tracking, update it
    if has_active_admins != room.has_admin:
        room.has_admin = has_active_admins
        db.session.commit()

        # If no active admins but has_admin was True, emit admin left event
        if not has_active_admins:
            from . import socketio
            socketio.emit('admin_left_chat', {
                'room_id': room_id,
                'admin_id': current_user.id,
                'admin_name': current_user.name
            }, to=None)

    return jsonify({
        'success': True,
        'has_admin': room.has_admin,
        'admin_count': admin_count
    })

@admin_bp.route('/api/dashboard_stats', methods=['GET'])
@login_required
def dashboard_stats():
    """API endpoint to get real-time dashboard statistics"""
    if not hasattr(current_user, 'is_online'):
        return jsonify({'error': 'Authentication required'}), 401

    try:
        # Get real-time active visitors based on socket connections
        from . import views

        # Count active visitors with real connections
        active_visitor_ids = []
        active_rooms_ids = []
        for visitor_id, data in views.active_connections.items():
            if data['sids']:  # Only consider visitors with active socket connections
                active_visitor_ids.append(visitor_id)
                room_id = data.get('room_id')
                if room_id:
                    active_rooms_ids.append(room_id)

        # Count active visitors and chats
        active_visitors_count = len(active_visitor_ids)

        # Only count rooms that have active connections
        active_chats = Room.query.filter(
            Room.id.in_(active_rooms_ids),
            Room.is_active == True,
            Room.chat_mode == 'human'
        ).count()

        total_admins = Admin.query.count()

        # Get online admins with more details
        online_admins = Admin.query.filter_by(is_online=True).all()
        online_admins_data = []

        for admin in online_admins:
            # Count active chats for this admin
            active_chat_count = db.session.query(admin_rooms_association).filter(
                admin_rooms_association.c.admin_id == admin.id,
                admin_rooms_association.c.room_id.in_(active_rooms_ids)
            ).count()

            online_admins_data.append({
                'id': admin.id,
                'name': admin.name,
                'profile_image': admin.profile_image,
                'active_chats': active_chat_count,
                'last_seen': format_time(admin.last_seen) if admin.last_seen else None
            })

        return jsonify({
            'success': True,
            'active_visitors_count': active_visitors_count,
            'active_chats_count': active_chats,
            'total_admins': total_admins,
            'current_user_online': current_user.is_online,
            'online_admins': online_admins_data
        })
    except Exception as e:
        current_app.logger.error(f"Error in dashboard_stats: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to get dashboard stats'
        }), 500

@admin_bp.route('/api/check_online_admins', methods=['GET'])
def check_online_admins():
    """API endpoint to check if any admins are online"""
    # Get all online admins
    online_admins = Admin.query.filter_by(is_online=True).all()

    # Check if any admins are online
    has_online_admins = len(online_admins) > 0

    return jsonify({
        'success': True,
        'has_online_admins': has_online_admins,
        'online_admins_count': len(online_admins)
    })

@admin_bp.route('/api/check_human_mode_availability', methods=['GET'])
def check_human_mode_availability():
    """API endpoint to check if human mode is available based on business hours and admin availability"""
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
            from .models import SiteSettings
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
        from .models import BusinessHours
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

    # Prepare response with detailed information
    reason = None
    if not human_mode_available:
        if not has_online_admins and not within_business_hours:
            reason = "No admin is available and outside of business hours"
        elif not has_online_admins:
            reason = "No admin is available right now"
        elif not within_business_hours:
            reason = "Outside of business hours"

    return jsonify({
        'success': True,
        'human_mode_available': human_mode_available,
        'has_online_admins': has_online_admins,
        'within_business_hours': within_business_hours,
        'reason': reason
    })