from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_user, login_required, logout_user, current_user
from . import db
from .models import Admin
from werkzeug.security import check_password_hash
import json

auth = Blueprint('auth', __name__)

@auth.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated and hasattr(current_user, 'is_online'):
        return redirect(url_for('admin.dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Print out debug information
        print(f"Login attempt with email: {email}")
        
        # For testing, also try to load admin credentials from config
        try:
            with open('config.json', 'r') as config_file:
                config = json.load(config_file)
                admin_email = config.get('ADMIN_USERNAME')
                admin_password = config.get('ADMIN_PASSWORD')
                print(f"Config has admin email: {admin_email}")
                
                # If credentials match config directly, create admin account if missing
                if email == admin_email and password == admin_password:
                    admin = Admin.query.filter_by(email=email).first()
                    if not admin:
                        from werkzeug.security import generate_password_hash
                        admin = Admin(
                            name="Administrator",
                            email=email,
                            password=generate_password_hash(password, method='pbkdf2:sha256'),
                            is_online=False
                        )
                        db.session.add(admin)
                        db.session.commit()
                        print(f"Created new admin account for {email}")
        except Exception as e:
            print(f"Error loading config: {e}")
        
        admin = Admin.query.filter_by(email=email).first()
        print(f"Found admin in database: {admin is not None}")
        
        if admin:
            if check_password_hash(admin.password, password):
                print("Password matches!")
                flash('Logged in successfully!', category='success')
                login_user(admin, remember=True)
                admin.is_online = True
                db.session.commit()
                return redirect(url_for('admin.dashboard'))
            else:
                print("Password doesn't match")
                flash('Incorrect password.', category='error')
        else:
            flash('No admin account found with that email.', category='error')
    
    return render_template('admin/login.html')

@auth.route('/logout')
@login_required
def logout():
    if hasattr(current_user, 'is_online'):
        current_user.is_online = False
        db.session.commit()
    logout_user()
    return redirect(url_for('auth.admin_login'))

