import os
import sys
import csv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.append(os.path.join(BASE_DIR, "../app"))

from models import Packaging  # Adjusted import for the model file

# CSV file path
CSV_FILE_PATH = "packaging.csv"

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

def import_packaging(csv_file_path):
    """Import packaging data from a CSV file."""
    try:
        with open(csv_file_path, "r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                name = row.get("Name")
                quantity_per_package = row.get("Quantity Per Package")
                price_per_package = row.get("Price Per Package")

                if not name or not quantity_per_package or not price_per_package:
                    print(f"Skipping invalid row: {row}")
                    continue

                existing_packaging = session.query(Packaging).filter_by(name=name).first()
                if existing_packaging:
                    print(f"Packaging '{name}' already exists. Skipping.")
                    continue

                packaging = Packaging(
                    name=name,
                    quantity_per_package=int(quantity_per_package),
                    price_per_package=float(price_per_package)
                )

                session.add(packaging)

            session.commit()
            print("Packaging data imported successfully.")
    except Exception as e:
        session.rollback()
        print(f"An error occurred: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    import_packaging(CSV_FILE_PATH)
