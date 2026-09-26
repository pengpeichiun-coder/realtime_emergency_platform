"""Keep database longitude/X, latitude/Y ordering out of API contracts."""
import math
from geoalchemy2 import WKBElement, WKTElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point


def point(latitude: float, longitude: float) -> WKBElement:
    if not (math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError('Invalid geographic coordinates')
    return from_shape(Point(longitude, latitude), srid=4326)


def coordinates(value: WKBElement | WKTElement) -> tuple[float, float]:
    shape = to_shape(value)
    return shape.y, shape.x
