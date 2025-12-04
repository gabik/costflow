#!/usr/bin/env python
"""
Migration script to unify Products and Premakes into a single table.
This script:
1. Adds new columns to Product table (is_product, is_premake, batch_size)
2. Migrates all Premake records to Product table
3. Updates all foreign key references
4. Migrates PremakeComponent records to ProductComponent
"""

from app import create_app
from app.models import db, Product, Premake, PremakeComponent, ProductComponent, StockLog, ProductionLog, StockAudit
from sqlalchemy import text

def migrate_premakes_to_products():
    app = create_app()
    with app.app_context():
        print("Starting migration: Unifying Products and Premakes...")

        try:
            # Step 1: Add new columns to Product table if they don't exist
            print("Step 1: Adding new columns to Product table...")
            with db.engine.connect() as conn:
                # Check if columns exist first
                result = conn.execute(text("PRAGMA table_info(product)"))
                columns = [row[1] for row in result]

                if 'is_product' not in columns:
                    conn.execute(text("ALTER TABLE product ADD COLUMN is_product BOOLEAN DEFAULT 1"))
                    conn.commit()
                    print("  - Added is_product column")

                if 'is_premake' not in columns:
                    conn.execute(text("ALTER TABLE product ADD COLUMN is_premake BOOLEAN DEFAULT 0"))
                    conn.commit()
                    print("  - Added is_premake column")

                if 'batch_size' not in columns:
                    conn.execute(text("ALTER TABLE product ADD COLUMN batch_size FLOAT"))
                    conn.commit()
                    print("  - Added batch_size column")

            # Step 2: Set is_product=True for all existing products
            print("Step 2: Setting is_product=True for existing products...")
            Product.query.update({Product.is_product: True, Product.is_premake: False})
            db.session.commit()
            print(f"  - Updated {Product.query.count()} existing products")

            # Step 3: Migrate all Premake records to Product table
            print("Step 3: Migrating Premake records to Product table...")
            premakes = Premake.query.all()
            premake_id_mapping = {}  # Old premake ID -> New product ID

            for premake in premakes:
                # Create new Product from Premake
                new_product = Product(
                    name=premake.name,
                    category_id=premake.category_id,
                    products_per_recipe=1,  # Default value for premakes
                    selling_price_per_unit=0,  # Premakes typically aren't sold
                    is_product=False,
                    is_premake=True,
                    batch_size=premake.batch_size
                )
                db.session.add(new_product)
                db.session.flush()  # Get the new ID

                premake_id_mapping[premake.id] = new_product.id
                print(f"  - Migrated premake '{premake.name}' (ID: {premake.id} -> {new_product.id})")

            db.session.commit()
            print(f"  - Migrated {len(premakes)} premakes to products")

            # Step 4: Migrate PremakeComponent records to ProductComponent
            print("Step 4: Migrating PremakeComponent records to ProductComponent...")
            premake_components = PremakeComponent.query.all()
            migrated_components = 0

            for comp in premake_components:
                if comp.premake_id in premake_id_mapping:
                    # When component references another premake, update the ID
                    component_id = comp.component_id
                    if comp.component_type == 'premake' and comp.component_id in premake_id_mapping:
                        component_id = premake_id_mapping[comp.component_id]

                    new_component = ProductComponent(
                        product_id=premake_id_mapping[comp.premake_id],
                        component_type=comp.component_type,
                        component_id=component_id,
                        quantity=comp.quantity
                    )
                    db.session.add(new_component)
                    migrated_components += 1

            db.session.commit()
            print(f"  - Migrated {migrated_components} premake components")

            # Step 5: Update ProductComponent records where component_type='premake'
            print("Step 5: Updating ProductComponent premake references...")
            product_components = ProductComponent.query.filter_by(component_type='premake').all()
            updated_components = 0

            for comp in product_components:
                if comp.component_id in premake_id_mapping:
                    comp.component_id = premake_id_mapping[comp.component_id]
                    updated_components += 1

            db.session.commit()
            print(f"  - Updated {updated_components} product component references")

            # Step 6: Update StockLog premake_id references
            print("Step 6: Updating StockLog references...")

            # First, add product_id column to StockLog if it doesn't exist
            with db.engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(stock_log)"))
                columns = [row[1] for row in result]

                # Note: product_id might already exist for some reason, check first
                if 'product_id' not in columns:
                    # Add product_id column if it doesn't exist
                    conn.execute(text("ALTER TABLE stock_log ADD COLUMN product_id INTEGER"))
                    conn.commit()
                    print("  - Added product_id column to stock_log")

            # Update StockLog records
            stock_logs = StockLog.query.filter(StockLog.premake_id != None).all()
            updated_logs = 0
            for log in stock_logs:
                if log.premake_id in premake_id_mapping:
                    # We'll use raw SQL to set product_id since the model might not have it yet
                    db.session.execute(
                        text("UPDATE stock_log SET product_id = :product_id WHERE id = :log_id"),
                        {"product_id": premake_id_mapping[log.premake_id], "log_id": log.id}
                    )
                    updated_logs += 1

            db.session.commit()
            print(f"  - Updated {updated_logs} stock log entries")

            # Step 7: Update ProductionLog premake_id references
            print("Step 7: Updating ProductionLog references...")
            production_logs = ProductionLog.query.filter(ProductionLog.premake_id != None).all()
            updated_prod_logs = 0

            for log in production_logs:
                if log.premake_id in premake_id_mapping:
                    # ProductionLog already has product_id field, use it
                    if not log.product_id:  # Only update if product_id is not set
                        log.product_id = premake_id_mapping[log.premake_id]
                        updated_prod_logs += 1

            db.session.commit()
            print(f"  - Updated {updated_prod_logs} production log entries")

            # Step 8: Update StockAudit premake_id references (if any)
            print("Step 8: Updating StockAudit references...")

            # Add product_id column to StockAudit if needed
            with db.engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(stock_audit)"))
                columns = [row[1] for row in result]

                if 'product_id' not in columns:
                    conn.execute(text("ALTER TABLE stock_audit ADD COLUMN product_id INTEGER"))
                    conn.commit()
                    print("  - Added product_id column to stock_audit")

            stock_audits = StockAudit.query.filter(StockAudit.premake_id != None).all()
            updated_audits = 0

            for audit in stock_audits:
                if audit.premake_id in premake_id_mapping:
                    db.session.execute(
                        text("UPDATE stock_audit SET product_id = :product_id WHERE id = :audit_id"),
                        {"product_id": premake_id_mapping[audit.premake_id], "audit_id": audit.id}
                    )
                    updated_audits += 1

            db.session.commit()
            print(f"  - Updated {updated_audits} stock audit entries")

            # Step 9: Update Product.migrated_to_premake_id references
            print("Step 9: Updating Product.migrated_to_premake_id references...")

            # Add migrated_to_product_id column
            with db.engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(product)"))
                columns = [row[1] for row in result]

                if 'migrated_to_product_id' not in columns:
                    conn.execute(text("ALTER TABLE product ADD COLUMN migrated_to_product_id INTEGER"))
                    conn.commit()
                    print("  - Added migrated_to_product_id column")

            # Update references
            products_with_migration = Product.query.filter(Product.migrated_to_premake_id != None).all()
            updated_migrations = 0

            for product in products_with_migration:
                if product.migrated_to_premake_id in premake_id_mapping:
                    db.session.execute(
                        text("UPDATE product SET migrated_to_product_id = :new_id WHERE id = :product_id"),
                        {"new_id": premake_id_mapping[product.migrated_to_premake_id], "product_id": product.id}
                    )
                    updated_migrations += 1

            db.session.commit()
            print(f"  - Updated {updated_migrations} migration references")

            print("\nMigration completed successfully!")
            print(f"Summary:")
            print(f"  - Migrated {len(premakes)} premakes to products")
            print(f"  - Created {migrated_components} new product components")
            print(f"  - Updated {updated_components} component references")
            print(f"  - Updated {updated_logs} stock logs")
            print(f"  - Updated {updated_prod_logs} production logs")
            print(f"  - Updated {updated_audits} stock audits")
            print(f"  - Updated {updated_migrations} migration references")

            print("\nIMPORTANT: The Premake and PremakeComponent tables still exist.")
            print("They should be removed after verifying the migration was successful.")

            # Return mapping for verification
            return premake_id_mapping

        except Exception as e:
            print(f"\nError during migration: {str(e)}")
            db.session.rollback()
            raise

if __name__ == "__main__":
    mapping = migrate_premakes_to_products()
    print("\nPremake ID Mapping (old -> new):")
    for old_id, new_id in mapping.items():
        print(f"  Premake {old_id} -> Product {new_id}")