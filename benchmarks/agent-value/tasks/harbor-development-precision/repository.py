"""In-memory persistence adapter. Snapshots never alias a caller's objects."""

from copy import deepcopy


class Repository:
    def __init__(self, state):
        self.data = deepcopy(state)

    def snapshot(self):
        return deepcopy(self.data)

    def restore(self, state):
        self.data = deepcopy(state)
