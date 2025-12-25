import sys
import os

# Add parent directory to path to allow importing app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from sqlalchemy import text

app = create_app()

def add_indexes():
    print("Starting database index optimization...")
    
    indexes = [
        # Stock Log Optimization (Crucial for bulk fetch)
        "CREATE INDEX IF NOT EXISTS idx_stock_log_material_ts ON stock_log (raw_material_id, timestamp);",
        "CREATE INDEX IF NOT EXISTS idx_stock_log_product_ts ON stock_log (product_id, timestamp);",
        "CREATE INDEX IF NOT EXISTS idx_stock_log_supplier ON stock_log (supplier_id);",
        
        # Product Component Optimization (For joins)
        "CREATE INDEX IF NOT EXISTS idx_product_comp_product ON product_component (product_id);",
        "CREATE INDEX IF NOT EXISTS idx_product_comp_component ON product_component (component_id);",
        
        # Production Log Optimization
        "CREATE INDEX IF NOT EXISTS idx_prod_log_product_ts ON production_log (product_id, timestamp);",
        
        # Weekly Sales Optimization
        "CREATE INDEX IF NOT EXISTS idx_weekly_sales_product ON weekly_product_sales (product_id);",
        "CREATE INDEX IF NOT EXISTS idx_weekly_sales_week ON weekly_product_sales (weekly_cost_id);"
    ]
    
    with app.app_context():
        # Check DB type
        is_sqlite = 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']
        
        for sql in indexes:
            try:
                # SQLite doesn't support IF NOT EXISTS in older versions, but we'll try standard syntax
                # If it fails, we catch it
                print(f"Executing: {sql}")
                db.session.execute(text(sql))
                db.session.commit()
                print("  -> Success")
            except Exception as e:
                db.session.rollback()
                print(f"  -> Skipped/Failed: {e}")
                
    print("Index optimization complete.")

if __name__ == "__main__":
    add_indexes()
