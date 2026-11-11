"""Print install orders for the addons of this repository: forward and reverse topological order."""
import os
import sys

from manifest_graph import find_cycle, repository_graph


def topological(graph, reverse=False):
    order = []
    placed = set()
    pending = sorted(graph, reverse=reverse)
    while pending:
        ready = next(name for name in pending if all(dep in placed for dep in graph[name]))
        order.append(ready)
        placed.add(ready)
        pending.remove(ready)
    return order


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    graph = repository_graph(root)
    cycle = find_cycle(graph)
    if cycle:
        sys.exit('dependency cycle: ' + ' -> '.join(cycle))
    for order in dict.fromkeys((tuple(topological(graph)), tuple(topological(graph, reverse=True)))):
        print(','.join(order))


if __name__ == '__main__':
    main()
