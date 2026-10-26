"""Fail fixture CI when a JUnit report is empty, unsuccessful, or skipped."""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


def verify_reports(paths, required_classes):
    cases = [case for path in paths for case in ET.parse(path).getroot().iter('testcase')]
    if not cases:
        raise ValueError('No test cases found in reports')
    for case in cases:
        for outcome in ('skipped', 'failure', 'error'):
            if case.find(outcome) is not None:
                raise ValueError(f"{outcome}: {case.get('classname', '')}.{case.get('name', '')}")
    classes = {case.get('classname') for case in cases}
    missing = set(required_classes) - classes
    if missing:
        raise ValueError(f"Required test classes did not run: {', '.join(sorted(missing))}")
    return len(cases)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+')
    parser.add_argument('--require-class', action='append', default=[])
    args = parser.parse_args()
    count = verify_reports(args.reports, args.require_class)
    print(f'Checked {count} test cases: no failures, errors, or skips')
