from flask import Flask, g, send_from_directory, url_for, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from .extensions import socketio
from cachetools import TTLCache, cached
import json
import os
import hashlib
from werkzeug.security import generate_password_hash
from sqlalchemy import text
from flask import request
from datetime import datetime, timedelta
import time
import functools

db = SQLAlchemy()

# Create a TTL cache for templates and data
template_cache = TTLCache(maxsize=100, ttl=300)  # Cache up to 100 items for 5 minutes
data_cache = TTLCache(maxsize=100, ttl=300)  # Cache for API data

# Custom caching decorator
def cached_view(timeout=300):
    """Decorator for caching view functions"""
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            # Create a cache key based on the function name and arguments
            cache_key = f"{f.__name__}_{str(args)}_{str(sorted(kwargs.items()))}"

            # For logged-in users, add user ID to cache key to make it user-specific
            from flask_login import current_user
            if hasattr(current_user, 'id') and current_user.is_authenticated:
                cache_key += f"_user_{current_user.id}"

            # Add request method and path to make cache key more specific
            cache_key += f"_{request.method}_{request.path}"

            # Convert to a hash to keep it a reasonable length
            cache_key = hashlib.md5(cache_key.encode()).hexdigest()

            # Try to get from cache
            if cache_key in template_cache:
                return template_cache[cache_key]

            # If not in cache, call the original function
            result = f(*args, **kwargs)

            # Cache the result
            template_cache[cache_key] = result

            return result
        return decorated_function
    return decorator

def create_app():
    app = Flask(__name__)

    # Load configuration from config.json
    with open('config.json', 'r') as config_file:
        config = json.load(config_file)

    app.config['SECRET_KEY'] = config['SECRET_KEY']
    app.config['UPLOAD_FOLDER'] = config['UPLOAD_FOLDER']
    app.config['UPLOAD_URL_PATH'] = config.get('UPLOAD_URL_PATH', '/static/uploads')

    # SESSION-BASED CHAT ISOLATION: Configure Flask sessions properly
    app.config['SESSION_PERMANENT'] = False
    app.config['SESSION_USE_SIGNER'] = True
    app.config['SESSION_KEY_PREFIX'] = 'chat_session:'
    app.config['SESSION_COOKIE_NAME'] = 'chat_session'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SECURE'] = False  # Set to True in production with HTTPS
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    # Use PostgreSQL exclusively
    app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{os.environ.get('POSTGRES_USER')}:{os.environ.get('POSTGRES_PASSWORD')}@{os.environ.get('POSTGRES_SERVER')}:{os.environ.get('POSTGRES_PORT')}/{os.environ.get('POSTGRES_DB')}"

    # Performance optimizations for SQLAlchemy
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,  # Check connection validity before using
        'pool_recycle': 300,    # Recycle connections after 5 minutes
        'pool_size': 10,        # Connection pool size
        'max_overflow': 20      # Max additional connections
    }

    app.config['ADMIN_USERNAME'] = config['ADMIN_USERNAME']
    app.config['ADMIN_PASSWORD'] = config['ADMIN_PASSWORD']

    # Set API keys from environment
    app.config['OPENAI_API_KEY'] = os.environ.get('OPENAI_API_KEY', '')
    app.config['GROQ_API_KEY'] = os.environ.get('GROQ_API_KEY', '')

    # Configure Jinja2 for better performance
    app.jinja_env.cache = {}  # Enable template caching
    app.jinja_env.auto_reload = False  # Disable auto reloading
    app.jinja_env.trim_blocks = True  # Trim blocks
    app.jinja_env.lstrip_blocks = True  # Strip whitespace

    # Add request timing middleware
    @app.before_request
    def before_request():
        g.start_time = time.time()

        # Handle CORS preflight requests
        if request.method == 'OPTIONS':
            response = Response()
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Browser-UUID'
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            return response

    @app.after_request
    def after_request(response):
        if hasattr(g, 'start_time'):
            elapsed = time.time() - g.start_time
            # Add timing header for debugging
            response.headers['X-Request-Time'] = f"{elapsed:.4f}s"

            # Log slow requests (over 1 second)
            if elapsed > 1.0:
                app.logger.warning(f"Slow request: {request.path} took {elapsed:.4f}s")

        # Add CORS headers for cross-origin requests (needed for embed script)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Browser-UUID'
        response.headers['Access-Control-Allow-Credentials'] = 'true'

        # Add cache headers for static files
        if request.path.startswith('/static/'):
            # Cache static files for 1 week
            response.cache_control.max_age = 60 * 60 * 24 * 7  # 7 days
            response.cache_control.public = True
            response.headers['Expires'] = (datetime.utcnow() + timedelta(days=7)).strftime('%a, %d %b %Y %H:%M:%S GMT')

        return response

    # Serve static files with versioning for better caching
    @app.context_processor
    def inject_static_url():
        def static_url(filename):
            # Get the full path to the file
            filepath = os.path.join(app.static_folder, filename)

            # If file doesn't exist, return the normal URL
            if not os.path.exists(filepath):
                return url_for('static', filename=filename)

            # Generate a hash of the file content for versioning
            try:
                with open(filepath, 'rb') as f:
                    file_hash = hashlib.md5(f.read()).hexdigest()[:8]

                # Add the hash as a query parameter
                return url_for('static', filename=filename, v=file_hash)
            except:
                # If any error occurs, return the normal URL
                return url_for('static', filename=filename)

        return dict(static_url=static_url)

    # Add caching decorator for views
    @app.context_processor
    def inject_cache_helpers():
        def cache_data(key, value=None):
            """Get or set data in the cache"""
            if value is not None:
                data_cache[key] = value
                return value
            return data_cache.get(key)

        def clear_cache(key=None):
            """Clear specific key or entire cache"""
            if key is not None and key in data_cache:
                del data_cache[key]
            else:
                data_cache.clear()

        return dict(cache_data=cache_data, clear_cache=clear_cache)

    # Print debug info about environment variables
    print(f"OPENAI_API_KEY set: {'Yes' if os.environ.get('OPENAI_API_KEY') else 'No'}")
    print(f"GROQ_API_KEY set: {'Yes' if os.environ.get('GROQ_API_KEY') else 'No'}")
    print(f"PostgreSQL connection: {app.config['SQLALCHEMY_DATABASE_URI']}")
    print(f"Upload folder: {app.config['UPLOAD_FOLDER']}")
    print(f"Upload URL path: {app.config['UPLOAD_URL_PATH']}")
    print(f"Caching enabled: TTLCache with 300s timeout")
    print(f"Template caching enabled: {app.jinja_env.cache is not None}")
    print(f"SQLAlchemy pool size: {app.config['SQLALCHEMY_ENGINE_OPTIONS']['pool_size']}")
    print(f"Static file caching enabled: 7 days")

    db.init_app(app)

    # Initialize socketio with the app - optimized for high concurrency
    socketio.init_app(app,
                     ping_timeout=60,
                     ping_interval=25,
                     cors_allowed_origins="*",
                     cors_credentials=True,
                     max_http_buffer_size=1e6,  # 1MB buffer for large messages
                     transports=['websocket', 'polling'],  # Prefer websockets
                     async_mode='threading',  # Use threading for better concurrency
                     logger=False,  # Disable verbose logging for performance
                     engineio_logger=False,  # Disable engine.io logging for performance
                     allow_upgrades=True,
                     cookie=None)  # Disable cookies for cross-origin

    with app.app_context():
        # First, import models after db is initialized
        from .models import Message, Room, Admin, Visitor, BusinessHours, SiteSettings, TimeFormat
        from .views import views
        from .auth import auth
        from .admin import admin_bp
        from .utils import format_time

        app.register_blueprint(views, url_prefix='/')
        app.register_blueprint(auth, url_prefix='/')
        app.register_blueprint(admin_bp, url_prefix='/admin')

        login_manager = LoginManager()
        login_manager.login_view = 'auth.admin_login'
        login_manager.init_app(app)

        @login_manager.user_loader
        def load_user(id):
            # Only admins can log in now
            return Admin.query.get(str(id))

        # Add context processor for datetime
        @app.context_processor
        def inject_now():
            from datetime import datetime
            return {'now': datetime.now()}

        # Add context processor for time format
        @app.context_processor
        def inject_time_format():
            def get_time_format():
                try:
                    return TimeFormat.get_format()
                except:
                    return '24h'  # Default to 24h format

            return {
                'time_format': get_time_format(),
                'format_time': format_time
            }

        # Add context processor for widget settings
        @app.context_processor
        def inject_widget_settings():
            """Inject widget settings into all templates"""
            try:
                return {
                    'widget_icon_color': SiteSettings.get_setting('widget_icon_color', '#4674C6'),
                    'primary_color': SiteSettings.get_setting('primary_color', '#4674C6'),
                    'company_name': SiteSettings.get_setting('company_name', 'Customer Support')
                }
            except:
                return {
                    'widget_icon_color': '#4674C6',
                    'primary_color': '#4674C6',
                    'company_name': 'Customer Support'
                }

        # Check if admin table exists before trying to create it
        try:
            result = db.session.execute(text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'admin')"))
            admin_table_exists = result.scalar()

            if not admin_table_exists:
                # Create all tables if admin table doesn't exist
                db.create_all()
                print("Database tables created successfully")

                # Create default admin
                admin = Admin(
                    name="Administrator",
                    username="admin",
                    email=app.config['ADMIN_USERNAME'],
                    password=generate_password_hash(app.config['ADMIN_PASSWORD'], method='pbkdf2:sha256'),
                    is_online=False
                )
                db.session.add(admin)
                db.session.commit()
                print("Default admin created successfully")
            else:
                # Check if admin exists
                try:
                    # Try to query by email only to avoid username column issues
                    result = db.session.execute(
                        text("SELECT id FROM admin WHERE email = :email"),
                        {"email": app.config['ADMIN_USERNAME']}
                    )
                    admin_exists = result.scalar() is not None

                    if not admin_exists:
                        # Create default admin with raw SQL to handle potential schema differences
                        import uuid

                        admin_id = str(uuid.uuid4())
                        db.session.execute(
                            text("""
                                INSERT INTO admin (id, name, email, password, is_online, username, is_super_admin)
                                VALUES (:id, :name, :email, :password, :is_online, :username, :is_super_admin)
                            """),
                            {
                                'id': admin_id,
                                'name': 'Administrator',
                                'email': app.config['ADMIN_USERNAME'],
                                'password': generate_password_hash(app.config['ADMIN_PASSWORD'], method='pbkdf2:sha256'),
                                'is_online': False,
                                'username': 'admin',
                                'is_super_admin': True
                            }
                        )
                        db.session.commit()
                        print("Default admin created successfully")
                except Exception as e:
                    print(f"Error checking for admin: {e}")
                    # Continue execution even if there's an error

        except Exception as e:
            print(f"Error setting up database: {e}")
            # Continue execution even if there's an error

        # Ensure upload folder exists
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

        # Define Socket.IO event handlers
        @socketio.on('connect')
        def handle_connect():
            print('Client connected:', request.sid)

        @socketio.on('disconnect')
        def handle_disconnect():
            print('Client disconnected:', request.sid)

        @socketio.on('admin_status_change')
        def handle_admin_status_change(data):
            """Handle admin status changes and broadcast to all clients"""
            admin_id = data.get('admin_id')
            is_online = data.get('is_online', False)

            # Broadcast to all clients
            socketio.emit('admin_status_changed', {
                'admin_id': admin_id,
                'is_online': is_online,
                'timestamp': format_time(datetime.now())
            })

            print(f"Admin {admin_id} status changed to {'online' if is_online else 'offline'}")

    return app