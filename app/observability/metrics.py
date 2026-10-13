"""In-process counters, gauges, and windowed latency summaries.

A registry is a dictionary of numbers and `render` turns it into the Prometheus
text exposition format, which every self-hosted scraper already reads. There is
no client library, no push, and no agent: a metric costs one dictionary lookup
and an addition, and collecting it costs one HTTP request to the process that
holds it.

Two properties matter more than any feature.

A registry cannot grow without bound. Metrics are declared up front, an
undeclared name or an unexpected label is a programming error rather than a new
series, each metric keeps at most `max_series` label combinations, and each
summary keeps at most `window` recent observations. Nothing a caller sends can
add a series, so a scrape endpoint can never become the way a process runs out
of memory.

A registry cannot carry content. Every value is a number and every label is one
the application chose from a bounded vocabulary. There is no field a query, a
document, or a credential could travel in, which is what makes the output safe
to ship somewhere else.

The platform's own metric names live in `app.observability.instruments`. This
module knows only how to hold numbers.
"""

import re
import threading
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from math import ceil, isfinite
from typing import Final, Literal

Kind = Literal["counter", "gauge", "summary"]
# One metric's labels, sorted by name, so two equal label sets are one series.
LabelValues = tuple[tuple[str, str], ...]

# A summary's quantiles are computed over this many recent observations. Large
# enough that a p99 means something, small enough that the window is a few tens
# of kilobytes per series and sorting it on a scrape is free.
DEFAULT_WINDOW: Final = 1_024
# Per metric, not per registry. Every declared metric's labels are bounded by
# the application, so reaching this means a bug rather than traffic.
DEFAULT_MAX_SERIES: Final = 128
QUANTILES: Final[tuple[float, ...]] = (0.5, 0.95, 0.99)
# The exposition format's own content type, which is what makes a scraper parse
# the body rather than store it.
CONTENT_TYPE: Final = "text/plain; version=0.0.4; charset=utf-8"

_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
# Prometheus gives this label its own meaning inside a summary, so a metric may
# not declare one and collide with the quantiles the registry emits.
_RESERVED: Final = frozenset({"quantile", "le"})


class MetricError(ValueError):
    """Raised when a metric is declared, recorded, or labelled incorrectly.

    Always a programming error: the names and labels come from the application
    itself, never from a request, so this cannot be reached by anything a
    caller sends.
    """


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """One metric, declared before anything can record it.

    Declaring up front is what keeps the output a catalogue rather than
    whatever the code happened to emit: the help text and type are always
    present, a typo in a name is refused instead of quietly becoming a second
    metric, and the labels a metric carries are fixed where they can be read.
    """

    name: str
    kind: Kind
    help: str
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject a declaration the exposition format could not represent."""
        if not _NAME.match(self.name):
            raise MetricError(f"{self.name!r} is not a valid metric name")
        if len(set(self.labels)) != len(self.labels):
            raise MetricError(f"{self.name} declares a label twice")
        for label in self.labels:
            if not _LABEL.match(label):
                raise MetricError(f"{label!r} is not a valid label name")
            if label in _RESERVED:
                raise MetricError(f"{label!r} is reserved by the exposition format")


@dataclass(slots=True)
class _Window:
    """What one summary series has seen.

    `count` and `total` are lifetime figures, so a rate computed from them
    covers the whole process. `recent` is the sliding window the quantiles are
    read from, which is what makes a p99 describe now rather than an average of
    every request since startup.
    """

    capacity: int
    count: int = 0
    total: float = 0.0
    recent: deque[float] = field(default_factory=deque)

    def observe(self, value: float) -> None:
        """Record one observation in both the lifetime figures and the window."""
        self.count += 1
        self.total += value
        if len(self.recent) == self.capacity:
            self.recent.popleft()
        self.recent.append(value)


class MetricsRegistry:
    """Every metric one process holds, and the text a scraper reads them as.

    Guarded by a lock rather than assumed single-threaded: the MongoDB driver
    publishes its command events from its own machinery, and a counter that is
    occasionally wrong is worse than one that costs an uncontended lock.
    """

    def __init__(
        self,
        specs: Iterable[MetricSpec],
        *,
        window: int = DEFAULT_WINDOW,
        max_series: int = DEFAULT_MAX_SERIES,
    ) -> None:
        if window < 1:
            raise MetricError("a summary window must hold at least one observation")
        if max_series < 1:
            raise MetricError("a metric must be allowed at least one series")
        declared: dict[str, MetricSpec] = {}
        for spec in specs:
            if spec.name in declared:
                raise MetricError(f"{spec.name} is declared twice")
            declared[spec.name] = spec
        self._specs = declared
        self._window = window
        self._max_series = max_series
        self._lock = threading.Lock()
        self._values: dict[tuple[str, LabelValues], float] = {}
        self._summaries: dict[tuple[str, LabelValues], _Window] = {}
        self._dropped: dict[str, int] = {}

    @property
    def specs(self) -> tuple[MetricSpec, ...]:
        """Return every declared metric, in declaration order."""
        return tuple(self._specs.values())

    def increment(self, name: str, value: float = 1.0, /, **labels: object) -> None:
        """Add to a counter.

        A counter only rises, so a negative step is refused rather than
        recorded: it would make every rate computed from the series wrong.
        """
        if value < 0.0:
            raise MetricError(f"{name} is a counter and cannot decrease")
        key = self._key(name, "counter", labels)
        with self._lock:
            if (series := self._admit(name, key)) is None:
                return
            self._values[series] = self._values.get(series, 0.0) + value

    def set(self, name: str, value: float, /, **labels: object) -> None:
        """Record a gauge's current level, replacing whatever it was."""
        key = self._key(name, "gauge", labels)
        with self._lock:
            if (series := self._admit(name, key)) is None:
                return
            self._values[series] = value

    def observe(self, name: str, value: float, /, **labels: object) -> None:
        """Record one observation of a summary.

        A non-finite observation is dropped rather than stored: one would make
        the sum unreadable forever, and a clock that produced it has already
        failed at the thing being measured.
        """
        key = self._key(name, "summary", labels)
        if not isfinite(value):
            return
        with self._lock:
            if (series := self._admit(name, key)) is None:
                return
            window = self._summaries.get(series)
            if window is None:
                window = _Window(capacity=self._window)
                self._summaries[series] = window
            window.observe(value)

    def value(self, name: str, **labels: object) -> float | None:
        """Read one counter or gauge series, or nothing if it has none."""
        key = self._key(name, None, labels)
        with self._lock:
            return self._values.get((name, key))

    def summary(self, name: str, **labels: object) -> tuple[int, float, tuple[float, ...]] | None:
        """Read one summary series as its count, sum, and recent observations."""
        key = self._key(name, "summary", labels)
        with self._lock:
            window = self._summaries.get((name, key))
            if window is None:
                return None
            return window.count, window.total, tuple(window.recent)

    def dropped(self) -> Mapping[str, int]:
        """Report how many series each metric refused, which should be none."""
        with self._lock:
            return dict(self._dropped)

    def reset(self) -> None:
        """Forget every recorded series, keeping the declarations."""
        with self._lock:
            self._values.clear()
            self._summaries.clear()
            self._dropped.clear()

    def render(self) -> str:
        """Render every declared metric in the Prometheus text format.

        Every metric appears with its help and type even before anything has
        recorded one, so the output is the catalogue of what this build can
        report rather than only what it happened to do. A summary's quantiles
        describe the recent window while its `_count` and `_sum` cover the
        process's whole life, which is the same split the format's own
        summaries have always had.
        """
        with self._lock:
            lines: list[str] = []
            for spec in self._specs.values():
                lines.append(f"# HELP {spec.name} {_escape_help(spec.help)}")
                lines.append(f"# TYPE {spec.name} {spec.kind}")
                if spec.kind == "summary":
                    lines.extend(self._render_summary(spec))
                else:
                    lines.extend(self._render_values(spec))
            return "\n".join(lines) + "\n"

    def _render_values(self, spec: MetricSpec) -> list[str]:
        """Render one counter or gauge as a line per label combination."""
        return [
            f"{spec.name}{_render_labels(labels)} {_render_number(value)}"
            for (name, labels), value in sorted(self._values.items())
            if name == spec.name
        ]

    def _render_summary(self, spec: MetricSpec) -> list[str]:
        """Render one summary as its quantiles, its count, and its sum."""
        lines: list[str] = []
        for (name, labels), window in sorted(self._summaries.items()):
            if name != spec.name:
                continue
            ordered = sorted(window.recent)
            for quantile in QUANTILES:
                measured = _quantile(ordered, quantile)
                if measured is None:
                    continue
                marked = (*labels, ("quantile", _format_quantile(quantile)))
                lines.append(f"{spec.name}{_render_labels(marked)} {_render_number(measured)}")
            rendered = _render_labels(labels)
            lines.append(f"{spec.name}_count{rendered} {window.count}")
            lines.append(f"{spec.name}_sum{rendered} {_render_number(window.total)}")
        return lines

    def _admit(self, name: str, key: LabelValues) -> tuple[str, LabelValues] | None:
        """Allow one series, or refuse it because this metric already has enough.

        Refusing is deliberate and is counted where an operator can see it. A
        metric at its limit means the application is labelling with something it
        does not control, and losing a series says so far more usefully than
        letting the process grow until it is killed.
        """
        series = (name, key)
        if series in self._values or series in self._summaries:
            return series
        held = sum(1 for held_name, _ in (*self._values, *self._summaries) if held_name == name)
        if held >= self._max_series:
            self._dropped[name] = self._dropped.get(name, 0) + 1
            return None
        return series

    def _key(self, name: str, kind: Kind | None, labels: Mapping[str, object]) -> LabelValues:
        """Validate one recording against its declaration and order its labels."""
        spec = self._specs.get(name)
        if spec is None:
            raise MetricError(f"{name} was never declared")
        if kind is not None and spec.kind != kind:
            raise MetricError(f"{name} is a {spec.kind}, not a {kind}")
        if set(labels) != set(spec.labels):
            expected = ", ".join(spec.labels) or "no labels"
            raise MetricError(f"{name} takes {expected}")
        return tuple(sorted((label, str(labels[label])) for label in spec.labels))


def _quantile(ordered: Sequence[float], quantile: float) -> float | None:
    """Return the nearest-rank quantile of an already-sorted window.

    Nearest rank rather than interpolation: every value reported is one that was
    actually measured, so a p99 names a request that really took that long.
    """
    if not ordered:
        return None
    rank = ceil(quantile * len(ordered))
    return ordered[min(len(ordered) - 1, max(0, rank - 1))]


def _format_quantile(quantile: float) -> str:
    """Render a quantile the way the exposition format names one."""
    return f"{quantile:g}"


def _render_number(value: float) -> str:
    """Render a number without inventing precision or losing it."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def _render_labels(labels: LabelValues) -> str:
    """Render one series' labels, escaping what the format reserves."""
    if not labels:
        return ""
    rendered = ",".join(f'{name}="{_escape_value(value)}"' for name, value in labels)
    return "{" + rendered + "}"


def _escape_value(value: str) -> str:
    """Escape a label value so no value can end the label or the line."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(text: str) -> str:
    """Escape help text, which may not contain a newline."""
    return text.replace("\\", "\\\\").replace("\n", "\\n")
