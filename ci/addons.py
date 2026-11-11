"""Addon graph checks for this repository: depends form no cycle, and install orders to try.

Usage: python3 ci/addons.py graph | orders
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def manifests():
    found = {}
    for name in sorted(os.listdir(ROOT)):
        path = os.path.join(ROOT, name, "__manifest__.py")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                found[name] = ast.literal_eval(handle.read())
    return found


def local_graph():
    addons = manifests()
    return {name: [dep for dep in manifest.get("depends", []) if dep in addons] for name, manifest in addons.items()}


def find_cycle(graph):
    state = {}
    stack = []

    def visit(node):
        state[node] = "open"
        stack.append(node)
        for dep in graph[node]:
            if state.get(dep) == "open":
                return stack[stack.index(dep):] + [dep]
            if dep not in state:
                cycle = visit(dep)
                if cycle:
                    return cycle
        stack.pop()
        state[node] = "done"
        return None

    for node in graph:
        if node not in state:
            cycle = visit(node)
            if cycle:
                return cycle
    return None


def topological(graph, reverse=False):
    order = []
    placed = set()
    pending = sorted(graph, reverse=reverse)
    while pending:
        ready = [name for name in pending if all(dep in placed for dep in graph[name])]
        order.append(ready[0])
        placed.add(ready[0])
        pending.remove(ready[0])
    return order


def graph_command():
    graph = local_graph()
    cycle = find_cycle(graph)
    if cycle:
        sys.exit("depends cycle: %s" % " -> ".join(cycle))
    edges = sum(len(deps) for deps in graph.values())
    print("addon graph ok: %s addons, %s local depends, order %s" % (len(graph), edges, " ".join(topological(graph))))


def orders_command():
    graph = local_graph()
    if find_cycle(graph):
        sys.exit("depends cycle, run: python3 ci/addons.py graph")
    for order in {tuple(topological(graph)), tuple(topological(graph, reverse=True))}:
        print(",".join(order))


if __name__ == "__main__":
    {"graph": graph_command, "orders": orders_command}[sys.argv[1]]()
