from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from html import escape
from pathlib import Path

from src.config import AppConfig
from src.maven import (
    Dependency,
    DependencyResolutionError,
    MavenRepository,
    UNRESOLVED_VERSION,
)

DEFAULT_MAX_DEPTH = 4
MIN_DEPTH = 0
ROOT_DEPTH = MIN_DEPTH
EMPTY_LAYER_INDEX = MIN_DEPTH
EMPTY_LAYER_COUNT = MIN_DEPTH
DEFAULT_LAYER_NODE_COUNT = 1
COORDINATE_SPLIT_LIMIT = 1
COORDINATE_SEPARATOR = ":"
CENTER_DIVISOR = 2
EDGE_LABEL_VERTICAL_OFFSET = 8
SVG_ORIGIN = 0
ARROW_MARKER_WIDTH = 10
ARROW_MARKER_HEIGHT = 10
ARROW_REFERENCE_X = 8
ARROW_REFERENCE_Y = 3
ARROW_HEAD_HEIGHT = 6
ARROW_HEAD_WIDTH = 9
NODE_WIDTH = 250
NODE_HEIGHT = 76
HORIZONTAL_GAP = 105
VERTICAL_GAP = 30
CANVAS_MARGIN = 48
TEXT_X_OFFSET = 16
TITLE_Y_OFFSET = 29
VERSION_Y_OFFSET = 53
FONT_SIZE = 14
SMALL_FONT_SIZE = 11
STROKE_WIDTH = 2
SVG_EXTENSION = ".svg"


@dataclass(frozen=True)
class GraphNode:
    group_id: str
    artifact_id: str
    version: str
    depth: int

    @property
    def coordinates(self) -> str:
        return COORDINATE_SEPARATOR.join(
            (self.group_id, self.artifact_id, self.version)
        )


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    scope: str
    optional: bool

    @property
    def label(self) -> str:
        suffix = ", optional" if self.optional else ""
        return f"{self.scope}{suffix}"


@dataclass
class DependencyGraph:
    root: str
    nodes: dict[str, GraphNode]
    edges: list[GraphEdge]


class GraphBuilder:
    def __init__(
        self,
        repository: MavenRepository,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        if max_depth < MIN_DEPTH:
            raise ValueError("max_depth не может быть отрицательным.")
        self.repository = repository
        self.max_depth = max_depth

    def build(self, config: AppConfig) -> DependencyGraph:
        root_coordinates = self._root_coordinates(config)
        root = self._coordinates_text(root_coordinates)
        nodes = {root: self._make_node(root_coordinates, ROOT_DEPTH)}
        edges: list[GraphEdge] = []
        edge_keys: set[tuple[str, str, str, bool]] = set()
        expanded: set[tuple[str, str, str]] = set()
        queue = deque([(root_coordinates, ROOT_DEPTH)])

        while queue:
            current, depth = queue.popleft()
            if self._must_skip(current, depth, expanded):
                continue

            dependencies = self.repository.dependencies_for(*current)
            self._append_dependencies(
                current,
                depth,
                dependencies,
                nodes,
                edges,
                edge_keys,
                queue,
            )

        return DependencyGraph(root, nodes, edges)

    @staticmethod
    def _root_coordinates(
        config: AppConfig,
    ) -> tuple[str, str, str]:
        group_id, artifact_id = config.package_name.split(
            COORDINATE_SEPARATOR, COORDINATE_SPLIT_LIMIT
        )
        return group_id, artifact_id, config.package_version

    @staticmethod
    def _coordinates_text(
        coordinates: tuple[str, str, str],
    ) -> str:
        return COORDINATE_SEPARATOR.join(coordinates)

    @staticmethod
    def _make_node(
        coordinates: tuple[str, str, str],
        depth: int,
    ) -> GraphNode:
        group_id, artifact_id, version = coordinates
        return GraphNode(group_id, artifact_id, version, depth)

    def _must_skip(
        self,
        coordinates: tuple[str, str, str],
        depth: int,
        expanded: set[tuple[str, str, str]],
    ) -> bool:
        if coordinates in expanded or depth >= self.max_depth:
            return True
        if (
            depth > ROOT_DEPTH
            and not self.repository.supports_transitive_resolution
        ):
            return True
        expanded.add(coordinates)
        return False

    def _append_dependencies(
        self,
        parent: tuple[str, str, str],
        depth: int,
        dependencies: list[Dependency],
        nodes: dict[str, GraphNode],
        edges: list[GraphEdge],
        edge_keys: set[tuple[str, str, str, bool]],
        queue: deque,
    ) -> None:
        source = self._coordinates_text(parent)

        for dependency in dependencies:
            target_coordinates = self._dependency_coordinates(dependency)
            target = self._coordinates_text(target_coordinates)
            self._add_node(nodes, target, target_coordinates, depth + 1)
            self._add_edge(
                source,
                target,
                dependency,
                edges,
                edge_keys,
            )
            if dependency.version != UNRESOLVED_VERSION:
                queue.append((target_coordinates, depth + 1))

    @staticmethod
    def _dependency_coordinates(
        dependency: Dependency,
    ) -> tuple[str, str, str]:
        return (
            dependency.group_id,
            dependency.artifact_id,
            dependency.version,
        )

    def _add_node(
        self,
        nodes: dict[str, GraphNode],
        key: str,
        coordinates: tuple[str, str, str],
        depth: int,
    ) -> None:
        existing = nodes.get(key)
        if existing is None or depth < existing.depth:
            nodes[key] = self._make_node(coordinates, depth)

    @staticmethod
    def _add_edge(
        source: str,
        target: str,
        dependency: Dependency,
        edges: list[GraphEdge],
        edge_keys: set[tuple[str, str, str, bool]],
    ) -> None:
        key = (
            source,
            target,
            dependency.scope,
            dependency.optional,
        )
        if key in edge_keys:
            return
        edge_keys.add(key)
        edges.append(
            GraphEdge(
                source,
                target,
                dependency.scope,
                dependency.optional,
            )
        )


def _layer_nodes(graph: DependencyGraph) -> dict[int, list[GraphNode]]:
    layers: dict[int, list[GraphNode]] = {}

    for node in graph.nodes.values():
        layers.setdefault(node.depth, []).append(node)

    for nodes in layers.values():
        nodes.sort(key=lambda node: node.coordinates)

    return layers


def _node_positions(
    layers: dict[int, list[GraphNode]],
) -> tuple[dict[str, tuple[int, int]], int, int]:
    positions: dict[str, tuple[int, int]] = {}
    max_layer = max(layers, default=EMPTY_LAYER_INDEX)
    max_count = max(
        (len(nodes) for nodes in layers.values()),
        default=DEFAULT_LAYER_NODE_COUNT,
    )
    width = (
        CANVAS_MARGIN * 2
        + (max_layer + 1) * NODE_WIDTH
        + max_layer * HORIZONTAL_GAP
    )
    height = (
        CANVAS_MARGIN * 2
        + max_count * NODE_HEIGHT
        + max(
            max_count - DEFAULT_LAYER_NODE_COUNT,
            EMPTY_LAYER_COUNT,
        ) * VERTICAL_GAP
    )

    for depth, nodes in layers.items():
        x = CANVAS_MARGIN + depth * (NODE_WIDTH + HORIZONTAL_GAP)
        for index, node in enumerate(nodes):
            y = CANVAS_MARGIN + index * (NODE_HEIGHT + VERTICAL_GAP)
            positions[node.coordinates] = (x, y)

    return positions, width, height


def _render_edge(
    edge: GraphEdge,
    positions: dict[str, tuple[int, int]],
) -> str:
    source_x, source_y = positions[edge.source]
    target_x, target_y = positions[edge.target]
    start_x = source_x + NODE_WIDTH
    start_y = source_y + NODE_HEIGHT // CENTER_DIVISOR
    end_x = target_x
    end_y = target_y + NODE_HEIGHT // CENTER_DIVISOR
    control_x = (start_x + end_x) // CENTER_DIVISOR
    label_x = (start_x + end_x) // CENTER_DIVISOR
    label_y = (
        (start_y + end_y) // CENTER_DIVISOR
        - EDGE_LABEL_VERTICAL_OFFSET
    )
    label = escape(edge.label)

    return (
        f'<path d="M {start_x} {start_y} '
        f'C {control_x} {start_y}, {control_x} {end_y}, '
        f'{end_x} {end_y}" class="edge" />'
        f'<text x="{label_x}" y="{label_y}" '
        f'class="edge-label">{label}</text>'
    )


def _render_node(
    node: GraphNode,
    position: tuple[int, int],
    root: str,
) -> str:
    x, y = position
    title = escape(f"{node.group_id}:{node.artifact_id}")
    version = escape(f"version: {node.version}")
    node_class = "root-node" if node.coordinates == root else "node"

    return (
        f'<g class="{node_class}">'
        f'<rect x="{x}" y="{y}" width="{NODE_WIDTH}" '
        f'height="{NODE_HEIGHT}" rx="12" />'
        f'<text x="{x + TEXT_X_OFFSET}" '
        f'y="{y + TITLE_Y_OFFSET}" '
        f'class="node-title">{title}</text>'
        f'<text x="{x + TEXT_X_OFFSET}" '
        f'y="{y + VERSION_Y_OFFSET}" '
        f'class="node-version">{version}</text>'
        f'</g>'
    )


def render_svg(graph: DependencyGraph, output_path: Path) -> Path:
    layers = _layer_nodes(graph)
    positions, width, height = _node_positions(layers)
    edge_markup = "".join(
        _render_edge(edge, positions) for edge in graph.edges
    )
    node_markup = "".join(
        _render_node(node, positions[node.coordinates], graph.root)
        for node in graph.nodes.values()
    )
    document = _svg_document(
        graph,
        width,
        height,
        edge_markup,
        node_markup,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    return output_path


def _svg_document(
    graph: DependencyGraph,
    width: int,
    height: int,
    edge_markup: str,
    node_markup: str,
) -> str:
    title = escape(f"Dependency graph: {graph.root}")
    return f'''<svg xmlns="http://www.w3.org/2000/svg"
     width="{width}" height="{height}"
     viewBox="{SVG_ORIGIN} {SVG_ORIGIN} {width} {height}" role="img">
  <title>{title}</title>
  <defs>
    <marker id="arrow" markerWidth="{ARROW_MARKER_WIDTH}"
            markerHeight="{ARROW_MARKER_HEIGHT}"
            refX="{ARROW_REFERENCE_X}"
            refY="{ARROW_REFERENCE_Y}" orient="auto">
      <path d="M{SVG_ORIGIN},{SVG_ORIGIN} L{SVG_ORIGIN},{ARROW_HEAD_HEIGHT}
            L{ARROW_HEAD_WIDTH},{ARROW_REFERENCE_Y} z" fill="#64748b" />
    </marker>
  </defs>
  <style>
    .edge {{ fill: none; stroke: #64748b; stroke-width: {STROKE_WIDTH};
            marker-end: url(#arrow); }}
    .edge-label {{ fill: #475569; font: {SMALL_FONT_SIZE}px sans-serif;
                   text-anchor: middle; }}
    .node rect {{ fill: #eff6ff; stroke: #3b82f6;
                  stroke-width: {STROKE_WIDTH}; }}
    .root-node rect {{ fill: #dcfce7; stroke: #16a34a;
                       stroke-width: {STROKE_WIDTH}; }}
    .node-title {{ fill: #0f172a; font: bold {FONT_SIZE}px sans-serif; }}
    .node-version {{ fill: #475569; font: {SMALL_FONT_SIZE}px sans-serif; }}
  </style>
  <rect width="100%" height="100%" fill="#ffffff" />
  {edge_markup}
  {node_markup}
</svg>
'''


def resolve_output_path(config: AppConfig) -> Path:
    output_path = Path(config.graph_image).expanduser()
    if not output_path.is_absolute():
        output_path = config.config_path.parent / output_path
    if output_path.suffix.lower() != SVG_EXTENSION:
        raise DependencyResolutionError(
            "для визуализации третьего этапа укажите файл с расширением .svg."
        )
    return output_path.resolve()
