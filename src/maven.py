from __future__ import annotations

import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from src.config import AppConfig


DEFAULT_HTTP_TIMEOUT = 15.0
MAX_PROPERTY_RESOLUTION_PASSES = 12
HTTP_NOT_FOUND_STATUS = 404
COORDINATE_SEPARATOR = ":"
COORDINATE_SPLIT_LIMIT = 1
DEFAULT_SCOPE = "compile"
REMOTE_MODE = "remote"
PROJECT_ROOT_TAG = "project"
DEFAULT_OPTIONAL = "false"
TRUE_VALUE = "true"
PROPERTY_PREFIX = "${"
UNRESOLVED_VERSION = "версия не разрешена"
POM_EXTENSION = ".pom"
USER_AGENT = "Variant7DependencyVisualizer/1.0"
URL_SAFE_CHARACTERS = ".-_~"
PROPERTY_PATTERN = re.compile(r"\$\{([^}]+)\}")


class DependencyResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Dependency:
    group_id: str
    artifact_id: str
    version: str
    scope: str = DEFAULT_SCOPE
    optional: bool = False

    @property
    def coordinates(self) -> str:
        return COORDINATE_SEPARATOR.join(
            (self.group_id, self.artifact_id, self.version)
        )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next(
        (node for node in element if _local_name(node.tag) == name),
        None,
    )


def _text(
    element: ET.Element,
    name: str,
    default: str = "",
) -> str:
    node = _child(element, name)
    return (node.text or "").strip() if node is not None else default


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [
        node for node in element
        if _local_name(node.tag) == name
    ]


def _resolve_properties(
    value: str,
    properties: dict[str, str],
) -> str:
    result = value.strip()

    for _ in range(MAX_PROPERTY_RESOLUTION_PASSES):
        updated = PROPERTY_PATTERN.sub(
            lambda match: properties.get(
                match.group(1),
                match.group(0),
            ),
            result,
        )
        if updated == result:
            break
        result = updated

    return result


def _resolve_property_map(
    properties: dict[str, str],
) -> dict[str, str]:
    for _ in range(MAX_PROPERTY_RESOLUTION_PASSES):
        changed = False

        for key, value in list(properties.items()):
            resolved = _resolve_properties(value, properties)
            if resolved != value:
                properties[key] = resolved
                changed = True

        if not changed:
            break

    return properties


def _project_properties(
    coordinates: tuple[str, str, str],
) -> dict[str, str]:
    group_id, artifact_id, version = coordinates

    return {
        "project.groupId": group_id,
        "pom.groupId": group_id,
        "project.artifactId": artifact_id,
        "pom.artifactId": artifact_id,
        "project.version": version,
        "pom.version": version,
    }


def _parent_properties(root: ET.Element) -> dict[str, str]:
    parent = _child(root, "parent")
    source = parent if parent is not None else root

    return {
        "project.parent.groupId": _text(source, "groupId"),
        "project.parent.version": _text(source, "version"),
    }


def _declared_properties(root: ET.Element) -> dict[str, str]:
    properties: dict[str, str] = {}
    props_node = _child(root, "properties")

    if props_node is None:
        return properties

    for child in props_node:
        properties[_local_name(child.tag)] = (child.text or "").strip()

    return properties


def _dependency_values(
    node: ET.Element,
    properties: dict[str, str],
) -> tuple[str, str, str]:
    group_id = _resolve_properties(
        _text(node, "groupId"),
        properties,
    )
    artifact_id = _resolve_properties(
        _text(node, "artifactId"),
        properties,
    )
    version = _resolve_properties(
        _text(node, "version"),
        properties,
    )

    return group_id, artifact_id, version


def _managed_dependency_versions(
    root: ET.Element,
    properties: dict[str, str],
) -> dict[tuple[str, str], str]:
    management = _child(root, "dependencyManagement")
    if management is None:
        return {}

    dependencies = _child(management, "dependencies")
    if dependencies is None:
        return {}

    versions: dict[tuple[str, str], str] = {}

    for node in _children(dependencies, "dependency"):
        group_id, artifact_id, version = _dependency_values(
            node,
            properties,
        )
        if _has_complete_managed_coordinates(
            group_id,
            artifact_id,
            version,
        ):
            versions[(group_id, artifact_id)] = version

    return versions


def _has_complete_managed_coordinates(
    group_id: str,
    artifact_id: str,
    version: str,
) -> bool:
    return bool(group_id and artifact_id and version) and (
        PROPERTY_PREFIX not in version
    )


def _resolve_dependency_version(
    group_id: str,
    artifact_id: str,
    version: str,
    managed_versions: dict[tuple[str, str], str],
) -> str:
    if version and PROPERTY_PREFIX not in version:
        return version

    return managed_versions.get(
        (group_id, artifact_id),
        UNRESOLVED_VERSION,
    )


def _parse_dependency(
    node: ET.Element,
    properties: dict[str, str],
    managed_versions: dict[tuple[str, str], str],
) -> Dependency:
    group_id, artifact_id, version = _dependency_values(
        node,
        properties,
    )
    _validate_dependency_coordinates(group_id, artifact_id)

    resolved_version = _resolve_dependency_version(
        group_id,
        artifact_id,
        version,
        managed_versions,
    )
    scope = _resolve_properties(
        _text(node, "scope", DEFAULT_SCOPE),
        properties,
    ) or DEFAULT_SCOPE
    optional = _resolve_properties(
        _text(node, "optional", DEFAULT_OPTIONAL),
        properties,
    )

    return Dependency(
        group_id=group_id,
        artifact_id=artifact_id,
        version=resolved_version,
        scope=scope,
        optional=optional.lower() == TRUE_VALUE,
    )


def _validate_dependency_coordinates(
    group_id: str,
    artifact_id: str,
) -> None:
    if not group_id or not artifact_id:
        raise DependencyResolutionError(
            "в POM найдена зависимость без groupId или artifactId."
        )


class MavenRepository:
    def __init__(
        self,
        config: AppConfig,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        self.config = config
        self.timeout = timeout
        self.package_group, self.package_artifact = (
            config.package_name.split(
                COORDINATE_SEPARATOR,
                maxsplit=COORDINATE_SPLIT_LIMIT,
            )
        )

    def _pom_relative_path(
        self,
        group_id: str,
        artifact_id: str,
        version: str,
    ) -> Path:
        group_path = Path(*group_id.split("."))
        pom_name = f"{artifact_id}-{version}{POM_EXTENSION}"
        return group_path / artifact_id / version / pom_name

    def _remote_pom_url(self, relative_path: Path) -> str:
        encoded_parts = (
            quote(part, safe=URL_SAFE_CHARACTERS)
            for part in relative_path.parts
        )
        relative_url = "/".join(encoded_parts)
        repository = self.config.repository.rstrip("/")
        return f"{repository}/{relative_url}"

    def _read_remote_pom(self, url: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT},
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout,
            ) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            self._raise_http_error(exc, url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DependencyResolutionError(
                f"не удалось получить POM по адресу {url}: {exc}"
            ) from exc

    @staticmethod
    def _raise_http_error(
        error: urllib.error.HTTPError,
        url: str,
    ) -> None:
        if error.code == HTTP_NOT_FOUND_STATUS:
            message = f"POM не найден (HTTP 404): {url}"
        else:
            message = (
                f"ошибка HTTP {error.code} при запросе POM: {url}"
            )

        raise DependencyResolutionError(message) from error

    def _local_pom_path(
        self,
        relative_path: Path,
        coordinates: tuple[str, str, str],
    ) -> Path:
        local_root = Path(self.config.repository)

        if local_root.is_file():
            self._validate_single_pom_target(coordinates)
            return local_root

        return local_root / relative_path

    def _validate_single_pom_target(
        self,
        coordinates: tuple[str, str, str],
    ) -> None:
        requested_coordinates = (
            self.package_group,
            self.package_artifact,
            self.config.package_version,
        )

        if coordinates != requested_coordinates:
            raise DependencyResolutionError(
                "для получения POM зависимостей нужен каталог "
                "Maven-репозитория; одиночный .pom-файл содержит "
                "только исходный пакет."
            )

    def _read_local_pom(self, pom_path: Path) -> bytes:
        try:
            return pom_path.read_bytes()
        except OSError as exc:
            raise DependencyResolutionError(
                f"POM не найден или недоступен для чтения: {pom_path}"
            ) from exc

    def _load_pom(
        self,
        group_id: str,
        artifact_id: str,
        version: str,
    ) -> bytes:
        relative_path = self._pom_relative_path(
            group_id,
            artifact_id,
            version,
        )
        coordinates = (group_id, artifact_id, version)

        if self.config.resolved_repository_mode == REMOTE_MODE:
            url = self._remote_pom_url(relative_path)
            return self._read_remote_pom(url)

        pom_path = self._local_pom_path(relative_path, coordinates)
        return self._read_local_pom(pom_path)

    @staticmethod
    def _parse_pom(data: bytes) -> ET.Element:
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise DependencyResolutionError(
                f"POM содержит некорректный XML: {exc}"
            ) from exc

        if _local_name(root.tag) != PROJECT_ROOT_TAG:
            raise DependencyResolutionError(
                "корневой XML-элемент POM должен быть project."
            )

        return root

    def _properties(
        self,
        root: ET.Element,
        coordinates: tuple[str, str, str],
    ) -> dict[str, str]:
        properties = _project_properties(coordinates)
        properties.update(_parent_properties(root))
        properties.update(_declared_properties(root))
        return _resolve_property_map(properties)

    def _managed_versions(
        self,
        root: ET.Element,
        properties: dict[str, str],
    ) -> dict[tuple[str, str], str]:
        return _managed_dependency_versions(root, properties)

    def _parse_direct_dependencies(
        self,
        root: ET.Element,
        properties: dict[str, str],
        managed_versions: dict[tuple[str, str], str],
    ) -> list[Dependency]:
        dependencies_node = _child(root, "dependencies")
        if dependencies_node is None:
            return []

        return [
            _parse_dependency(node, properties, managed_versions)
            for node in _children(dependencies_node, "dependency")
        ]

    @property
    def supports_transitive_resolution(self) -> bool:
        if self.config.resolved_repository_mode == REMOTE_MODE:
            return True
        return Path(self.config.repository).is_dir()

    def dependencies_for(
        self,
        group_id: str,
        artifact_id: str,
        version: str,
    ) -> list[Dependency]:
        coordinates = (group_id, artifact_id, version)
        data = self._load_pom(*coordinates)
        root = self._parse_pom(data)
        properties = self._properties(root, coordinates)
        managed_versions = self._managed_versions(root, properties)

        return self._parse_direct_dependencies(
            root,
            properties,
            managed_versions,
        )

    def direct_dependencies(self) -> list[Dependency]:
        return self.dependencies_for(
            self.package_group,
            self.package_artifact,
            self.config.package_version,
        )


def _project_properties(
    coordinates: tuple[str, str, str],
) -> dict[str, str]:
    group_id, artifact_id, version = coordinates

    return {
        "project.groupId": group_id,
        "pom.groupId": group_id,
        "project.artifactId": artifact_id,
        "pom.artifactId": artifact_id,
        "project.version": version,
        "pom.version": version,
    }
