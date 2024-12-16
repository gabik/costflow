import os
import sys
import csv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add the app directory to the Python path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.append(os.path.join(BASE_DIR, "../app"))

# Import models
from models import Labor  # Adjusted import for the model file

# CSV file path
CSV_FILE_PATH = "labor.csv"

# Database connection settings
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

def import_labor(csv_file_path):
    """Import labor data from a CSV file."""
    try:
        with open(csv_file_path, "r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                name = row.get("Name")
                base_hourly_rate = row.get("Base Hourly Rate")
                additional_hourly_rate = row.get("Additional Hourly Rate")

                if not name or not base_hourly_rate or additional_hourly_rate is None:
                    print(f"Skipping invalid row: {row}")
                    continue

                existing_labor = session.query(Labor).filter_by(name=name).first()
                if existing_labor:
                    print(f"Labor '{name}' already exists. Skipping.")
                    continue

                labor = Labor(
                    name=name,
                    base_hourly_rate=float(base_hourly_rate),
                    additional_hourly_rate=float(additional_hourly_rate)
                )

                session.add(labor)

            session.commit()
            print("Labor data imported successfully.")
    except Exception as e:
        session.rollback()
        print(f"An error occurred: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    import_labor(CSV_FILE_PATH)

