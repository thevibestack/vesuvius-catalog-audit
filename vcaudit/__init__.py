"""vcaudit -- an anonymous, metadata-only auditor for the Vesuvius open-data catalogue.

The question this package answers: *can what is published be reproduced from what
is published?* It reads nothing but object names, ``.zattrs``/``.zarray`` and the
headers of ``x.tif`` grids -- no voxel data, no credentials, no dependencies
outside the standard library -- and reports, per published surface volume and per
published segment, whether the catalogue's derived fields are consistent with the
files it points at.

Checks, all tied to an open report in the project's tracker:

* ``1727``  a surface volume's canvas is not reproducible from its published mesh
* ``1892``  a surface volume declares pyramid levels and holds no chunks
* ``1893``  a voxel size in micrometres is written under a ``nanometer`` label
* ``1734``  ``bbox_transformed`` is the ``-1`` sentinel scaled as a coordinate
* ``1730``  a segment declares a volume whose scan postdates the segment

Usage::

    python -m vcaudit all --outdir audit
"""

__version__ = "1.0.0"
