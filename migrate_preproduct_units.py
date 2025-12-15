#!/usr/bin/env python
"""Migration script to fix preproduct units from kg to unit"""

from app import create_app, db
from app.models import Product, ProductComponent

app = create_app()

with app.app_context():
    # Find all preproducts with wrong unit
    preproducts = Product.query.filter_by(is_preproduct=True).all()

    print(f"Found {len(preproducts)} preproducts to check:\n")

    for preproduct in preproducts:
        old_unit = preproduct.unit

        # Preproducts should be in 'unit' not kg/g
        if preproduct.unit in ['kg', 'g']:
            preproduct.unit = 'unit'
            print(f"✓ Updated '{preproduct.name}': {old_unit} → unit")
        else:
            print(f"  '{preproduct.name}' already has unit: {preproduct.unit}")

    # Also check if products_per_recipe makes sense
    print("\nChecking products_per_recipe values:")
    for preproduct in preproducts:
        if not preproduct.products_per_recipe or preproduct.products_per_recipe == 0:
            print(f"  WARNING: '{preproduct.name}' has products_per_recipe = {preproduct.products_per_recipe}")
            # Set default to 1 if missing
            if not preproduct.products_per_recipe:
                preproduct.products_per_recipe = 1
                print(f"    → Set to 1")

    # Commit changes
    try:
        db.session.commit()
        print("\n✅ Migration completed successfully!")
    except Exception as e:
        db.session.rollback()
        print(f"\n❌ Error during migration: {e}")