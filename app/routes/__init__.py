from flask import Blueprint

main_blueprint = Blueprint('main', __name__)

from . import dashboard, inventory, products, labor, production, finance, admin
