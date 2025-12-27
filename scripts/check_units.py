from app import create_app
from app.models import Product

app = create_app()

with app.app_context():
    preproducts = Product.query.filter_by(is_preproduct=True).all()
    print(f"{'ID':<5} | {'Name':<30} | {'Unit':<10} | {'Is Premake':<10} | {'Is Product':<10}")
    print("-" * 80)
    for p in preproducts:
        print(f"{p.id:<5} | {p.name:<30} | {p.unit:<10} | {p.is_premake:<10} | {p.is_product:<10}")
