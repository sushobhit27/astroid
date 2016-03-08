import collections
import itertools
import pprint
import os
import unittest

import hypothesis
from hypothesis import strategies

import astroid
from astroid import nodes
from astroid.tree import base
from astroid.tree import zipper


def _all_subclasses(cls):
    return cls.__subclasses__() + [g for s in cls.__subclasses__()
                                   for g in _all_subclasses(s)]
node_types_strategy = strategies.sampled_from(_all_subclasses(base.NodeNG))

# This screens out the empty init files.
astroid_file = strategies.sampled_from(os.path.join(p, n) for p, _, ns in os.walk('astroid/') for n in ns if n.endswith('.py') and '__init__.py' not in n)

class ASTMap(dict):
    def __repr__(self):
        return '{ 1: ' + repr(self[1]) + '...}'

class AssignLabels(object):
    Node = collections.namedtuple('Node', 'node children parent')    
    def __init__(self):
        self.label = 1
    def __call__(self, labels, node, parent_label=0):
        label = self.label
        self.label += 1
        children = tuple(self(labels, c, label) for c in node)
        labels[label] = self.Node(node, children, parent_label)
        return label

Node = collections.namedtuple('Node', 'node children parent edges')
Edge = collections.namedtuple('Edge', 'label move')

AST_CACHE = {}

def ast_from_file_name(name):
    if name in AST_CACHE:
        return AST_CACHE[name]
    with open(name, 'r') as source_file:
        # print(name)
        root = astroid.parse(source_file.read())
        ast = ASTMap()
        AssignLabels()(ast, root)
        to_visit = [1]
        while to_visit:
            label = to_visit.pop()
            children = ast[label].children
            parent = ast[label].parent
            to_visit.extend(c for c in reversed(children))
            edges = []
            if children:
                edges.append(Edge(children[0], zipper.Zipper.down))
            if parent:
                edges.append(Edge(parent, zipper.Zipper.up))
                edges.append(Edge(1, zipper.Zipper.root))
                siblings = ast[parent].children
                index = siblings.index(label)
                if index > 0:
                    edges.append(Edge(siblings[0], zipper.Zipper.leftmost))
                    edges.append(Edge(siblings[index - 1], zipper.Zipper.left))
                if index < len(siblings) - 1:
                    edges.append(Edge(siblings[index + 1], zipper.Zipper.right))
                    edges.append(Edge(siblings[-1], zipper.Zipper.rightmost))
            ast[label] = Node(ast[label].node, children, parent, tuple(edges))
    AST_CACHE[name] = ast
    return ast

ast_strategy = strategies.builds(ast_from_file_name, astroid_file)

# pprint.pprint(ast_strategy.example())

def check_linked_list(linked_list):
    '''Check that this linked list of tuples is correctly formed.'''
    while linked_list:
        assert(isinstance(linked_list, tuple))
        assert(len(linked_list) == 2)
        linked_list = linked_list[1]
    assert(len(linked_list) == 0)

def check_zipper(position):
    assert(isinstance(position, (base.NodeNG, collections.Sequence)))
    assert(isinstance(position._self_path, (zipper.Path, type(None))))
    if position._self_path:
        assert(isinstance(position._self_path.parent_path, (zipper.Path, type(None))))
        check_linked_list(position._self_path.right)
        check_linked_list(position._self_path.left)
        check_linked_list(position._self_path.parent_nodes)
        assert isinstance(position._self_path.changed, bool)

def preorder_descendants(label, ast, dont_recurse_on=None):
    def _preorder_descendants(label):
        if dont_recurse_on is not None and isinstance(ast[label].node, dont_recurse_on):
            return ()
        else:
            return (label,) + sum((_preorder_descendants(l) for l in ast[label].children), ())
    return (label,) + sum((_preorder_descendants(l) for l in ast[label].children), ())

def postorder_descendants(label, ast, dont_recurse_on=None):
    def _postorder_descendants(label):
        if dont_recurse_on is not None and isinstance(ast[label].node, dont_recurse_on):
            return ()
        else:
            return sum((_postorder_descendants(l) for l in ast[label].children), ()) + (label,)
    return sum((_postorder_descendants(l) for l in ast[label].children), ()) + (label,)

def common_ancestor(label1, label2, ast):
    ancestors = set()
    while label1:
        if ast[label1].node is not nodes.Empty:
            ancestors.add(label1)
        label1 = ast[label1].parent
    # print([ast[a].node for a in ancestors])
    while label2 not in ancestors:
        # print(repr(ast[label2].node))
        label2 = ast[label2].parent
    return label2

def traverse_to_node(label, ast, location):
    moves = collections.deque()
    while label != 1:
        siblings = ast[ast[label].parent].children
        index = siblings.index(label)
        moves.extendleft(index * (zipper.Zipper.right,))
        moves.appendleft(zipper.Zipper.down)
        label = ast[label].parent
    for move in moves:
        location = move(location)
    return location


class TestZipper(unittest.TestCase):
    @hypothesis.settings(perform_health_check=False)
    @hypothesis.given(ast_strategy, strategies.integers(min_value=0, max_value=100), strategies.choices())
    def test_traversal(self, ast, length, choice):
        hypothesis.note(str(ast[1].node))
        old_label = 1
        old_zipper = zipper.Zipper(ast[1].node)
        for _ in range(length):
            new_label, move = choice(ast[old_label].edges)
            new_zipper = move(old_zipper)
            check_zipper(new_zipper)
            hypothesis.note(new_zipper)
            hypothesis.note(ast[new_label].node)
            assert(new_zipper.__wrapped__ is ast[new_label].node)
            old_zipper = new_zipper
            old_label = new_label

    @hypothesis.settings(perform_health_check=False)
    @hypothesis.given(ast_strategy, strategies.choices(), node_types_strategy)
    def test_iterators(self, ast, choice, node_type):
        nodes = tuple(ast)
        random_label = choice(nodes)
        random_node = zipper.Zipper(ast[random_label].node)
        for node, label in zip(random_node.get_children(), ast[random_label].children):
            assert(node.__wrapped__ is ast[label].node)
        for node, label in zip(random_node.preorder_descendants(), preorder_descendants(random_label, ast)):
            assert(node.__wrapped__ is ast[label].node)
        for node, label in zip(random_node.preorder_descendants(dont_recurse_on=node_type), preorder_descendants(random_label, ast, dont_recurse_on=node_type)):
            assert(node.__wrapped__ is ast[label].node)
        for node, label in zip(random_node.postorder_descendants(), postorder_descendants(random_label, ast)):
            assert(node.__wrapped__ is ast[label].node)
        for node, label in zip(random_node.postorder_descendants(dont_recurse_on=node_type), postorder_descendants(random_label, ast, dont_recurse_on=node_type)):
            assert(node.__wrapped__ is ast[label].node)

    @hypothesis.settings(perform_health_check=False)
    @hypothesis.given(ast_strategy, strategies.choices())
    def test_legacy_apis(self, ast, choice):
        root = zipper.Zipper(ast[1].node)
        nodes = tuple(ast)
        random_node = traverse_to_node(choice(nodes), ast, root)
        if random_node.up() is not None:
            if isinstance(random_node.up(), collections.Sequence) and random_node.up().up() is not None:
                assert(random_node.parent.__wrapped__ is random_node.up().up().__wrapped__)
            if isinstance(random_node.up(), base.NodeNG):
                assert(random_node.parent.__wrapped__ is random_node.up().__wrapped__)
        if random_node.right() is not None:
            assert(random_node.last_child().__wrapped__ is random_node.rightmost().__wrapped__)
            assert(random_node.next_sibling().__wrapped__ is random_node.right().__wrapped__)
        if random_node.left() is not None:
            assert(random_node.previous_sibling().__wrapped__ is random_node.left().__wrapped__)

    @hypothesis.settings(perform_health_check=False)
    @hypothesis.given(ast_strategy, ast_strategy, strategies.choices())
    def test_common_parent(self, ast1, ast2, choice):
        hypothesis.assume(ast1 is not ast2)
        root1 = zipper.Zipper(ast1[1].node)
        root2 = zipper.Zipper(ast2[1].node)
        nodes1 = tuple(ast1)[1:]
        nodes2 = tuple(ast2)[1:]
        random_label11 = choice(nodes1)
        random_label12 = choice(nodes1)
        random_label21 = choice(nodes2)
        random_label22 = choice(nodes2)
        random_node11 = traverse_to_node(random_label11, ast1, root1)
        random_node12 = traverse_to_node(random_label12, ast1, root1)
        random_node21 = traverse_to_node(random_label21, ast2, root2)
        random_node22 = traverse_to_node(random_label22, ast2, root2)
        assert(random_node11.common_ancestor(random_node12).__wrapped__ is
               ast1[common_ancestor(random_label11, random_label12, ast1)].node)
        assert(random_node21.common_ancestor(random_node22).__wrapped__ is
               ast2[common_ancestor(random_label21, random_label22, ast2)].node)
        assert(random_node11.common_ancestor(random_node22) is None)
        assert(random_node12.common_ancestor(random_node21) is None)

if __name__ == '__main__':
    unittest.main()