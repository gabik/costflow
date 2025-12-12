from flask import Blueprint, jsonify
from app.models import db
from flask_babel import gettext as _

fix_sequences_bp = Blueprint("fix_sequences", __name__)

@fix_sequences_bp.route("/migrate_fix_sequences")
def fix_sequences():
    """Fix all PostgreSQL sequences to match the maximum ID in each table"""
    try:
        # List of tables with auto-incrementing IDs
        tables = [
            "audit_log",
            "category",
            "labor",
            "packaging",
            "packaging_supplier",
            "preproduct",
            "product",
            "product_component",
            "production_log",
            "raw_material",
            "raw_material_supplier",
            "stock_audit",
            "stock_log",
            "supplier",
            "weekly_labor_cost",
            "weekly_labor_entry",
            "weekly_production",
            "weekly_product_sales"
        ]

        fixed_sequences = []
        errors = []

        for table_name in tables:
            try:
                # Get the maximum ID from the table
                result = db.session.execute(
                    db.text(f"SELECT COALESCE(MAX(id), 0) as max_id FROM {table_name}")
                ).fetchone()

                max_id = result.max_id if result else 0

                # Set the sequence to max_id + 1
                # PostgreSQL sequence naming convention: tablename_id_seq
                sequence_name = f"{table_name}_id_seq"

                db.session.execute(
                    db.text(f"SELECT setval(\"{sequence_name}\", :max_id, TRUE)"),
                    {"max_id": max_id}
                )

                fixed_sequences.append({
                    "table": table_name,
                    "max_id": max_id,
                    "next_id": max_id + 1
                })

            except Exception as e:
                errors.append({
                    "table": table_name,
                    "error": str(e)
                })

        db.session.commit()

        return jsonify({
            "status": "success",
            "message": _("Fixed {} sequences").format(len(fixed_sequences)),
            "fixed_sequences": fixed_sequences,
            "errors": errors if errors else None
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
