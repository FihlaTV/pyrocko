# http://pyrocko.org - GPLv3
#
# The Pyrocko Developers, 21st Century
# ---|P------/S----------~Lg----------
from __future__ import absolute_import, division, print_function

import math

from pyrocko import gf, orthodrome as od
from .error import CannotCreatePath


def remake_dir(dpath, force):
    try:
        return gf.store.remake_dir(dpath, force)

    except gf.CannotCreate as e:
        raise CannotCreatePath(str(e))


def distance_range(s_lat, s_lon, s_radius, t_lat, t_lon, t_radius):

    if None in (s_radius, s_lat, s_lon, t_radius, t_lat, t_lon):
        return 0.0, od.earthradius * math.pi

    else:
        dist_centers = od.distance_accurate50m(s_lat, s_lon, t_lat, t_lon)

        dist_max = min(
            math.pi*od.earthradius,
            dist_centers + s_radius + t_radius)

        dist_min = max(
            0.0,
            dist_centers - s_radius - t_radius)

        return dist_min, dist_max


def suitable_store_ids(stores, distance_range, source_depth_range):
    ok = []
    for store in stores:
        if store.config.is_suitable(
                source_depth_range=source_depth_range,
                distance_range=distance_range):

            ok.append(store.config.id)

    return ok


__all__ = [
    'remake_dir',
    'distance_range',
    'suitable_store_ids']
