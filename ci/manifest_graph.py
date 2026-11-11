"""Fail when the addons of this repository depend on each other in a cycle."""
import ast
import os
import sys


def repository_graph(root):
    graph = {}
    for name in sorted(os.listdir(root)):
        manifest = os.path.join(root, name, '__manifest__.py')
        if os.path.isfile(manifest):
            with open(manifest) as handle:
                graph[name] = ast.literal_eval(handle.read()).get('depends', [])
    return {name: [dep for dep in depends if dep in graph] for name, depends in graph.items()}


def find_cycle(graph):
    state = {}
    path = []

    def visit(node):
        state[node] = 'open'
        path.append(node)
        for dep in graph[node]:
            if state.get(dep) == 'open':
                return path[path.index(dep):] + [dep]
            if dep not in state:
                cycle = visit(dep)
                if cycle:
                    return cycle
        path.pop()
        state[node] = 'done'
        return None

    for node in graph:
        if node not in state:
            cycle = visit(node)
            if cycle:
                return cycle
    return None


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    graph = repository_graph(root)
    for name, depends in graph.items():
        print('%s -> %s' % (name, ', '.join(depends) or '-'))
    cycle = find_cycle(graph)
    if cycle:
        print('dependency cycle: ' + ' -> '.join(cycle))
        sys.exit(1)
    print('no dependency cycle among %d addons' % len(graph))


if __name__ == '__main__':
    main()
