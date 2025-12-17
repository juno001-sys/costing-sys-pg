# views/inventory_locations/__init__.py

from .locations_page import init_inventory_locations_page
from .locations_api import init_inventory_locations_api
from .locations_actions import init_inventory_locations_actions


def init_inventory_location_views(app, get_db):
    """
    Register inventory location/config screens + APIs.
    Called from app.py:
      from views.inventory_locations import init_inventory_location_views
      init_inventory_location_views(app, get_db)
    """
    init_inventory_locations_page(app, get_db)
    init_inventory_locations_api(app, get_db)
    init_inventory_locations_actions(app, get_db)
