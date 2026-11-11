from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from src.config import AppConfig, load_config
from src.graph import GraphBuilder, render_svg, resolve_output_path
from src.maven import MavenRepository

PROJECT = Path(__file__).resolve().parents[1]


class GraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(PROJECT / "config.ini")
        self.repository = MavenRepository(self.config)

    def test_builds_direct_and_transitive_dependencies(self) -> None:
        graph = GraphBuilder(self.repository).build(self.config)

        self.assertEqual(len(graph.nodes), 4)
        self.assertEqual(len(graph.edges), 3)
        self.assertIn(
            "org.example:demo-common:1.0.0",
            graph.nodes,
        )

    def test_max_depth_one_includes_only_direct_dependencies(self) -> None:
        graph = GraphBuilder(
            self.repository,
            max_depth=1,
        ).build(self.config)

        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(len(graph.edges), 2)

    def test_zero_depth_contains_only_root_package(self) -> None:
        graph = GraphBuilder(
            self.repository,
            max_depth=0,
        ).build(self.config)

        self.assertEqual(len(graph.nodes), 1)
        self.assertEqual(graph.edges, [])

    def test_single_pom_creates_graph_without_transitive_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pom_path = root / "demo.pom"
            pom_path.write_text(
                "<project xmlns='http://maven.apache.org/POM/4.0.0'>"
                "<dependencies><dependency>"
                "<groupId>org.demo</groupId>"
                "<artifactId>child</artifactId>"
                "<version>1.2</version>"
                "</dependency></dependencies></project>",
                encoding="utf-8",
            )
            config_path = root / "config.ini"
            config_path.write_text(
                "[package]\nname = com.demo:app\nversion = 1.0\n"
                f"[repository]\nlocation = {pom_path}\nmode = local\n"
                "[visualization]\ngraph_image = graph.svg\n",
                encoding="utf-8",
            )
            config = load_config(config_path)
            repository = MavenRepository(config)
            graph = GraphBuilder(repository).build(config)

            self.assertEqual(len(graph.nodes), 2)
            self.assertEqual(len(graph.edges), 1)

    def test_graph_handles_dependency_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_pom(
                root,
                "org.demo",
                "first",
                "1.0",
                "org.demo",
                "second",
                "1.0",
            )
            self._write_pom(
                root,
                "org.demo",
                "second",
                "1.0",
                "org.demo",
                "first",
                "1.0",
            )
            config_path = root / "config.ini"
            config_path.write_text(
                "[package]\nname = org.demo:first\nversion = 1.0\n"
                "[repository]\nlocation = .\nmode = local\n"
                "[visualization]\ngraph_image = graph.svg\n",
                encoding="utf-8",
            )
            config = load_config(config_path)
            graph = GraphBuilder(
                MavenRepository(config),
                max_depth=10,
            ).build(config)

            self.assertEqual(len(graph.nodes), 2)
            self.assertEqual(len(graph.edges), 2)

    def test_svg_is_written_and_contains_all_nodes(self) -> None:
        graph = GraphBuilder(self.repository).build(self.config)

        with tempfile.TemporaryDirectory() as temp:
            output_path = Path(temp) / "graph.svg"
            result = render_svg(graph, output_path)
            content = result.read_text(encoding="utf-8")
            document = ET.fromstring(content)

            self.assertTrue(result.is_file())
            self.assertEqual(
                document.tag,
                "{http://www.w3.org/2000/svg}svg",
            )
            self.assertIn("demo-common", content)

    def test_output_path_is_relative_to_config_directory(self) -> None:
        output_path = resolve_output_path(self.config)

        self.assertEqual(
            output_path,
            (self.config.config_path.parent / "dependencies.svg").resolve(),
        )

    @staticmethod
    def _write_pom(
        root: Path,
        group_id: str,
        artifact_id: str,
        version: str,
        dependency_group: str,
        dependency_artifact: str,
        dependency_version: str,
    ) -> None:
        pom_path = (
            root
            / Path(*group_id.split("."))
            / artifact_id
            / version
            / f"{artifact_id}-{version}.pom"
        )
        pom_path.parent.mkdir(parents=True, exist_ok=True)
        pom_path.write_text(
            "<project xmlns='http://maven.apache.org/POM/4.0.0'>"
            "<dependencies><dependency>"
            f"<groupId>{dependency_group}</groupId>"
            f"<artifactId>{dependency_artifact}</artifactId>"
            f"<version>{dependency_version}</version>"
            "</dependency></dependencies></project>",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
