from __future__ import annotations

import numpy as np

from fledermap.derive.sites import cluster_points


def test_two_nearby_points_form_one_cluster() -> None:
    # ~6.8m apart at this latitude.
    points = np.array([[13.4000, 52.5000], [13.4001, 52.5000]])

    labels = cluster_points(points, eps_m=75.0, min_points=2)

    assert labels[0] == labels[1]
    assert labels[0] != -1


def test_two_far_points_are_noise() -> None:
    # Berlin and Munich — hundreds of km apart.
    points = np.array([[13.4, 52.5], [11.58, 48.14]])

    labels = cluster_points(points, eps_m=75.0, min_points=2)

    assert list(labels) == [-1, -1]


def test_empty_input_returns_empty_array() -> None:
    labels = cluster_points(np.empty((0, 2)), eps_m=75.0, min_points=2)
    assert len(labels) == 0


def test_eps_is_metres_not_degrees() -> None:
    """Regression for the pitfall parent spec section 7 pins: eps must be
    metres. Two points ~68m apart at Berlin's latitude: noise under a 30m eps,
    one cluster under a 100m eps."""
    points = np.array([[13.4000, 52.5000], [13.4010, 52.5000]])

    tight = cluster_points(points, eps_m=30.0, min_points=2)
    loose = cluster_points(points, eps_m=100.0, min_points=2)

    assert list(tight) == [-1, -1]
    assert loose[0] == loose[1]
    assert loose[0] != -1
