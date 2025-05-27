#!/usr/bin/env python3
"""
Database migration script to add session_id column to Room table
for implementing session-based chat isolation.
"""

import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def run_migration():
    """Add session_id column to Room table"""
    
    # Create database connection
    db_url = f"postgresql://{os.environ.get('POSTGRES_USER')}:{os.environ.get('POSTGRES_PASSWORD')}@{os.environ.get('POSTGRES_SERVER')}:{os.environ.get('POSTGRES_PORT')}/{os.environ.get('POSTGRES_DB')}"
    
    engine = create_engine(db_url)
    
    try:
        with engine.connect() as conn:
            # Check if session_id column already exists
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'room' AND column_name = 'session_id'
            """))
            
            if result.fetchone():
                print("session_id column already exists in room table")
                return
            
            # Add session_id column to room table
            print("Adding session_id column to room table...")
            conn.execute(text("""
                ALTER TABLE room 
                ADD COLUMN session_id VARCHAR(128)
            """))
            
            # Commit the transaction
            conn.commit()
            print("Successfully added session_id column to room table")
            
            # Update existing rooms with a placeholder session_id
            print("Updating existing rooms with placeholder session_id...")
            conn.execute(text("""
                UPDATE room 
                SET session_id = CONCAT('legacy_', id) 
                WHERE session_id IS NULL
            """))
            
            conn.commit()
            print("Successfully updated existing rooms")
            
    except Exception as e:
        print(f"Error running migration: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("Running session_id migration for Room table...")
    run_migration()
    print("Migration completed successfully!")
