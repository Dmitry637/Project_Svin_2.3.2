from __future__ import annotations

import argparse
import sys

from src.config import ConfigError, load_config
from src.graph import (
    DEFAULT_MAX_DEPTH,
    MIN_DEPTH,
    GraphBuilder,
    render_svg,
    resolve_output_path,
)
from src.maven import DependencyResolutionError, MavenRepository

EXIT_SUCCESS = 0
EXIT_GRAPH_ERROR = 1
EXIT_CONFIG_ERROR = 2
EXIT_INTERRUPTED = 130


def _parse_max_depth(value: str) -> int:
    try:
        depth = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "глубина должна быть целым числом."
        ) from exc

    if depth < MIN_DEPTH:
        raise argparse.ArgumentTypeError(
            "глубина не может быть отрицательной."
        )

    return depth


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Вариант №7, этап 3: построение графа прямых и "
            "транзитивных зависимостей Maven."
        )
    )
    parser.add_argument(
        "--config",
        default="config.ini",
        metavar="FILE",
        help="путь к INI-файлу (по умолчанию: config.ini)",
    )
    parser.add_argument(
        "--max-depth",
        type=_parse_max_depth,
        default=DEFAULT_MAX_DEPTH,
        metavar="N",
        help=(
            "максимальная глубина графа; 0 означает только "
            "исходный пакет"
        ),
    )
    return parser


def _print_result(
    package_name: str,
    package_version: str,
    node_count: int,
    edge_count: int,
    output_path: str,
) -> None:
    print(f"Пакет: {package_name}:{package_version}")
    print(f"Количество узлов: {node_count}")
    print(f"Количество связей: {edge_count}")
    print(f"Граф сохранён: {output_path}")


def _print_error(message: str) -> None:
    print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config(args.config)
        repository = MavenRepository(config)
        graph = GraphBuilder(
            repository,
            max_depth=args.max_depth,
        ).build(config)
        output_path = render_svg(graph, resolve_output_path(config))
    except ConfigError as exc:
        _print_error(f"Ошибка конфигурации: {exc}")
        return EXIT_CONFIG_ERROR
    except (DependencyResolutionError, OSError, ValueError) as exc:
        _print_error(f"Ошибка построения графа: {exc}")
        return EXIT_GRAPH_ERROR
    except KeyboardInterrupt:
        _print_error("\nОперация отменена пользователем.")
        return EXIT_INTERRUPTED

    _print_result(
        config.package_name,
        config.package_version,
        len(graph.nodes),
        len(graph.edges),
        str(output_path),
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
