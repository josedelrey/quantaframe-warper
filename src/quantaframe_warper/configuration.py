"""Versioned project files shared by the API, CLI and desktop."""

from __future__ import annotations

import math
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


class ProjectLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key, value in node.value:
        key = loader.construct_object(key)
        if not isinstance(key, str) or key in result:
            raise ValueError(f"Invalid or duplicate project key: {key}")
        result[key] = loader.construct_object(value)
    return result


ProjectLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _keys(data, allowed):
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise ValueError(f"Expected mapping with keys: {', '.join(allowed)}")


def _integer(name, value, minimum):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _number(name, value, minimum=None, maximum=None):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be finite")
    if (
        minimum is not None
        and value < minimum
        or maximum is not None
        and value > maximum
    ):
        raise ValueError(f"{name} outside allowed range")


@dataclass(frozen=True)
class Placement:
    asset: str
    start: int = 0
    duration: int = 120
    x: float = 0
    y: float = 0
    scale: float = 1
    rotation: float = 0
    strength: float = 1

    def __post_init__(self):
        if not isinstance(self.asset, str) or not self.asset:
            raise ValueError("asset must name an imported PNG")
        _integer("start", self.start, 0)
        _integer("duration", self.duration, 1)
        for name in ("x", "y", "rotation", "strength"):
            _number(name, getattr(self, name))
        _number("scale", self.scale, 0.001, 100)
        _number("strength", self.strength, -20, 20)


@dataclass(frozen=True)
class Project:
    video: str
    assets: dict[str, str] = field(default_factory=dict)
    placements: tuple[Placement, ...] = ()
    version: int = 1

    def __post_init__(self):
        if type(self.version) is not int or self.version != 1:
            raise ValueError("Unsupported project version")
        if not isinstance(self.video, str) or not self.video:
            raise ValueError("video must be a path")
        if not isinstance(self.assets, dict) or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v
            for k, v in self.assets.items()
        ):
            raise ValueError("assets must map names to PNG paths")
        for placement in self.placements:
            if (
                not isinstance(placement, Placement)
                or placement.asset not in self.assets
            ):
                raise ValueError("Each placement must reference an imported asset")

    @classmethod
    def from_dict(cls, data, base: Path | None = None):
        _keys(data, ("version", "video", "assets", "placements"))
        data = dict(data)
        items = data.get("placements", [])
        if not isinstance(items, list):
            raise ValueError("placements must be a list")
        placements = []
        for item in items:
            _keys(item, Placement.__dataclass_fields__)
            if "asset" not in item:
                raise ValueError("Placement requires asset")
            placements.append(Placement(**item))
        data["placements"] = tuple(placements)
        if "video" not in data:
            raise ValueError("Project requires video")
        project = cls(**data)
        if base is not None:

            def resolve(p):
                return str((base / p).resolve())

            project = cls(
                resolve(project.video),
                {k: resolve(v) for k, v in project.assets.items()},
                project.placements,
                project.version,
            )
        return project

    def to_dict(self):
        data = asdict(self)
        data["placements"] = list(data["placements"])
        return data

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        return cls.from_dict(
            yaml.load(path.read_text(), Loader=ProjectLoader), path.parent
        )

    def save(self, path):
        # Absolute paths make Save As unambiguous. Handwritten relative paths load
        # relative to the YAML file, never the process working directory.
        data = self.to_dict()
        data["video"] = str(Path(self.video).resolve())
        data["assets"] = {k: str(Path(v).resolve()) for k, v in self.assets.items()}
        path = Path(path).resolve()
        if path in [Path(data["video"]), *(Path(p) for p in data["assets"].values())]:
            raise ValueError("Project file cannot overwrite an input")
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, suffix=".yaml", delete=False
        ) as file:
            temporary = Path(file.name)
            try:
                file.write(yaml.safe_dump(data, sort_keys=False))
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
