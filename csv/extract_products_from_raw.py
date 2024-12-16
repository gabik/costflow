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
CSV_FILE_PATH = "products_raw.csv"

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
            reader = list(csv.reader(file))

            # Extract products
            row_offset = 84  # Jump for product names
            col_product_name = 7  # Column index for product names (0-based)
            col_products_per_recipe = 14  # Column index for products per recipe (0-based)
            col_selling_price = 14  # Column index for selling price (0-based)

            for i in range(0, len(reader), row_offset):
                try:
                    # Row indices for the required data
                    product_name_row = 4 + i
                    products_per_recipe_row = 6 + i
                    selling_price_row = 8 + i

                    # Extract data
                    product_name = reader[product_name_row][col_product_name]
                    products_per_recipe = reader[products_per_recipe_row][col_products_per_recipe]
                    selling_price = reader[selling_price_row][col_selling_price]

                    print(f"{product_name}, {products_per_recipe}, {selling_price}")

                    if not product_name.strip():
                        print(f"Skipping empty product at row {product_name_row}.")
                        continue

#                    # Check for existing product
#                    existing_product = session.query(Product).filter_by(name=product_name).first()
#                    if existing_product:
#                        print(f"Product '{product_name}' already exists. Skipping.")
#                        continue
#
#                    # Create and add product to the session
#                    product = Product(
#                        name=product_name.strip(),
#                        products_per_recipe=int(products_per_recipe),
#                        selling_price_per_unit=float(selling_price)
#                    )
#                    session.add(product)

                except IndexError:
                    print(f"Row out of range or malformed data at index {i}. Skipping.")

            # Commit session
#            session.commit()
#            print("Products imported successfully.")
    except Exception as e:
        session.rollback()
        print(f"An error occurred: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    import_products(CSV_FILE_PATH)

