from faker import Faker
import random, requests, json, string, secrets
from datetime import datetime
from . import db
from .models import Room, Admin
from PIL import Image



def get_rooms_count():
    all_rooms = Room.query.all()
    num_rooms = len(all_rooms)
    return num_rooms


def get_public_rooms():
    public_rooms = Room.query.filter_by(is_private=False).all()
    return public_rooms

#########################admin stuff###########################
def is_admin(admin_id, room_id):
    # Check if the admin with admin_id is an admin in the specified room_id
    room = Room.query.filter_by(id=room_id).first()
    if room:
        admin = Admin.query.filter_by(id=admin_id).first()
        return admin and admin in room.admins
    return False

# Function to add new admins to the room
def add_admins(room, new_admin_ids):
    existing_admin_ids = [admin.id for admin in room.admins]
    for admin_id in new_admin_ids:
        admin_id = admin_id.strip()
        if len(admin_id) == 36 and admin_id not in existing_admin_ids:
            admin = Admin.query.get(admin_id)
            if admin:
                room.admins.append(admin)

def remove_admin(room, admin_id):
    admin_to_remove = Admin.query.get(admin_id)
    if admin_to_remove in room.admins:
        room.admins.remove(admin_to_remove)
        db.session.commit()

def get_admins(room):
    if room.admins:
        return room.admins # Returns admins as list of admin objects [<admin_id>]
    else:
        return None
    
def handle_command(command, room):
    command = command.lower()  # Convert the command to lowercase
    if command.startswith("/admins"):
        admins_list = get_admins(room)
        if admins_list:
            admin_names = [admin.name for admin in admins_list]
            response_message = f"Admins in the room: {', '.join(admin_names)}"
        else:
            response_message = "There are no admins in the room."
        return response_message