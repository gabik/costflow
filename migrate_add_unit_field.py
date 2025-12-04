#!/usr/bin/env python
"""
Migration script to add 'unit' field to Product table and make selling_price_per_unit nullable.

Run this script to update your database schema after pulling the latest changes.
"""

import os
import sys
from app import create_app, db
from app.models import Product
from sqlalchemy import text

def run_migration():
    """Run the database migration."""
    app = create_app()

    with app.app_context():
        try:
            # Check if unit column already exists
            inspector = db.inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('product')]

            if 'unit' not in columns:
                print("Adding 'unit' column to Product table...")

                # Add unit column
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE product ADD COLUMN unit VARCHAR(20)"))
                    conn.commit()
                    print("✓ Added 'unit' column")

                    # Try to set default unit for existing premakes (if is_premake column exists)
                    try:
                        conn.execute(text("UPDATE product SET unit = 'kg' WHERE is_premake = 1 AND unit IS NULL"))
                        conn.commit()
                        print("✓ Set default unit 'kg' for existing premakes")
                    except:
                        # If is_premake doesn't exist, just set a default for all null units
                        conn.execute(text("UPDATE product SET unit = 'kg' WHERE unit IS NULL"))
                        conn.commit()
                        print("✓ Set default unit 'kg' for items with null unit")
            else:
                print("'unit' column already exists")

            # Check if selling_price_per_unit is nullable
            col_info = next((col for col in inspector.get_columns('product') if col['name'] == 'selling_price_per_unit'), None)
            if col_info and not col_info['nullable']:
                print("Making 'selling_price_per_unit' nullable...")

                # Different syntax for different databases
                if 'sqlite' in str(db.engine.url):
                    print("Note: SQLite doesn't support ALTER COLUMN directly.")
                    print("The column will work as nullable in the application even if the constraint isn't changed.")
                else:
                    # For PostgreSQL or MySQL
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE product ALTER COLUMN selling_price_per_unit DROP NOT NULL"))
                        conn.commit()
                        print("✓ Made 'selling_price_per_unit' nullable")
            else:
                print("'selling_price_per_unit' is already nullable or doesn't exist")

            print("\n✅ Migration completed successfully!")

        except Exception as e:
            print(f"\n❌ Migration failed: {str(e)}")
            print("\nIf you're using SQLite, you may need to recreate the database:")
            print("1. Back up your data")
            print("2. Delete the .db file")
            print("3. Run the application to recreate tables with new schema")
            sys.exit(1)

if __name__ == "__main__":
    print("Starting database migration...")
    print("-" * 50)
    run_migration()