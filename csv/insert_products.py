import csv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
import sys

# Add the app directory to the Python path
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.append(os.path.join(BASE_DIR, "../app"))

# Import models
from models import Product  # Adjust this import for the actual model path

# CSV File Path
CSV_FILE_PATH = "products.csv"

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

def import_products(csv_file_path):
    """Import products from a messy CSV file."""
    try:
        with open(csv_file_path, "r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                name = row.get("Name")
                products_per_recipe = row.get("Products Per Recipe")
                selling_price_per_unit = row.get("Selling Price")

                if not name or not products_per_recipe or not selling_price_per_unit:
                    print(f"Skipping invalid row: {row}")
                    continue

                existing_product = session.query(Product).filter_by(name=name).first()
                if existing_product:
                    print(f"Product '{name}' already exists. Skipping.")
                    continue

                products = Product(
                    name=name,
                    products_per_recipe=int(products_per_recipe),
                    selling_price_per_unit=float(selling_price_per_unit)
                )

                session.add(products)

        session.commit()
        print("Product data imported successfully.")
    except Exception as e:
        session.rollback()
        print(f"An error occurred: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    import_products(CSV_FILE_PATH)

