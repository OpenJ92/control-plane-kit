"""Select intact unittest module suites and account for exhaustive CI shards.

Run only inside the owning Docker harness. This is test scheduling, not a
replacement for unittest, package integrity, or the other package-gate phases.
"""

import argparse
import hashlib
import inspect
import json
import math
from pathlib import Path, PurePosixPath
import platform
import re
import sys
import time
import unittest


FORMAT = "cpk.operations-test-shard.v1"
MAX_REPORT_BYTES = 16 * 1024 * 1024
SUCCESS_STATUSES = {"passed", "skipped", "expected-failure"}


def _count(value):
    if type(value) is not int or not 1 <= value <= 16:
        raise ValueError("worker count must be between 1 and 16")


def shard_index(origin, count):
    _count(count)
    if (type(origin) is not str or len(origin) > 512 or "\\" in origin
            or not origin.startswith("tests/") or not origin.endswith(".py")
            or PurePosixPath(origin).as_posix() != origin
            or ".." in PurePosixPath(origin).parts):
        raise ValueError("test origin must be a canonical package-relative POSIX path")
    return int.from_bytes(hashlib.sha256(origin.encode("utf-8")).digest()[:4], "big") % count


def _cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _cases(item)
        elif isinstance(item, unittest.TestCase):
            yield item
        else:
            raise ValueError("unsupported unittest collection member")


class _OriginLoader(unittest.TestLoader):
    def __init__(self, root):
        super().__init__()
        self.root = root
        self.modules = []

    def loadTestsFromModule(self, module, *, pattern=None):
        suite = super().loadTestsFromModule(module, pattern=pattern)
        origin = Path(module.__file__).resolve().relative_to(self.root).as_posix()
        shard_index(origin, 1)
        self.modules.append((origin, suite))
        return suite


def discover(root):
    root = Path(root).resolve()
    loader = _OriginLoader(root)
    complete = loader.discover(str(root / "tests"))
    if loader.errors:
        raise ValueError("unittest collection failed; no shard can establish coverage")
    origins = [origin for origin, _ in loader.modules]
    if len(origins) != len(set(origins)):
        raise ValueError("ambiguous collection-origin module")
    # Confirm that the recorded module suites cover standard discovery exactly.
    # Keep suites intact for execution; flatten only their occurrence inventory.
    if ([id(case) for case in _cases(complete)] !=
            [id(case) for _, suite in loader.modules for case in _cases(suite)]):
        raise ValueError("unsupported discovery structure")
    return loader.modules


def _approved_skip(test, reason, root, approvals):
    # Match the declaring class/method, including inherited and imported cases.
    for cls in type(test).__mro__:
        if cls in (unittest.TestCase, object):
            continue
        path = inspect.getsourcefile(cls)
        if path is None:
            continue
        try:
            origin = Path(path).resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        class_id = f"{origin}::{cls.__name__}"
        if approvals.get(class_id) == reason:
            return True
        if test._testMethodName in cls.__dict__:
            if approvals.get(class_id + "." + test._testMethodName) == reason:
                return True
    return False


class _OccurrenceResult(unittest.TextTestResult):
    def __init__(self, *args, planned, root, approvals, **kwargs):
        super().__init__(*args, **kwargs)
        self.planned = planned
        self.root = root
        self.approvals = approvals
        self.actual = []
        self.position = 0
        self.active = None

    def startTest(self, test):
        if self.position >= len(self.planned) or self.planned[self.position][0] is not test:
            raise ValueError("executed occurrence differs from selected discovery")
        self.active = {"occurrence": self.planned[self.position][1], "status": "error"}
        self.position += 1
        self.started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.active["seconds"] = time.monotonic() - self.started
        self.actual.append(self.active)
        self.active = None
        super().stopTest(test)

    def _status(self, value):
        if self.active is not None:
            self.active["status"] = value

    def addSuccess(self, test):
        self._status("passed")
        super().addSuccess(test)

    def addFailure(self, test, err):
        self._status("failed")
        super().addFailure(test, err)

    def addError(self, test, err):
        self._status("error")
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self._status("failed")
        super().addSubTest(test, subtest, err)

    def addSkip(self, test, reason):
        approved = isinstance(test, unittest.TestCase) and _approved_skip(
            test, reason, self.root, self.approvals)
        self._status("skipped" if approved else "unapproved-skip")
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self._status("expected-failure")
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self._status("unexpected-success")
        super().addUnexpectedSuccess(test)


def run_shard(root, index, count, coordinates, *, stream=None):
    _count(count)
    if type(index) is not int or not 0 <= index < count:
        raise ValueError("worker index is outside its count")
    root = Path(root).resolve()
    modules = discover(root)
    discovered, planned, selected_suites = [], [], []
    for origin, suite in modules:
        entries = [(case, [origin, ordinal, case.id()])
                   for ordinal, case in enumerate(_cases(suite))]
        discovered.extend(row for _, row in entries)
        if shard_index(origin, count) == index:
            selected_suites.append(suite)
            planned.extend(entries)
    if not discovered or not planned:
        raise ValueError("empty discovery or worker selection")
    approval_path = root / "tests/approved_skips.json"
    approvals = {row["identity"]: row["reason"] for row in
                 json.loads(approval_path.read_text())} if approval_path.exists() else {}

    def result_factory(*args, **kwargs):
        return _OccurrenceResult(*args, planned=planned, root=root, approvals=approvals, **kwargs)

    result = unittest.TextTestRunner(stream=stream, verbosity=2, durations=0,
                                    resultclass=result_factory).run(unittest.TestSuite(selected_suites))
    successful = (result.wasSuccessful() and not result.shouldStop
                  and [row["occurrence"] for row in result.actual] == [row for _, row in planned]
                  and all(row["status"] in SUCCESS_STATUSES for row in result.actual))
    return {"format": FORMAT, "coordinates": coordinates, "index": index, "count": count,
            "discovered": discovered, "results": result.actual, "successful": successful}


def _coordinates(value, source, architecture):
    if (type(value) is not dict or set(value) != {"source", "architecture", "inputs", "python"}
            or value["source"] != source or value["architecture"] != architecture
            or not re.fullmatch(r"[0-9a-f]{40}", source)
            or not re.fullmatch(r"[0-9a-f]{40}", architecture)
            or type(value["inputs"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", value["inputs"])
            or type(value["python"]) is not str or not re.fullmatch(r"[0-9.]+", value["python"])):
        raise ValueError("worker source/dependency coordinates differ")


def validate_reports(reports, count, source, architecture):
    _count(count)
    if len(reports) != count:
        raise ValueError("missing or additional worker reports")
    indices, baseline, coordinates, total, skipped = set(), None, None, 0, 0
    for report in reports:
        if (type(report) is not dict or set(report) != {
                "format", "coordinates", "index", "count", "discovered", "results", "successful"}
                or report["format"] != FORMAT or report["successful"] is not True
                or type(report["count"]) is not int or report["count"] != count
                or type(report["index"]) is not int or not 0 <= report["index"] < count
                or report["index"] in indices):
            raise ValueError("worker did not produce one successful report")
        indices.add(report["index"])
        _coordinates(report["coordinates"], source, architecture)
        if coordinates is None:
            coordinates = report["coordinates"]
        if report["coordinates"] != coordinates:
            raise ValueError("workers used different inputs or Python versions")
        full = report["discovered"]
        if type(full) is not list or not full:
            raise ValueError("empty discovery")
        ordinals, closed, previous = {}, set(), None
        for row in full:
            if (type(row) is not list or len(row) != 3 or type(row[1]) is not int
                    or type(row[2]) is not str or not 0 < len(row[2]) <= 2048):
                raise ValueError("invalid discovered occurrence")
            origin, ordinal, _ = row
            shard_index(origin, count)
            if origin != previous:
                if origin in closed:
                    raise ValueError("noncontiguous module occurrences")
                closed.add(origin)
                previous = origin
            if ordinal != ordinals.get(origin, 0):
                raise ValueError("missing or duplicate discovery ordinal")
            ordinals[origin] = ordinal + 1
        if baseline is None:
            baseline = full
        if full != baseline:
            raise ValueError("workers discovered different suites")
        expected = [row for row in full if shard_index(row[0], count) == report["index"]]
        actual = report["results"]
        if type(actual) is not list or not expected or len(actual) != len(expected):
            raise ValueError("worker coverage is incomplete or empty")
        for row, occurrence in zip(actual, expected):
            if (type(row) is not dict or set(row) != {"occurrence", "status", "seconds"}
                    or row["occurrence"] != occurrence or row["status"] not in SUCCESS_STATUSES
                    or type(row["seconds"]) not in (int, float)
                    or not math.isfinite(row["seconds"]) or row["seconds"] < 0):
                raise ValueError("worker did not terminally account for its exact occurrence")
            skipped += row["status"] == "skipped"
        total += len(actual)
    if total != len(baseline):
        raise ValueError("aggregate coverage differs from complete discovery")
    return {"workers": count, "tests": total, "skipped": skipped, "source": source}


def input_fingerprint(paths):
    digest = hashlib.sha256()
    for index, path in enumerate(paths):
        path = Path(path)
        if not path.exists():
            raise ValueError("source input is unavailable")
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for item in files:
            if any(part in {"__pycache__", ".git"} for part in item.parts) or not item.is_file():
                continue
            label = item.relative_to(path).as_posix() if path.is_dir() else item.name
            digest.update(f"{index}:{label}\0".encode())
            digest.update(hashlib.sha256(item.read_bytes()).digest())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--root", type=Path, default=Path.cwd())
    run.add_argument("--index", type=int, required=True)
    run.add_argument("--count", type=int, required=True)
    run.add_argument("--report", type=Path, required=True)
    run.add_argument("--input", action="append", required=True)
    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--reports", type=Path, required=True)
    aggregate.add_argument("--count", type=int, required=True)
    for command in (run, aggregate):
        command.add_argument("--source", required=True)
        command.add_argument("--architecture", required=True)
    args = parser.parse_args()
    if args.command == "run":
        coordinates = {"source": args.source, "architecture": args.architecture,
                       "inputs": input_fingerprint(args.input), "python": platform.python_version()}
        _coordinates(coordinates, args.source, args.architecture)
        report = run_shard(args.root, args.index, args.count, coordinates)
        data = json.dumps(report, sort_keys=True, ensure_ascii=True).encode()
        if len(data) > MAX_REPORT_BYTES:
            raise ValueError("worker report exceeds bounded capacity")
        args.report.write_bytes(data + b"\n")
        return 0 if report["successful"] else 1
    reports = []
    paths = sorted(args.reports.glob("shard-*.json"))
    if len(paths) != args.count:
        raise ValueError("missing or additional worker reports")
    for path in paths:
        if path.stat().st_size > MAX_REPORT_BYTES:
            raise ValueError("worker report exceeds bounded capacity")
        reports.append(json.loads(path.read_bytes()))
    print(json.dumps(validate_reports(reports, args.count, args.source, args.architecture), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError):
        print("Operations shard evidence is unavailable or inconsistent", file=sys.stderr)
        sys.exit(1)
