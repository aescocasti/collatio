"""
Minimal RangeSet implementation to replace ClusterShell.RangeSet.
Supports the exact API used by collatex internals.
"""


class RangeSet:
    def __init__(self):
        self._set = set()

    def add_range(self, start, stop):
        """Add integers from start to stop (exclusive)."""
        self._set.update(range(start, stop))

    def intersection(self, other):
        result = RangeSet()
        result._set = self._set & other._set
        return result

    def union_update(self, other):
        self._set |= other._set

    def difference(self, other):
        result = RangeSet()
        result._set = self._set - other._set
        return result

    def contiguous(self):
        """Return list of contiguous sub-ranges."""
        if not self._set:
            return []
        sorted_items = sorted(self._set)
        groups = []
        current = [sorted_items[0]]
        for item in sorted_items[1:]:
            if item == current[-1] + 1:
                current.append(item)
            else:
                groups.append(current)
                current = [item]
        groups.append(current)
        return [_ContiguousRange(g) for g in groups]

    def __bool__(self):
        return bool(self._set)

    def __eq__(self, other):
        if isinstance(other, RangeSet):
            return self._set == other._set
        return NotImplemented

    def __str__(self):
        if not self._set:
            return ""
        parts = []
        sorted_items = sorted(self._set)
        start = end = sorted_items[0]
        for item in sorted_items[1:]:
            if item == end + 1:
                end = item
            else:
                parts.append(str(start) if start == end else f"{start}-{end}")
                start = end = item
        parts.append(str(start) if start == end else f"{start}-{end}")
        return ",".join(parts)

    def __repr__(self):
        return f"RangeSet('{self}')"

    def __hash__(self):
        return hash(str(self))


class _ContiguousRange:
    def __init__(self, items):
        self._items = items

    def __getitem__(self, idx):
        return self._items[idx]

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __contains__(self, item):
        return item in self._items
