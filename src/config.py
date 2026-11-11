from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


HTTP_SCHEMES = frozenset({"http", "https"})
REPOSITORY_MODES = frozenset({"auto", "local", "remote"})
INVALID_COORDINATE_PARTS = frozenset({".", ".."})
VERSION_FORBIDDEN_CHARS = frozenset({"/", "\\"})
COORDINATE_SEPARATOR = ":"
COORDINATE_PART_COUNT = 2
POM_SUFFIX = ".pom"
URL_SCHEME_SEPARATOR = "://"
REMOTE_MODE = "remote"
LOCAL_MODE = "local"
CONFIG_ENCODING = "utf-8-sig"

PACKAGE_SECTION = "package"
REPOSITORY_SECTION = "repository"
VISUALIZATION_SECTION = "visualization"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AppConfig:
    package_name: str
    repository: str
    repository_mode: str
    resolved_repository_mode: str
    package_version: str
    graph_image: str
    config_path: Path


def _ensure_section(
    parser: configparser.ConfigParser,
    section: str,
) -> None:
    if not parser.has_section(section):
        raise ConfigError(
            f"в INI-файле отсутствует секция [{section}]."
        )


def _ensure_option(
    parser: configparser.ConfigParser,
    section: str,
    key: str,
) -> None:
    if not parser.has_option(section, key):
        raise ConfigError(
            f"в секции [{section}] отсутствует параметр {key}."
        )


def _required(
    parser: configparser.ConfigParser,
    section: str,
    key: str,
) -> str:
    _ensure_section(parser, section)
    _ensure_option(parser, section, key)
    value = parser.get(section, key, fallback="").strip()

    if not value:
        raise ConfigError(
            f"параметр [{section}] {key} не должен быть пустым."
        )

    return value


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return (
            parsed.scheme.lower() in HTTP_SCHEMES
            and bool(parsed.hostname)
        )
    except ValueError:
        return False


def _validate_coordinate_part(value: str) -> None:
    if any(char.isspace() for char in value):
        raise ConfigError(
            "package.name не должен содержать пробелы."
        )

    if value in INVALID_COORDINATE_PARTS:
        raise ConfigError(
            "package.name содержит недопустимый сегмент."
        )


def _validate_coordinates(value: str) -> str:
    parts = value.strip().split(COORDINATE_SEPARATOR)

    if len(parts) != COORDINATE_PART_COUNT or not all(parts):
        raise ConfigError(
            "package.name должен иметь Maven-формат groupId:artifactId, "
            "например com.example:demo-app."
        )

    for part in parts:
        _validate_coordinate_part(part)

    return COORDINATE_SEPARATOR.join(parts)


def _validate_package_version(value: str) -> str:
    has_whitespace = any(char.isspace() for char in value)
    has_forbidden_char = any(
        char in VERSION_FORBIDDEN_CHARS
        for char in value
    )

    if has_whitespace or has_forbidden_char:
        raise ConfigError(
            "package.version содержит недопустимые символы."
        )

    return value


def _validate_repository_mode(value: str) -> str:
    mode = value.lower()

    if mode not in REPOSITORY_MODES:
        raise ConfigError(
            "repository.mode должен быть одним из значений: "
            "auto, local, remote."
        )

    return mode


def _validate_repository_location(
    location: str,
    mode: str,
    is_url: bool,
) -> None:
    if URL_SCHEME_SEPARATOR in location and not is_url:
        raise ConfigError(
            "repository.location содержит некорректный URL; "
            "поддерживаются http:// и https:// с именем хоста."
        )

    if mode == REMOTE_MODE and not is_url:
        raise ConfigError(
            "для remote repository.location должен быть HTTP(S)-URL."
        )

    if mode == LOCAL_MODE and is_url:
        raise ConfigError(
            "для local repository.location должен быть локальным путём."
        )


def _resolve_local_repository(
    location: str,
    config_path: Path,
) -> str:
    local_path = Path(location).expanduser()

    if not local_path.is_absolute():
        local_path = config_path.parent / local_path

    local_path = local_path.resolve()

    if not local_path.exists():
        raise ConfigError(
            f"локальный репозиторий не найден: {local_path}"
        )

    if not (local_path.is_file() or local_path.is_dir()):
        raise ConfigError(
            "repository.location должен указывать на файл или каталог."
        )

    _validate_pom_file(local_path)
    return str(local_path)


def _validate_pom_file(local_path: Path) -> None:
    if local_path.is_file() and local_path.suffix.lower() != POM_SUFFIX:
        raise ConfigError(
            "локальный файл репозитория должен иметь расширение .pom."
        )


def _resolve_repository(
    parser: configparser.ConfigParser,
    config_path: Path,
) -> tuple[str, str, str]:
    location = _required(
        parser,
        REPOSITORY_SECTION,
        "location",
    )
    mode = _validate_repository_mode(
        _required(parser, REPOSITORY_SECTION, "mode")
    )
    is_url = _is_http_url(location)

    _validate_repository_location(location, mode, is_url)

    if is_url:
        return location.rstrip("/"), mode, REMOTE_MODE

    repository = _resolve_local_repository(location, config_path)
    return repository, mode, LOCAL_MODE


def _resolve_config_path(config_file: str | Path) -> Path:
    path = Path(config_file).expanduser()

    if not path.is_absolute():
        path = Path.cwd() / path

    return path.resolve()


def _validate_config_file(config_path: Path) -> None:
    if not config_path.exists():
        raise ConfigError(
            f"конфигурационный файл не найден: {config_path}"
        )

    if not config_path.is_file():
        raise ConfigError(
            f"путь конфигурации должен быть файлом: {config_path}"
        )


def _read_parser(config_path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)

    try:
        with config_path.open("r", encoding=CONFIG_ENCODING) as stream:
            parser.read_file(stream)
    except (OSError, configparser.Error) as exc:
        raise ConfigError(
            f"не удалось прочитать INI-файл: {exc}"
        ) from exc

    return parser


def load_config(config_file: str | Path) -> AppConfig:
    config_path = _resolve_config_path(config_file)
    _validate_config_file(config_path)
    parser = _read_parser(config_path)

    package_name = _validate_coordinates(
        _required(parser, PACKAGE_SECTION, "name")
    )
    package_version = _validate_package_version(
        _required(parser, PACKAGE_SECTION, "version")
    )
    repository, mode, resolved_mode = _resolve_repository(
        parser,
        config_path,
    )
    graph_image = _required(
        parser,
        VISUALIZATION_SECTION,
        "graph_image",
    )

    return AppConfig(
        package_name=package_name,
        repository=repository,
        repository_mode=mode,
        resolved_repository_mode=resolved_mode,
        package_version=package_version,
        graph_image=graph_image,
        config_path=config_path,
    )
