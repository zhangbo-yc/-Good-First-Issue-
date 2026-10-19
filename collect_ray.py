#!/usr/bin/env python3
"""Collect Ray source and all current GFI-labelled issues, then verify saved labels."""
import sys

import collect_gfi as core
from collect_dask_manim import collect_and_audit as collect_project_and_audit


PROJECTS = ({'repo': 'ray-project/ray'},)


def collect_and_audit(project, client, output, state, source, refresh, limit):
    return collect_project_and_audit({**project, 'preserve_missing_source_links': True},
                                    client, output, state, source, refresh, limit)


def main(argv=None):
    return core.main(argv, projects=PROJECTS, project_runner=collect_and_audit, default_state='all',
                     summary_name='collection_summary_ray.json', description=__doc__)


if __name__ == '__main__':
    sys.exit(main())
