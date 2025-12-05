#!/usr/bin/env python3
"""
Database migration script to add cost tracking fields to ProductionLog table.
"""
import sqlite3
import os

# Database path
db_path = '/Users/gabik/workspace/projects/costflow/waste_tracking.db'

# Check if database exists
if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    exit(1)

# Connect to the database
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    # Check if columns already exist
    cursor.execute("PRAGMA table_info(production_log)")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]

    # Add total_cost column if it doesn't exist
    if 'total_cost' not in column_names:
        cursor.execute("ALTER TABLE production_log ADD COLUMN total_cost FLOAT")
        print("Added column: total_cost")
    else:
        print("Column total_cost already exists")

    # Add cost_per_unit column if it doesn't exist
    if 'cost_per_unit' not in column_names:
        cursor.execute("ALTER TABLE production_log ADD COLUMN cost_per_unit FLOAT")
        print("Added column: cost_per_unit")
    else:
        print("Column cost_per_unit already exists")

    # Add cost_details column if it doesn't exist
    if 'cost_details' not in column_names:
        cursor.execute("ALTER TABLE production_log ADD COLUMN cost_details TEXT")
        print("Added column: cost_details")
    else:
        print("Column cost_details already exists")

    # Commit the changes
    conn.commit()
    print("\nMigration completed successfully!")

except Exception as e:
    print(f"Error during migration: {e}")
    conn.rollback()

finally:
    conn.close()