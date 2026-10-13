"""The commands `contextctl` exposes, one module per group."""

import argparse

# `argparse._SubParsersAction` is generic to a type checker but not at runtime,
# so every command module annotates with this lazily-evaluated alias rather
# than subscripting the class where Python would evaluate it.
type SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]
