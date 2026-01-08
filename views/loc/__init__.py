# views/loc/__init__.py

from .locations_page import init_location_page
from .locations_api import init_location_api
from .item_location_api import init_item_location_api
from .admin_store_config import init_admin_store_config
from .locations_actions import init_location_actions
from .shelves_page import init_location_shelves_page
from .zones_page import init_location_zones_page


def init_location_views(app, get_db):
    """
    Register location-related routes (zones/shelves/assignments etc.)

    Usage:
      from views.loc import init_location_views
      init_location_views(app, get_db)
    """
    init_location_page(app, get_db)
    init_location_api(app, get_db)
    init_item_location_api(app, get_db)
    init_admin_store_config(app, get_db)
    init_location_actions(app, get_db)
    init_location_shelves_page(app, get_db)
    init_location_zones_page(app, get_db)