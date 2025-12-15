#!/usr/bin/env python
"""Debug script to check preproduct cost calculation"""

from app import create_app, db
from app.models import Product, ProductComponent, RawMaterial
from app.routes.utils import calculate_prime_cost

app = create_app()

with app.app_context():
    # Get all preproducts
    preproducts = Product.query.filter_by(is_preproduct=True).all()

    print(f"\nFound {len(preproducts)} preproducts:\n")

    for preproduct in preproducts:
        print(f"=" * 60)
        print(f"Preproduct: {preproduct.name} (ID: {preproduct.id})")
        print(f"  - products_per_recipe: {preproduct.products_per_recipe}")
        print(f"  - unit: {preproduct.unit if hasattr(preproduct, 'unit') else 'N/A'}")
        print(f"  - batch_size: {preproduct.batch_size if hasattr(preproduct, 'batch_size') else 'N/A'}")
        print(f"  - is_preproduct: {preproduct.is_preproduct}")

        # Get components
        components = ProductComponent.query.filter_by(product_id=preproduct.id).all()
        print(f"\n  Components ({len(components)}):")

        for comp in components:
            print(f"    - Type: {comp.component_type}, Quantity: {comp.quantity}, Component ID: {comp.component_id}")

            if comp.component_type == 'raw_material':
                material = RawMaterial.query.get(comp.component_id)
                if material:
                    print(f"      Material: {material.name}, Unit: {material.unit}")
            elif comp.component_type == 'product':
                sub_product = Product.query.get(comp.component_id)
                if sub_product:
                    print(f"      Product: {sub_product.name}, is_preproduct: {sub_product.is_preproduct}")

        # Calculate cost
        try:
            cost = calculate_prime_cost(preproduct)
            print(f"\n  Calculated cost per unit: {cost}")
        except Exception as e:
            print(f"\n  ERROR calculating cost: {e}")

        print()

    # Check products that use preproducts
    print("=" * 60)
    print("\nProducts using preproducts:\n")

    preproduct_components = ProductComponent.query.filter_by(component_type='product').all()

    for comp in preproduct_components:
        product = Product.query.get(comp.product_id)
        preproduct = Product.query.get(comp.component_id)

        if product and preproduct and preproduct.is_preproduct:
            print(f"  Product '{product.name}' uses preproduct '{preproduct.name}'")
            print(f"    - Quantity: {comp.quantity}")
            print(f"    - Preproduct is_preproduct flag: {preproduct.is_preproduct}")

            # Try to calculate cost
            try:
                cost = calculate_prime_cost(preproduct)
                print(f"    - Preproduct cost: {cost}")
                print(f"    - Total cost in recipe: {comp.quantity * cost}")
            except Exception as e:
                print(f"    - ERROR: {e}")
            print()