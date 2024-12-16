import os
import sys

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.append(os.path.join(BASE_DIR, "../app"))

import csv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import RawMaterial, Category, db  # Adjust your import paths if needed

# CSV file path
CSV_FILE_PATH = "raw_materials.csv"
sqlite_db_path = os.path.join(BASE_DIR, "../instance/waste_tracking.db")

DATABASE_URLS = {
    "dev": f"sqlite:///{sqlite_db_path}",
    "prod": os.getenv("DATABASE_URL", "postgresql://user:password@localhost/dbname")
}

ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")  # Set "prod" for production
DATABASE_URL = DATABASE_URLS[ENVIRONMENT]

# Database engine
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

def get_or_create_category(category_name):
    """Get an existing category or create it if it doesn't exist."""
    category = session.query(Category).filter_by(name=category_name).first()
    if not category:
        category = Category(name=category_name)
        session.add(category)
        session.commit()
    return category

def import_raw_materials(csv_file_path):
    """Import raw materials from a CSV file."""
    try:
        with open(csv_file_path, "r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                name = row.get("Raw material")
                unit = row.get("Unit")
                cost_per_unit = row.get("Price per unit")
                category_name = row.get("Category")

                if not name or not unit or not cost_per_unit or not category_name:
                    print(f"Skipping invalid row: {row}")
                    continue

                category = get_or_create_category(category_name)

                existing_material = session.query(RawMaterial).filter_by(name=name).first()
                if existing_material:
                    print(f"Raw material '{name}' already exists. Skipping.")
                    continue

                raw_material = RawMaterial(
                    name=name,
                    unit=unit,
                    cost_per_unit=float(cost_per_unit),
                    category_id=category.id
                )

                session.add(raw_material)

            session.commit()
            print(f"Raw materials imported successfully. {name}")
    except Exception as e:
        session.rollback()
        print(f"An error occurred: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    import_raw_materials(CSV_FILE_PATH)

